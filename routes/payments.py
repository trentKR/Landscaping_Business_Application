from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from models import PAYMENT_TOLERANCE, Client, Payment, db
from services import apply_date_filters, apply_payment_fifo, client_balances, refresh_client_invoice_statuses
from tz import eastern_today
from utils import parse_amount

bp = Blueprint('payments', __name__)

# Upper bound on rows accepted by the quick-add grid in a single POST.
MAX_QUICK_ADD_ROWS = 200


@bp.route('/payments')
@login_required
def payment_list():
    search = request.args.get('search', '').strip()
    method_filter = request.args.get('method', 'all')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Payment.query.join(Client).options(joinedload(Payment.client))
    if search:
        query = query.filter(
            Client.first_name.ilike(f'%{search}%') |
            Client.last_name.ilike(f'%{search}%')
        )
    if method_filter != 'all':
        query = query.filter(Payment.payment_method == method_filter)
    query, date_from, date_to = apply_date_filters(query, Payment.payment_date, date_from, date_to)

    payments = query.order_by(Payment.payment_date.desc()).limit(200).all()
    total = sum(p.amount for p in payments)

    return render_template('payments.html', payments=payments,
                           search=search,
                           method_filter=method_filter,
                           date_from=date_from, date_to=date_to,
                           total=total,
                           payment_methods=Payment.PAYMENT_METHODS)


@bp.route('/payments/quick-add', methods=['GET', 'POST'])
@login_required
def payment_quick_add():
    """Bulk-enter multiple payments from a spreadsheet-style grid.

    One row per payment, each FIFO-applied exactly as /payments/new does it.
    The browser validates before submitting; this loop is the backstop and
    re-validates everything from scratch.
    """
    clients = Client.active_sorted()

    if request.method == 'POST':
        valid_client_ids = {c.id for c in clients}
        valid_methods = {val for val, _ in Payment.PAYMENT_METHODS}

        staged = []   # built in memory; added to the session only if every row is clean
        errors = []
        seen = set()

        for position, raw_idx in enumerate(
                request.form.getlist('row_index')[:MAX_QUICK_ADD_ROWS], start=1):
            if raw_idx in seen:
                continue  # duplicate index in a crafted post
            seen.add(raw_idx)

            date_str = request.form.get(f'payment_date_{raw_idx}', '').strip()
            client_str = request.form.get(f'client_id_{raw_idx}', '').strip()
            amount_str = request.form.get(f'amount_{raw_idx}', '').strip()
            method_str = request.form.get(f'payment_method_{raw_idx}', '').strip()
            reference = request.form.get(f'reference_{raw_idx}', '').strip()[:100]
            notes = request.form.get(f'notes_{raw_idx}', '').strip()[:250]

            # Blank-row test — deliberately ignores payment_date and
            # payment_method, because the template pre-fills both. Must stay
            # identical to rowIsBlank() in payment_quick_add.html.
            if not (client_str or amount_str or reference or notes):
                continue

            try:
                payment_date_val = date.fromisoformat(date_str)
            except ValueError:
                errors.append(f'Row {position}: enter a valid date.')
                continue

            try:
                client_id = int(client_str)
            except ValueError:
                client_id = 0
            # SQLite does not enforce this FK — an unknown ID would insert a
            # dangling reference and break the payment list page.
            if client_id not in valid_client_ids:
                errors.append(f'Row {position}: choose a client.')
                continue

            try:
                # No abs() here, unlike expenses: Payment has no is_refund flag,
                # so a negative is a typo rather than a sign convention.
                amount = parse_amount(amount_str)
            except ValueError:
                errors.append(f'Row {position}: enter a valid amount.')
                continue
            if amount <= 0:
                errors.append(f'Row {position}: amount must be more than $0.')
                continue

            if method_str not in valid_methods:
                errors.append(f'Row {position}: choose a payment method.')
                continue

            staged.append(Payment(
                client_id=client_id,
                payment_date=payment_date_val,
                amount=amount,
                payment_method=method_str,
                reference=reference,
                notes=notes,
            ))

        if errors:
            for msg in errors[:5]:
                flash(msg, 'danger')
            if len(errors) > 5:
                flash(f'…and {len(errors) - 5} more row(s) with problems.', 'danger')
            return redirect(url_for('payments.payment_quick_add'))

        if not staged:
            flash('No payments entered.', 'warning')
            return redirect(url_for('payments.payment_quick_add'))

        try:
            for payment in staged:
                db.session.add(payment)
            db.session.flush()   # one flush assigns every PK; FIFO needs them

            # Row order, not date order: FIFO consumes invoice balances
            # oldest-first, so when two rows target the same client whichever
            # runs first eats the older invoices. The totals come out identical
            # either way, but row order is what the user sees, so attribution
            # stays predictable. Applying sequentially is safe because
            # Invoice.balance_due is a live SUM over PaymentApplication and
            # apply_payment_fifo flushes each application as it creates it.
            total_unapplied = 0.0
            for payment in staged:
                total_unapplied += apply_payment_fifo(payment)

            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash('A database error occurred. No payments were recorded. Please try again.', 'danger')
            return redirect(url_for('payments.payment_quick_add'))

        total = sum(p.amount for p in staged)
        if total_unapplied > PAYMENT_TOLERANCE:
            flash(f'{len(staged)} payment(s) recorded (${total:,.2f}). '
                  f'${total_unapplied:,.2f} remains as unapplied credit.', 'info')
        else:
            flash(f'{len(staged)} payment(s) recorded (${total:,.2f}) and fully applied.', 'success')
        return redirect(url_for('payments.payment_list'))

    return render_template('payment_quick_add.html',
                           clients=clients, balances=client_balances(),
                           payment_methods=Payment.PAYMENT_METHODS,
                           today=eastern_today(), blank_rows=1,
                           max_rows=MAX_QUICK_ADD_ROWS)


@bp.route('/payments/<int:payment_id>')
@login_required
def payment_detail(payment_id):
    payment = db.session.get(Payment, payment_id) or abort(404)
    return render_template('payment_detail.html', payment=payment)


@bp.route('/payments/<int:payment_id>/edit', methods=['GET', 'POST'])
@login_required
def payment_edit(payment_id):
    payment = db.session.get(Payment, payment_id) or abort(404)
    if request.method == 'POST':
        try:
            amount = parse_amount(request.form['amount'])
            payment_date_val = date.fromisoformat(request.form['payment_date'])
        except (KeyError, ValueError):
            flash('Please fill in all required fields with valid values.', 'danger')
            return redirect(url_for('payments.payment_edit', payment_id=payment_id))

        if amount <= 0:
            flash('Payment amount must be greater than zero.', 'danger')
            return redirect(url_for('payments.payment_edit', payment_id=payment_id))

        old_amount = payment.amount
        payment.payment_date = payment_date_val
        payment.amount = amount
        payment.payment_method = request.form.get('payment_method', 'check')
        payment.reference = request.form.get('reference', '').strip()
        payment.notes = request.form.get('notes', '').strip()

        if abs(amount - old_amount) > PAYMENT_TOLERANCE:
            # Re-apply FIFO with new amount
            for app_row in list(payment.applications):
                db.session.delete(app_row)
            db.session.flush()
            try:
                apply_payment_fifo(payment)
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                flash('An error occurred re-applying the payment. Please try again.', 'danger')
                return redirect(url_for('payments.payment_edit', payment_id=payment_id))
        else:
            try:
                db.session.commit()
                refresh_client_invoice_statuses(payment.client)
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                flash('An error occurred saving the payment. Please try again.', 'danger')
                return redirect(url_for('payments.payment_edit', payment_id=payment_id))

        flash('Payment updated.', 'success')
        return redirect(url_for('payments.payment_detail', payment_id=payment_id))

    return render_template('payment_form.html',
                           payment=payment,
                           clients=None,
                           payment_methods=Payment.PAYMENT_METHODS,
                           today=eastern_today())


@bp.route('/payments/<int:payment_id>/delete', methods=['POST'])
@login_required
def payment_delete(payment_id):
    payment = db.session.get(Payment, payment_id) or abort(404)
    client = payment.client
    db.session.delete(payment)
    db.session.commit()
    refresh_client_invoice_statuses(client)
    db.session.commit()
    flash('Payment deleted. Invoice statuses have been updated.', 'warning')
    return redirect(url_for('payments.payment_list'))
