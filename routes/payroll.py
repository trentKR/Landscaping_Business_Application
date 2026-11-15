import logging
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required

from gmail import send_email
from models import Employee, Payroll, TimeEntry, db
from services import apply_date_filters
from tz import eastern_now, eastern_today
from utils import MAX_HOURS, make_setup_token, parse_amount

log = logging.getLogger(__name__)

bp = Blueprint('payroll', __name__)


# ---------------------------------------------------------------------------
# Payroll routes
# ---------------------------------------------------------------------------

@bp.route('/payroll')
@login_required
def payroll():
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Payroll.query
    query, date_from, date_to = apply_date_filters(query, Payroll.work_date, date_from, date_to)

    entries = query.order_by(Payroll.work_date.desc()).limit(200).all()
    total = sum(e.total_payment for e in entries)
    return render_template('payroll.html', entries=entries,
                           date_from=date_from, date_to=date_to,
                           total=total)


@bp.route('/payroll/quick-add', methods=['GET', 'POST'])
@login_required
def payroll_quick_add():
    emps = Employee.query.filter_by(active=True).order_by(Employee.last_name, Employee.first_name).all()
    today = eastern_today()

    if request.method == 'GET':
        return render_template('payroll_quick_add.html',
                               employees=emps, today=today,
                               blank_rows=1, max_rows=200)

    # POST — parse grid rows
    indices = request.form.getlist('row_index')
    entries = []
    errors = []

    for idx in indices:
        emp_id = request.form.get(f'employee_id_{idx}', '').strip()
        hours_raw = request.form.get(f'hours_{idx}', '').strip()
        rate_raw = request.form.get(f'hourly_rate_{idx}', '').strip()

        # Blank-row filter: skip if all meaningful fields empty
        if not emp_id and not hours_raw and not rate_raw:
            continue

        work_date_raw = request.form.get(f'work_date_{idx}', '').strip()
        notes = request.form.get(f'notes_{idx}', '').strip()

        # Validate
        try:
            work_date_val = date.fromisoformat(work_date_raw)
        except (ValueError, TypeError):
            errors.append(f'Row {len(entries)+1}: invalid date.')
            continue

        emp = db.session.get(Employee, int(emp_id)) if emp_id else None
        if not emp:
            errors.append(f'Row {len(entries)+1}: employee required.')
            continue

        try:
            hours = parse_amount(hours_raw, maximum=MAX_HOURS)
            if hours <= 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(f'Row {len(entries)+1}: hours must be > 0.')
            continue

        try:
            hourly_rate = parse_amount(rate_raw)
            if hourly_rate <= 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(f'Row {len(entries)+1}: rate must be > $0.')
            continue

        entries.append(Payroll(
            employee_name=emp.full_name,
            employee_id=emp.id,
            work_date=work_date_val,
            hours=hours,
            hourly_rate=hourly_rate,
            total_payment=round(hours * hourly_rate, 2),
            notes=notes,
        ))

    if errors:
        for msg in errors[:5]:
            flash(msg, 'danger')
        return redirect(url_for('payroll.payroll_quick_add'))

    if not entries:
        flash('Enter at least one entry before saving.', 'danger')
        return redirect(url_for('payroll.payroll_quick_add'))

    for entry in entries:
        db.session.add(entry)
    db.session.commit()

    flash(f'{len(entries)} payroll entr{"y" if len(entries) == 1 else "ies"} recorded.', 'success')
    return redirect(url_for('payroll.payroll'))


@bp.route('/payroll/new', methods=['GET', 'POST'])
@login_required
def payroll_new():
    if request.method == 'POST':
        try:
            hours = parse_amount(request.form['hours'], maximum=MAX_HOURS)
            hourly_rate = parse_amount(request.form['hourly_rate'])
            work_date_val = date.fromisoformat(request.form['work_date'])
            if hours <= 0 or hourly_rate <= 0:
                raise ValueError
        except (ValueError, KeyError):
            flash('Please fill in all required fields with valid values.', 'danger')
            return redirect(url_for('payroll.payroll_new'))
        entry = Payroll(
            employee_name=request.form['employee_name'].strip(),
            work_date=work_date_val,
            hours=hours,
            hourly_rate=hourly_rate,
            total_payment=round(hours * hourly_rate, 2),
            notes=request.form.get('notes', '').strip(),
        )
        db.session.add(entry)
        db.session.commit()
        flash('Payroll entry recorded.', 'success')
        return redirect(url_for('payroll.payroll'))
    emps = Employee.query.filter_by(active=True).order_by(Employee.last_name, Employee.first_name).all()
    return render_template('payroll_form.html', entry=None, today=eastern_today(), employees=emps)


@bp.route('/payroll/<int:entry_id>/edit', methods=['GET', 'POST'])
@login_required
def payroll_edit(entry_id):
    entry = db.session.get(Payroll, entry_id) or abort(404)
    if request.method == 'POST':
        try:
            hours = parse_amount(request.form['hours'], maximum=MAX_HOURS)
            hourly_rate = parse_amount(request.form['hourly_rate'])
            work_date_val = date.fromisoformat(request.form['work_date'])
            if hours <= 0 or hourly_rate <= 0:
                raise ValueError
        except (ValueError, KeyError):
            flash('Please fill in all required fields with valid values.', 'danger')
            return redirect(url_for('payroll.payroll_edit', entry_id=entry_id))
        entry.employee_name = request.form['employee_name'].strip()
        entry.work_date = work_date_val
        entry.hours = hours
        entry.hourly_rate = hourly_rate
        entry.total_payment = round(hours * hourly_rate, 2)
        entry.notes = request.form.get('notes', '').strip()
        db.session.commit()
        flash('Payroll entry updated.', 'success')
        return redirect(url_for('payroll.payroll'))
    emps = Employee.query.filter_by(active=True).order_by(Employee.last_name, Employee.first_name).all()
    return render_template('payroll_form.html', entry=entry, today=eastern_today(), employees=emps)


@bp.route('/payroll/<int:entry_id>/delete', methods=['POST'])
@login_required
def payroll_delete(entry_id):
    entry = db.session.get(Payroll, entry_id) or abort(404)
    db.session.delete(entry)
    db.session.commit()
    flash('Payroll entry deleted.', 'warning')
    return redirect(url_for('payroll.payroll'))


# ---------------------------------------------------------------------------
# Employee routes
# ---------------------------------------------------------------------------

@bp.route('/employees')
@login_required
def employees():
    search = request.args.get('search', '').strip()
    query = Employee.query.filter_by(active=True)
    if search:
        query = query.filter(
            db.or_(Employee.first_name.ilike(f'%{search}%'), Employee.last_name.ilike(f'%{search}%'))
        )
    emps = query.order_by(Employee.last_name, Employee.first_name).all()
    return render_template('employees.html', employees=emps, search=search)


@bp.route('/employees/new', methods=['GET', 'POST'])
@login_required
def employee_new():
    if request.method == 'POST':
        try:
            default_hourly_rate = parse_amount(request.form.get('default_hourly_rate') or 0)
            if default_hourly_rate < 0:
                raise ValueError
        except ValueError:
            flash('Hourly rate must be a valid number.', 'danger')
            return redirect(url_for('payroll.employee_new'))
        email = request.form.get('email', '').strip().lower()
        if email:
            existing = Employee.query.filter(
                db.func.lower(Employee.email) == email,
                Employee.active.is_(True)
            ).first()
            if existing:
                flash('Another active employee already has that email.', 'danger')
                return redirect(url_for('payroll.employee_new'))
        emp = Employee(
            first_name=request.form['first_name'].strip(),
            last_name=request.form['last_name'].strip(),
            default_hourly_rate=default_hourly_rate,
            email=email,
        )
        db.session.add(emp)
        db.session.commit()
        flash(f'Employee "{emp.full_name}" added.', 'success')
        return redirect(url_for('payroll.employees'))
    return render_template('employee_form.html', employee=None)


@bp.route('/employees/<int:employee_id>/edit', methods=['GET', 'POST'])
@login_required
def employee_edit(employee_id):
    emp = db.session.get(Employee, employee_id) or abort(404)
    if request.method == 'POST':
        try:
            default_hourly_rate = parse_amount(request.form.get('default_hourly_rate') or 0)
            if default_hourly_rate < 0:
                raise ValueError
        except ValueError:
            flash('Hourly rate must be a valid number.', 'danger')
            return redirect(url_for('payroll.employee_edit', employee_id=employee_id))
        email = request.form.get('email', '').strip().lower()
        if email:
            existing = Employee.query.filter(
                db.func.lower(Employee.email) == email,
                Employee.active.is_(True),
                Employee.id != emp.id
            ).first()
            if existing:
                flash('Another active employee already has that email.', 'danger')
                return redirect(url_for('payroll.employee_edit', employee_id=employee_id))
        emp.first_name = request.form['first_name'].strip()
        emp.last_name = request.form['last_name'].strip()
        emp.default_hourly_rate = default_hourly_rate
        emp.email = email
        emp.active = 'active' in request.form
        db.session.commit()
        flash(f'Employee "{emp.full_name}" updated.', 'success')
        return redirect(url_for('payroll.employees'))
    return render_template('employee_form.html', employee=emp)


@bp.route('/employees/<int:employee_id>/delete', methods=['POST'])
@login_required
def employee_delete(employee_id):
    emp = db.session.get(Employee, employee_id) or abort(404)
    emp.active = False
    db.session.commit()
    flash(f'Employee "{emp.full_name}" deactivated.', 'warning')
    return redirect(url_for('payroll.employees'))


@bp.route('/employees/<int:employee_id>/portal-invite', methods=['POST'])
@login_required
def employee_portal_invite(employee_id):
    emp = db.session.get(Employee, employee_id) or abort(404)
    if not emp.email:
        flash('Employee must have an email address before sending an invite.', 'danger')
        return redirect(url_for('payroll.employee_edit', employee_id=employee_id))

    token = make_setup_token(current_app.config['SECRET_KEY'], 'employee_id', emp)
    emp.portal_invite_token = token
    emp.portal_invite_sent_at = eastern_now()
    db.session.commit()

    setup_url = url_for('staff.staff_setup_password', token=token, _external=True)
    try:
        html_body = render_template('email_employee_invite.html',
                                    employee=emp, setup_url=setup_url,
                                    login_url=url_for('staff.staff_login', _external=True))
        if send_email(subject='Your Clips Employee Portal Invite',
                      recipients=[emp.email], html=html_body):
            flash(f'Portal invite sent to {emp.email}.', 'success')
        else:
            flash('Invite created but email failed to send. Share this link manually.', 'warning')
    except Exception as e:
        log.error('Employee invite email failed for %s: %s', emp.email, e)
        flash('Invite created but email failed to send. Share this link manually.', 'warning')

    return redirect(url_for('payroll.employee_edit', employee_id=employee_id))


# ---------------------------------------------------------------------------
# Timesheet Admin routes
# ---------------------------------------------------------------------------

def _week_monday(ref_date):
    return ref_date - timedelta(days=ref_date.weekday())


@bp.route('/timesheets')
@login_required
def timesheets():
    status_filter = request.args.get('status', 'submitted')

    query = TimeEntry.query.join(Employee).filter(Employee.active.is_(True))
    if status_filter and status_filter != 'all':
        query = query.filter(TimeEntry.status == status_filter)

    entries = query.order_by(TimeEntry.work_date.desc()).all()

    # Group by (employee_id, week_monday)
    groups = {}
    for entry in entries:
        week_start = _week_monday(entry.work_date)
        key = (entry.employee_id, week_start)
        if key not in groups:
            groups[key] = {
                'employee': entry.employee,
                'week_start': week_start,
                'week_end': week_start + timedelta(days=6),
                'entries': [],
                'total_hours': 0.0,
                'total_pay': 0.0,
            }
        groups[key]['entries'].append(entry)
        groups[key]['total_hours'] += entry.hours
        groups[key]['total_pay'] += entry.total_payment

    # Sort groups by week_start desc, then employee name
    sorted_groups = sorted(groups.values(),
                           key=lambda g: (-g['week_start'].toordinal(), g['employee'].full_name))

    return render_template('timesheets.html',
                           groups=sorted_groups,
                           status_filter=status_filter)


@bp.route('/timesheets/approve', methods=['POST'])
@login_required
def timesheets_approve():
    entry_ids = request.form.getlist('entry_ids')
    if not entry_ids:
        flash('No entries selected.', 'warning')
        return redirect(url_for('payroll.timesheets'))

    now = eastern_now()
    count = 0
    for entry_id in entry_ids:
        entry = db.session.get(TimeEntry, int(entry_id))
        if not entry or entry.status != 'submitted':
            continue
        entry.status = 'approved'
        entry.approved_at = now
        # Create payroll row
        payroll_entry = Payroll(
            employee_name=entry.employee.full_name,
            work_date=entry.work_date,
            hours=entry.hours,
            hourly_rate=entry.hourly_rate,
            total_payment=entry.total_payment,
            notes=f'Auto-created from timesheet entry #{entry.id}',
        )
        db.session.add(payroll_entry)
        db.session.flush()
        entry.payroll_id = payroll_entry.id
        count += 1

    db.session.commit()
    flash(f'Approved {count} entr{"y" if count == 1 else "ies"} and created payroll records.', 'success')
    return redirect(url_for('payroll.timesheets'))


@bp.route('/timesheets/reject', methods=['POST'])
@login_required
def timesheets_reject():
    entry_ids = request.form.getlist('entry_ids')
    if not entry_ids:
        flash('No entries selected.', 'warning')
        return redirect(url_for('payroll.timesheets'))

    count = 0
    for entry_id in entry_ids:
        entry = db.session.get(TimeEntry, int(entry_id))
        if not entry or entry.status != 'submitted':
            continue
        entry.status = 'rejected'
        entry.submitted_at = None
        count += 1

    db.session.commit()
    flash(f'Rejected {count} entr{"y" if count == 1 else "ies"}. Employee can now edit and resubmit.', 'warning')
    return redirect(url_for('payroll.timesheets'))
