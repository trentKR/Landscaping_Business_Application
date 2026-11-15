import csv
import io
from datetime import date

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.datastructures import ImmutableMultiDict

from models import Client, Expense, ExpenseAllocation, ExpenseCategory, Vendor, db
from services import apply_date_filters
from tz import eastern_today
from utils import parse_amount

bp = Blueprint('expenses', __name__)

# Upper bound on rows accepted by the quick-add grid in a single POST.
MAX_QUICK_ADD_ROWS = 200


@bp.route('/expenses')
@login_required
def expense_list():
    search = request.args.get('search', '').strip()
    category_filter = request.args.get('category_id', '', type=str)
    vendor_filter = request.args.get('vendor_id', '', type=str)
    method_filter = request.args.get('method', 'all')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Expense.query.join(ExpenseCategory).options(
        joinedload(Expense.category),
        joinedload(Expense.vendor),
        joinedload(Expense.client)
    )
    if search:
        query = query.filter(Expense.description.ilike(f'%{search}%'))
    if category_filter:
        try:
            query = query.filter(Expense.category_id == int(category_filter))
        except ValueError:
            category_filter = ''
    if vendor_filter:
        try:
            query = query.filter(Expense.vendor_id == int(vendor_filter))
        except ValueError:
            vendor_filter = ''
    if method_filter != 'all':
        query = query.filter(Expense.payment_method == method_filter)
    query, date_from, date_to = apply_date_filters(query, Expense.expense_date, date_from, date_to)

    expenses = query.order_by(Expense.expense_date.desc()).limit(200).all()
    total = sum(e.effective_amount for e in expenses)

    all_categories = ExpenseCategory.query.order_by(ExpenseCategory.name).all()
    all_vendors = Vendor.query.filter_by(active=True).order_by(Vendor.name).all()

    return render_template('expenses.html', expenses=expenses,
                           search=search, category_filter=category_filter,
                           vendor_filter=vendor_filter, method_filter=method_filter,
                           date_from=date_from, date_to=date_to,
                           total=total,
                           all_categories=all_categories,
                           all_vendors=all_vendors,
                           payment_methods=Expense.PAYMENT_METHODS)


def _save_allocations(expense: Expense, form: ImmutableMultiDict) -> int:
    """Delete existing allocations and rebuild from parallel form arrays.

    Returns the number of rows skipped due to unparseable client ID or amount.
    Caller is responsible for flashing a warning if the count is non-zero.
    """
    for a in list(expense.allocations):
        db.session.delete(a)
    client_ids = form.getlist('alloc_client_id[]')
    amounts = form.getlist('alloc_amount[]')
    skipped = 0
    for cid, amt in zip(client_ids, amounts, strict=False):
        try:
            cid_int = int(cid)
            amt_float = parse_amount(amt)
        except (ValueError, TypeError):
            skipped += 1
            continue
        if cid_int and amt_float > 0:
            db.session.add(ExpenseAllocation(
                expense_id=expense.id,
                client_id=cid_int,
                amount=amt_float,
            ))
    return skipped


def _apply_expense_form(expense: Expense, form: ImmutableMultiDict) -> int:
    """Populate an Expense from form data and rebuild allocations.
    Returns count of skipped allocations."""
    expense.expense_date = date.fromisoformat(form['expense_date'])
    expense.amount = abs(parse_amount(form['amount']))
    expense.category_id = int(form['category_id'])
    expense.vendor_id = form.get('vendor_id', type=int) or None
    expense.payment_method = form['payment_method']
    expense.description = form.get('description', '').strip()
    expense.service_id = form.get('service_id', type=int) or None
    is_split = 'split_mode' in form
    expense.client_id = None if is_split else (form.get('client_id', type=int) or None)
    expense.is_refund = 'is_refund' in form
    return _save_allocations(expense, form)


@bp.route('/expenses/quick-add', methods=['GET', 'POST'])
@login_required
def expense_quick_add():
    """Bulk-enter multiple expenses from a spreadsheet-style grid.

    One row per expense. Client attribution and splits are not offered here —
    use /expenses/new for those. The browser validates before submitting; this
    loop is the backstop and re-validates everything from scratch.
    """
    categories = ExpenseCategory.query.order_by(ExpenseCategory.name).all()
    vendors = Vendor.active_sorted()

    if request.method == 'POST':
        valid_category_ids = {c.id for c in categories}
        valid_vendor_ids = {v.id for v in vendors}
        valid_methods = {val for val, _ in Expense.PAYMENT_METHODS}

        staged = []   # built in memory; added to the session only if every row is clean
        errors = []
        seen = set()

        for position, raw_idx in enumerate(
                request.form.getlist('row_index')[:MAX_QUICK_ADD_ROWS], start=1):
            if raw_idx in seen:
                continue  # duplicate index in a crafted post
            seen.add(raw_idx)

            date_str = request.form.get(f'expense_date_{raw_idx}', '').strip()
            amount_str = request.form.get(f'amount_{raw_idx}', '').strip()
            category_str = request.form.get(f'category_id_{raw_idx}', '').strip()
            vendor_str = request.form.get(f'vendor_id_{raw_idx}', '').strip()
            method_str = request.form.get(f'payment_method_{raw_idx}', '').strip()
            description = request.form.get(f'description_{raw_idx}', '').strip()[:250]
            is_refund = bool(request.form.get(f'is_refund_{raw_idx}'))

            # Blank-row test — deliberately ignores expense_date, payment_method
            # and is_refund, because the template pre-fills the first two and the
            # third alone does not describe an expense. Must stay identical to
            # rowIsBlank() in expense_quick_add.html.
            if not (amount_str or category_str or vendor_str or description):
                continue

            try:
                expense_date = date.fromisoformat(date_str)
            except ValueError:
                errors.append(f'Row {position}: enter a valid date.')
                continue

            try:
                amount = abs(parse_amount(amount_str))
            except ValueError:
                errors.append(f'Row {position}: enter a valid amount.')
                continue
            if amount <= 0:
                errors.append(f'Row {position}: amount must be more than $0.')
                continue

            try:
                category_id = int(category_str)
            except ValueError:
                category_id = 0
            if category_id not in valid_category_ids:
                errors.append(f'Row {position}: choose a category.')
                continue

            vendor_id = None
            if vendor_str:
                try:
                    vendor_id = int(vendor_str)
                except ValueError:
                    vendor_id = None
                # SQLite does not enforce these FKs — an unknown ID would insert a
                # dangling reference and break the expense list page.
                if vendor_id not in valid_vendor_ids:
                    errors.append(f'Row {position}: unknown vendor.')
                    continue

            if method_str not in valid_methods:
                errors.append(f'Row {position}: choose a payment method.')
                continue

            staged.append(Expense(
                expense_date=expense_date,
                amount=amount,
                category_id=category_id,
                vendor_id=vendor_id,
                payment_method=method_str,
                description=description,
                is_refund=is_refund,
            ))

        if errors:
            for msg in errors[:5]:
                flash(msg, 'danger')
            if len(errors) > 5:
                flash(f'…and {len(errors) - 5} more row(s) with problems.', 'danger')
            return redirect(url_for('expenses.expense_quick_add'))

        if not staged:
            flash('No expenses entered.', 'warning')
            return redirect(url_for('expenses.expense_quick_add'))

        try:
            for expense in staged:
                db.session.add(expense)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash('A database error occurred. No expenses were created. Please try again.', 'danger')
            return redirect(url_for('expenses.expense_quick_add'))

        refund_count = sum(1 for e in staged if e.is_refund)
        parts = [f'{len(staged)} expense(s) recorded']
        if refund_count:
            parts.append(f'{refund_count} marked as refund')
        flash(', '.join(parts) + '.', 'success')
        return redirect(url_for('expenses.expense_list'))

    return render_template('expense_quick_add.html',
                           categories=categories, vendors=vendors,
                           payment_methods=Expense.PAYMENT_METHODS,
                           today=eastern_today(), blank_rows=1,
                           max_rows=MAX_QUICK_ADD_ROWS)


@bp.route('/expenses/<int:expense_id>')
@login_required
def expense_detail(expense_id):
    expense = db.session.get(Expense, expense_id) or abort(404)
    return render_template('expense_detail.html', expense=expense)


@bp.route('/expenses/<int:expense_id>/edit', methods=['GET', 'POST'])
@login_required
def expense_edit(expense_id):
    expense = db.session.get(Expense, expense_id) or abort(404)
    if request.method == 'POST':
        try:
            # Validate the three required fields before applying changes
            date.fromisoformat(request.form['expense_date'])
            if parse_amount(request.form['amount']) == 0:
                raise ValueError
            int(request.form['category_id'])
        except (ValueError, KeyError):
            flash('Please fill in all required fields with valid values.', 'danger')
            return redirect(url_for('expenses.expense_edit', expense_id=expense_id))
        skipped = _apply_expense_form(expense, request.form)
        if skipped:
            flash(f'{skipped} allocation(s) skipped — invalid client or amount.', 'warning')
        db.session.commit()
        flash('Expense updated.', 'success')
        return redirect(url_for('expenses.expense_detail', expense_id=expense.id))

    categories = ExpenseCategory.query.order_by(ExpenseCategory.name).all()
    vendors = Vendor.query.filter_by(active=True).order_by(Vendor.name).all()
    clients = Client.query.filter_by(active=True).order_by(Client.last_name, Client.first_name).all()

    return render_template('expense_form.html', expense=expense,
                           categories=categories, vendors=vendors,
                           clients=clients,
                           payment_methods=Expense.PAYMENT_METHODS,
                           today=eastern_today())


@bp.route('/expenses/<int:expense_id>/delete', methods=['POST'])
@login_required
def expense_delete(expense_id):
    expense = db.session.get(Expense, expense_id) or abort(404)
    db.session.delete(expense)
    db.session.commit()
    flash('Expense deleted.', 'warning')
    return redirect(url_for('expenses.expense_list'))


@bp.route('/expenses/export')
@login_required
def expense_export():
    """Export expenses as CSV for tax purposes."""
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Expense.query.join(ExpenseCategory).outerjoin(Vendor)
    if date_from:
        try:
            query = query.filter(Expense.expense_date >= date.fromisoformat(date_from))
        except ValueError:
            date_from = ''
    if date_to:
        try:
            query = query.filter(Expense.expense_date <= date.fromisoformat(date_to))
        except ValueError:
            date_to = ''

    expenses = query.order_by(Expense.expense_date.asc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Amount', 'Type', 'Category', 'Vendor',
                     'Payment Method', 'Description', 'Client', 'Job Linked'])
    for e in expenses:
        if e.is_split:
            client_col = '; '.join(f'{a.client.full_name} (${a.amount:.2f})' for a in e.allocations)
        else:
            client_col = e.client.full_name if e.client else ''
        writer.writerow([
            e.expense_date.isoformat(),
            f'{e.effective_amount:.2f}',
            'Refund' if e.is_refund else 'Expense',
            e.category.name,
            e.vendor.name if e.vendor else '',
            e.method_display,
            e.description,
            client_col,
            'Yes' if e.service_id else 'No',
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=expenses_{eastern_today().isoformat()}.csv'}
    )


# ---------------------------------------------------------------------------
# Expense API endpoints
# ---------------------------------------------------------------------------

@bp.route('/api/vendors/search')
@login_required
def api_vendor_search():
    """Auto-suggest vendors by name prefix."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    vendors = Vendor.query.filter(
        Vendor.active.is_(True),
        Vendor.name.ilike(f'%{q}%')
    ).order_by(Vendor.name).limit(10).all()
    return jsonify([{'id': v.id, 'name': v.name} for v in vendors])


@bp.route('/api/expense-categories')
@login_required
def api_expense_categories():
    """Return all expense categories."""
    categories = ExpenseCategory.query.order_by(ExpenseCategory.name).all()
    return jsonify([{'id': c.id, 'name': c.name} for c in categories])
