import logging
from collections import defaultdict
from datetime import time, timedelta
from functools import wraps

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from extensions import limiter
from gmail import send_email
from models import Employee, Payroll, TimeEntry, db
from tz import eastern_now, eastern_today
from utils import is_safe_redirect_target, make_setup_token, setup_token_matches

log = logging.getLogger(__name__)

bp = Blueprint('staff', __name__, url_prefix='/staff')


# ---------------------------------------------------------------------------
# Staff auth decorator (independent from Flask-Login admin and portal auth)
# ---------------------------------------------------------------------------

def staff_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        employee_id = session.get('portal_employee_id')
        if not employee_id:
            return redirect(url_for('staff.staff_login', next=request.url))
        employee = db.session.get(Employee, employee_id)
        if not employee or not employee.active:
            session.pop('portal_employee_id', None)
            return redirect(url_for('staff.staff_login', next=request.url))
        g.staff_employee = employee
        return f(*args, **kwargs)
    return decorated


def _get_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])


def _week_monday(ref_date):
    """Return the Monday of the week containing ref_date."""
    return ref_date - timedelta(days=ref_date.weekday())


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def staff_login():
    if session.get('portal_employee_id'):
        return redirect(url_for('staff.staff_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        employee = Employee.query.filter(
            db.func.lower(Employee.email) == email,
            Employee.active.is_(True)
        ).first()
        if employee and employee.check_portal_password(password):
            session['portal_employee_id'] = employee.id
            next_url = request.args.get('next') or request.form.get('next')
            if is_safe_redirect_target(next_url):
                return redirect(next_url)
            return redirect(url_for('staff.staff_dashboard'))
        flash('Invalid email or password.', 'danger')
    return render_template('staff_login.html')


@bp.route('/logout', methods=['POST'])
@staff_login_required
def staff_logout():
    session.pop('portal_employee_id', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('staff.staff_login'))


# ---------------------------------------------------------------------------
# Password setup (from email invite)
# ---------------------------------------------------------------------------

@bp.route('/setup-password', methods=['GET', 'POST'])
def staff_setup_password():
    token = request.args.get('token', '')
    if not token:
        flash('Invalid or missing link.', 'danger')
        return redirect(url_for('staff.staff_login'))

    s = _get_serializer()
    try:
        data = s.loads(token, max_age=86400 * 7)  # 7 days
    except SignatureExpired:
        flash('This link has expired. You can request a new one below.', 'warning')
        return redirect(url_for('staff.staff_login'))
    except BadSignature as e:
        log.error('Staff setup BadSignature: %s', e)
        flash('Invalid link. You can request a new one below.', 'danger')
        return redirect(url_for('staff.staff_login'))

    employee_id = data.get('employee_id')
    employee = db.session.get(Employee, employee_id) if employee_id else None
    if not employee:
        log.error('Staff setup: no employee with id=%s', employee_id)
        flash('No account found for this link.', 'danger')
        return redirect(url_for('staff.staff_login'))
    if not employee.active:
        log.error('Staff setup: employee id=%s is inactive', employee_id)
        flash('This account has been deactivated.', 'danger')
        return redirect(url_for('staff.staff_login'))
    if not setup_token_matches(data, employee):
        log.warning('Staff setup: stale token for employee id=%s', employee_id)
        flash('This link is no longer valid. You can request a new one below.', 'warning')
        return redirect(url_for('staff.staff_login'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('staff_setup_password.html', token=token, employee=employee)
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('staff_setup_password.html', token=token, employee=employee)

        employee.set_portal_password(password)
        employee.portal_invite_token = None
        employee.portal_invite_sent_at = None
        db.session.commit()

        session['portal_employee_id'] = employee.id
        flash('Password set successfully! Welcome to your portal.', 'success')
        return redirect(url_for('staff.staff_dashboard'))

    return render_template('staff_setup_password.html', token=token, employee=employee)


# ---------------------------------------------------------------------------
# Self-service "Email me a login link"
# ---------------------------------------------------------------------------

@bp.route('/request-link', methods=['POST'])
@limiter.limit("3 per minute")
def staff_request_link():
    email = request.form.get('email', '').strip().lower()
    flash('If an account exists for that email, we\'ll send a link.', 'info')

    if email:
        employee = Employee.query.filter(
            db.func.lower(Employee.email) == email,
            Employee.active.is_(True)
        ).first()
        if not employee:
            log.info('Staff link requested for %s — no active employee found', email)
        else:
            token = make_setup_token(current_app.config['SECRET_KEY'], 'employee_id', employee)
            setup_url = url_for('staff.staff_setup_password', token=token, _external=True)
            try:
                html_body = render_template('email_employee_invite.html',
                                            employee=employee, setup_url=setup_url,
                                            login_url=url_for('staff.staff_login', _external=True))
                if send_email(subject='Your Clips Employee Portal Access Link',
                              recipients=[employee.email], html=html_body):
                    log.info('Staff link emailed to %s', employee.email)
                else:
                    log.error('Staff request-link email failed for %s', employee.email)
            except Exception as e:
                log.error('Staff request-link email failed for %s: %s', employee.email, e)

    return redirect(url_for('staff.staff_login'))


# ---------------------------------------------------------------------------
# Staff dashboard — hours & earnings overview
# ---------------------------------------------------------------------------

@bp.route('/')
@staff_login_required
def staff_dashboard():
    employee = g.staff_employee
    today = eastern_today()

    # --- Stat cards ---
    week_start = _week_monday(today)
    week_end = week_start + timedelta(days=6)
    week_entries = TimeEntry.query.filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.work_date >= week_start,
        TimeEntry.work_date <= week_end,
    ).all()
    week_hours = sum(e.hours for e in week_entries)
    week_pay = sum(e.total_payment for e in week_entries)

    month_start = today.replace(day=1)
    month_entries = TimeEntry.query.filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.work_date >= month_start,
        TimeEntry.work_date <= today,
    ).all()
    month_hours = sum(e.hours for e in month_entries)
    month_pay = sum(e.total_payment for e in month_entries)

    year_start = today.replace(month=1, day=1)
    ytd_entries = TimeEntry.query.filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.work_date >= year_start,
        TimeEntry.work_date <= today,
    ).all()
    ytd_hours = sum(e.hours for e in ytd_entries)
    ytd_pay = sum(e.total_payment for e in ytd_entries)

    # Merge legacy payroll records (exclude rows linked to time_entries to avoid double-counting)
    linked_payroll_ids = db.session.query(TimeEntry.payroll_id).filter(
        TimeEntry.payroll_id.isnot(None)
    ).subquery()

    def _legacy_payroll(start_date, end_date):
        rows = Payroll.query.filter(
            Payroll.employee_id == employee.id,
            Payroll.work_date >= start_date,
            Payroll.work_date <= end_date,
            ~Payroll.id.in_(linked_payroll_ids),
        ).all()
        return sum(r.hours for r in rows), sum(r.total_payment for r in rows)

    leg_h, leg_p = _legacy_payroll(week_start, week_end)
    week_hours += leg_h
    week_pay += leg_p

    leg_h, leg_p = _legacy_payroll(month_start, today)
    month_hours += leg_h
    month_pay += leg_p

    leg_h, leg_p = _legacy_payroll(year_start, today)
    ytd_hours += leg_h
    ytd_pay += leg_p

    # --- Recent weeks (last 6 weeks) ---
    six_weeks_ago = _week_monday(today - timedelta(weeks=5))
    recent_entries = TimeEntry.query.filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.work_date >= six_weeks_ago,
        TimeEntry.work_date <= week_end,
    ).order_by(TimeEntry.work_date).all()

    weeks_map = defaultdict(list)
    for e in recent_entries:
        weeks_map[_week_monday(e.work_date)].append(e)

    recent_weeks = []
    for wk in sorted(weeks_map.keys(), reverse=True):
        entries = weeks_map[wk]
        statuses = set(e.status for e in entries)
        if statuses == {'approved'}:
            week_status = 'approved'
        elif 'submitted' in statuses:
            week_status = 'submitted'
        elif 'rejected' in statuses:
            week_status = 'rejected'
        else:
            week_status = 'draft'
        recent_weeks.append({
            'week_start': wk,
            'week_end': wk + timedelta(days=6),
            'hours': sum(e.hours for e in entries),
            'pay': sum(e.total_payment for e in entries),
            'status': week_status,
            'days_worked': sum(1 for e in entries if e.hours > 0),
        })

    return render_template('staff_home.html',
                           employee=employee,
                           week_hours=week_hours, week_pay=week_pay,
                           month_hours=month_hours, month_pay=month_pay,
                           ytd_hours=ytd_hours, ytd_pay=ytd_pay,
                           recent_weeks=recent_weeks,
                           current_week=week_start)


# ---------------------------------------------------------------------------
# Timesheet view
# ---------------------------------------------------------------------------

@bp.route('/timesheet')
@staff_login_required
def staff_timesheet():
    employee = g.staff_employee
    today = eastern_today()

    # Determine which week to display
    week_param = request.args.get('week', '')
    if week_param:
        try:
            from datetime import date
            ref_date = date.fromisoformat(week_param)
        except ValueError:
            ref_date = today
    else:
        ref_date = today

    week_start = _week_monday(ref_date)
    week_end = week_start + timedelta(days=6)
    current_week_start = _week_monday(today)

    # Load existing entries for this week
    entries = TimeEntry.query.filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.work_date >= week_start,
        TimeEntry.work_date <= week_end,
    ).all()
    entry_map = {e.work_date: e for e in entries}

    # Build the 7-day data
    days = []
    for i in range(7):
        day_date = week_start + timedelta(days=i)
        entry = entry_map.get(day_date)
        days.append({
            'date': day_date,
            'date_iso': day_date.isoformat(),
            'day_name': day_date.strftime('%A'),
            'start_time': entry.start_time if entry else None,
            'end_time': entry.end_time if entry else None,
            'hours': entry.hours if entry else 0.0,
            'rate': entry.hourly_rate if entry else employee.default_hourly_rate,
            'pay': entry.total_payment if entry else 0.0,
            'status': entry.status if entry else None,
            'has_entry': entry is not None,
            'notes': entry.notes if entry else '',
        })

    total_hours = sum(d['hours'] for d in days)
    total_pay = sum(d['pay'] for d in days)
    show_next = week_start < current_week_start
    prev_week = (week_start - timedelta(days=7)).isoformat()
    next_week = (week_start + timedelta(days=7)).isoformat()

    return render_template('staff_dashboard.html',
                           employee=employee,
                           days=days,
                           week_start=week_start,
                           week_end=week_end,
                           total_hours=total_hours,
                           total_pay=total_pay,
                           show_next=show_next,
                           prev_week=prev_week,
                           next_week=next_week,
                           rate_warning=employee.default_hourly_rate == 0)


# ---------------------------------------------------------------------------
# Submit (single save+submit action)
# ---------------------------------------------------------------------------

@bp.route('/timesheet/submit', methods=['POST'])
@staff_login_required
def staff_timesheet_submit():
    employee = g.staff_employee
    week_start_str = request.form.get('week_start', '')
    try:
        from datetime import date
        week_start = date.fromisoformat(week_start_str)
    except ValueError:
        flash('Invalid week.', 'danger')
        return redirect(url_for('staff.staff_timesheet'))

    existing = TimeEntry.query.filter(
        TimeEntry.employee_id == employee.id,
        TimeEntry.work_date >= week_start,
        TimeEntry.work_date <= week_start + timedelta(days=6),
    ).all()
    entry_map = {e.work_date: e for e in existing}

    now = eastern_now()
    submitted_count = 0

    for i in range(7):
        day_date = week_start + timedelta(days=i)
        start_str = request.form.get(f'start_time_{i}', '').strip()
        end_str = request.form.get(f'end_time_{i}', '').strip()
        notes_str = request.form.get(f'notes_{i}', '').strip()

        # Parse start/end times
        start_t = None
        end_t = None
        hours = 0.0
        if start_str and end_str:
            try:
                start_t = time.fromisoformat(start_str)
                end_t = time.fromisoformat(end_str)
            except ValueError:
                start_t = None
                end_t = None
            if start_t and end_t:
                start_mins = start_t.hour * 60 + start_t.minute
                end_mins = end_t.hour * 60 + end_t.minute
                diff = end_mins - start_mins
                if diff <= 0:
                    flash(f'End time must be after start time on {day_date.strftime("%A")}.', 'danger')
                    return redirect(url_for('staff.staff_timesheet', week=week_start.isoformat()))
                # Round to nearest 15 minutes
                rounded = round(diff / 15) * 15
                hours = rounded / 60
        elif start_str or end_str:
            # Only one time provided — skip this day
            flash(f'Both start and end times are required on {day_date.strftime("%A")}.', 'danger')
            return redirect(url_for('staff.staff_timesheet', week=week_start.isoformat()))

        hours = max(0.0, min(24.0, hours))

        entry = entry_map.get(day_date)
        rate = employee.default_hourly_rate

        if entry:
            if entry.status == 'approved' and abs(entry.hours - hours) > 0.001:
                # Hours changed on an approved entry — delete linked payroll, revert to submitted
                if entry.payroll_id:
                    payroll_row = db.session.get(Payroll, entry.payroll_id)
                    if payroll_row:
                        db.session.delete(payroll_row)
                    entry.payroll_id = None
                mod_note = f"[Modified after approval: was {entry.hours}h]"
                entry.notes = f"{mod_note} {notes_str}".strip()
                entry.start_time = start_t
                entry.end_time = end_t
                entry.hours = hours
                entry.hourly_rate = rate
                entry.total_payment = round(hours * rate, 2)
                entry.status = 'submitted'
                entry.submitted_at = now
                entry.approved_at = None
                submitted_count += 1
            elif entry.status == 'approved':
                # Only notes changed — keep approved status
                entry.notes = notes_str
            else:
                # draft, submitted, or rejected — update freely, set to submitted
                entry.start_time = start_t
                entry.end_time = end_t
                entry.hours = hours
                entry.hourly_rate = rate
                entry.total_payment = round(hours * rate, 2)
                entry.notes = notes_str
                entry.status = 'submitted'
                entry.submitted_at = now
                submitted_count += 1
        else:
            if hours > 0:
                entry = TimeEntry(
                    employee_id=employee.id,
                    work_date=day_date,
                    start_time=start_t,
                    end_time=end_t,
                    hours=hours,
                    hourly_rate=rate,
                    total_payment=round(hours * rate, 2),
                    status='submitted',
                    submitted_at=now,
                    notes=notes_str,
                )
                db.session.add(entry)
                submitted_count += 1

    if submitted_count == 0:
        flash('No hours to submit. Enter your hours first.', 'warning')
    else:
        db.session.commit()
        if employee.default_hourly_rate == 0:
            flash(f'Timesheet submitted ({submitted_count} day{"s" if submitted_count != 1 else ""}). Note: your hourly rate is $0 — contact your admin.', 'warning')
        else:
            flash(f'Timesheet submitted ({submitted_count} day{"s" if submitted_count != 1 else ""}).', 'success')

    return redirect(url_for('staff.staff_timesheet', week=week_start.isoformat()))


# ---------------------------------------------------------------------------
# Clear a single day (AJAX)
# ---------------------------------------------------------------------------

@bp.route('/timesheet/clear-day', methods=['POST'])
@staff_login_required
def staff_timesheet_clear_day():
    employee = g.staff_employee
    data = request.get_json(silent=True) or {}
    date_str = data.get('date', '')
    try:
        from datetime import date
        work_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return jsonify(success=False, error='Invalid date.'), 400

    entry = TimeEntry.query.filter_by(
        employee_id=employee.id, work_date=work_date
    ).first()

    if not entry:
        return jsonify(success=True, deleted=False)

    if entry.status == 'approved':
        return jsonify(success=False, error='Cannot clear an approved entry.'), 403

    db.session.delete(entry)
    db.session.commit()
    return jsonify(success=True, deleted=True)
