from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFError
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import limiter
from models import Client, Expense, ExpenseCategory, Invoice, InvoiceStatus, PaymentApplication, Service, User, db
from services import get_available_years, get_category_breakdown, get_monthly_financials, get_service_breakdown
from tz import EASTERN, eastern_today
from utils import is_safe_redirect_target

bp = Blueprint('core', __name__)


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------

@bp.app_template_filter('currency')
def currency_filter(value):
    try:
        return f'${float(value):,.2f}'
    except (TypeError, ValueError):
        return '$0.00'


@bp.app_template_filter('eastern')
def eastern_filter(value):
    """Convert a datetime to Eastern time for display."""
    if value is None:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=EASTERN)
    else:
        value = value.astimezone(EASTERN)
    return value.strftime('%B %d, %Y %I:%M %p')


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@bp.app_errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404,
                           message='Page not found.'), 404


@bp.app_errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500,
                           message='Something went wrong.'), 500


@bp.app_errorhandler(429)
def rate_limited(e):
    # Bouncing a prospective customer to an error page loses the lead; send
    # them back to the form with the phone number instead.
    if request.path == url_for('landing.request_quote'):
        flash('That was a lot of requests at once. Please wait a few minutes '
              'and try again, or call (330) 620-3465.', 'warning')
        return redirect(url_for('landing.home', _anchor='quote'))
    return render_template('error.html', code=429,
                           message='Too many requests. Please wait a few minutes '
                                   'and try again.'), 429


@bp.app_errorhandler(413)
def payload_too_large(e):
    if request.path == url_for('landing.request_quote'):
        flash('That was too much text for one message. Please shorten it '
              'and send it again.', 'warning')
        return redirect(url_for('landing.home', _anchor='quote'))
    return render_template('error.html', code=413,
                           message='That upload was too large.'), 413


@bp.app_errorhandler(CSRFError)
def csrf_error(e):
    # The public quote form is the one CSRF-protected form a signed-out
    # stranger can hit; bouncing them to the admin login is the wrong door.
    if request.path == url_for('landing.request_quote'):
        flash('That form expired. Please send it again.', 'warning')
        return redirect(url_for('landing.home', _anchor='quote'))
    flash('Your session expired. Please try again.', 'warning')
    return redirect(url_for('core.login'))


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for('core.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember='remember_me' in request.form)
            next_page = request.args.get('next')
            if not is_safe_redirect_target(next_page):
                next_page = None
            return redirect(next_page or url_for('core.dashboard'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('core.login'))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@bp.route('/dashboard')
@login_required
def dashboard():
    today = eastern_today()
    start_of_week = today - timedelta(days=today.weekday())
    start_of_month = today.replace(day=1)

    # Revenue this week
    weekly_revenue = db.session.query(func.sum(Service.amount)).filter(
        Service.service_date >= start_of_week
    ).scalar() or 0.0

    # Revenue this month
    monthly_revenue = db.session.query(func.sum(Service.amount)).filter(
        Service.service_date >= start_of_month
    ).scalar() or 0.0

    # Payroll this month
    from models import Payroll
    monthly_payroll = db.session.query(func.sum(Payroll.total_payment)).filter(
        Payroll.work_date >= start_of_month
    ).scalar() or 0.0

    # Expenses this month (excluding refunds, adding refunds back)
    monthly_expenses = db.session.query(func.sum(Expense.amount)).filter(
        Expense.expense_date >= start_of_month,
        Expense.is_refund.is_(False)
    ).scalar() or 0.0
    monthly_refunds = db.session.query(func.sum(Expense.amount)).filter(
        Expense.expense_date >= start_of_month,
        Expense.is_refund.is_(True)
    ).scalar() or 0.0
    monthly_expenses_net = monthly_expenses - monthly_refunds

    # Top expense categories this month
    top_categories = db.session.query(
        ExpenseCategory.name,
        func.sum(Expense.amount)
    ).join(Expense).filter(
        Expense.expense_date >= start_of_month,
        Expense.is_refund.is_(False)
    ).group_by(ExpenseCategory.name).order_by(
        func.sum(Expense.amount).desc()
    ).limit(5).all()

    # Client counts
    total_clients = Client.query.filter_by(active=True).count()
    weekly_clients = Client.query.filter_by(active=True, is_weekly=True).count()

    # Services this week
    services_this_week = Service.query.filter(
        Service.service_date >= start_of_week
    ).count()

    # Recent services (eager-load client to avoid N+1 in template)
    recent_services = Service.query.options(
        joinedload(Service.client)
    ).order_by(Service.service_date.desc()).limit(10).all()

    _outstanding = [InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL]

    # AR summary
    total_ar = db.session.query(func.sum(Invoice.subtotal)).filter(
        Invoice.status.in_(_outstanding)
    ).scalar() or 0.0
    total_payments_applied = db.session.query(
        func.sum(PaymentApplication.amount_applied)
    ).join(Invoice).filter(
        Invoice.status.in_(_outstanding)
    ).scalar() or 0.0
    total_ar_balance = round(total_ar - total_payments_applied, 2)

    unpaid_invoice_count = Invoice.query.filter(
        Invoice.status.in_(_outstanding)
    ).count()

    return render_template('dashboard.html',
                           weekly_revenue=weekly_revenue,
                           monthly_revenue=monthly_revenue,
                           monthly_payroll=monthly_payroll,
                           monthly_expenses=monthly_expenses_net,
                           monthly_profit=monthly_revenue - monthly_payroll - monthly_expenses_net,
                           total_clients=total_clients,
                           weekly_clients=weekly_clients,
                           services_this_week=services_this_week,
                           recent_services=recent_services,
                           total_ar=total_ar_balance,
                           unpaid_invoice_count=unpaid_invoice_count,
                           top_categories=top_categories,
                           today=today)


# ---------------------------------------------------------------------------
# Financial reports
# ---------------------------------------------------------------------------

@bp.route('/financials')
@login_required
def financials():
    year = request.args.get('year', eastern_today().year, type=int)

    monthly_data = get_monthly_financials(year)
    service_breakdown = get_service_breakdown(year)
    expense_categories_data = get_category_breakdown(year)
    years = get_available_years(year)

    total_income = sum(m['income'] for m in monthly_data)
    total_payroll = sum(m['payroll'] for m in monthly_data)
    total_expenses = sum(m['expenses'] for m in monthly_data)
    total_costs = total_payroll + total_expenses

    # Chart arrays (JSON-serializable)
    chart_months = [m['month'][:3] for m in monthly_data]
    chart_income = [round(m['income'], 2) for m in monthly_data]
    chart_payroll = [round(m['payroll'], 2) for m in monthly_data]
    chart_expenses = [round(m['expenses'], 2) for m in monthly_data]
    chart_profit = [round(m['profit'], 2) for m in monthly_data]
    cat_labels = [c['name'] for c in expense_categories_data]
    cat_amounts = [round(c['amount'], 2) for c in expense_categories_data]

    return render_template('financials.html',
                           monthly_data=monthly_data,
                           total_income=total_income,
                           total_payroll=total_payroll,
                           total_expenses=total_expenses,
                           total_costs=total_costs,
                           total_profit=total_income - total_costs,
                           service_breakdown=service_breakdown,
                           expense_categories_data=expense_categories_data,
                           chart_months=chart_months,
                           chart_income=chart_income,
                           chart_payroll=chart_payroll,
                           chart_expenses=chart_expenses,
                           chart_profit=chart_profit,
                           cat_labels=cat_labels,
                           cat_amounts=cat_amounts,
                           year=year, years=years)
