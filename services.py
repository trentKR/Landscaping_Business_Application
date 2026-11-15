"""Shared business logic and query utilities.

All functions assume an active Flask application context.
"""
from datetime import date
from typing import Any

from sqlalchemy import extract, func

from models import (
    PAYMENT_TOLERANCE,
    Client,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentApplication,
    Payroll,
    Service,
    db,
)

# ---------------------------------------------------------------------------
# Payment application helpers
# ---------------------------------------------------------------------------

def refresh_client_invoice_statuses(client: Client) -> None:
    """Recalculate status for all non-void invoices using each invoice's own balance_due."""
    for invoice in client.invoices.filter(Invoice.status != InvoiceStatus.VOID).all():
        invoice.update_status()


def apply_payment_fifo(payment: Payment) -> float:
    """Apply payment to unpaid invoices for this client, oldest first.
    Returns the remaining unapplied amount (credit)."""
    remaining = payment.amount
    invoices = Invoice.query.filter(
        Invoice.client_id == payment.client_id,
        Invoice.status != InvoiceStatus.VOID
    ).order_by(Invoice.invoice_date.asc(), Invoice.id.asc()).all()

    for invoice in invoices:
        if remaining <= PAYMENT_TOLERANCE:
            break
        balance = invoice.balance_due
        if balance <= 0:
            continue
        apply_amount = round(min(remaining, balance), 2)
        pa = PaymentApplication(
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount_applied=apply_amount
        )
        db.session.add(pa)
        db.session.flush()
        remaining = round(remaining - apply_amount, 2)

    refresh_client_invoice_statuses(payment.client)
    return remaining


def client_balances() -> dict[int, float]:
    """Account balance for every client, in two grouped queries.

    Same formula as Client.account_balance(), batched. That method costs two
    SUM queries per client, which the quick-add grid would multiply by the whole
    client list. A missing key means a zero balance.
    """
    invoiced = dict(db.session.query(
        Invoice.client_id, func.sum(Invoice.subtotal)
    ).filter(Invoice.status != InvoiceStatus.VOID).group_by(Invoice.client_id).all())

    paid = dict(db.session.query(
        Payment.client_id, func.sum(Payment.amount)
    ).group_by(Payment.client_id).all())

    return {
        client_id: round((invoiced.get(client_id) or 0.0) - (paid.get(client_id) or 0.0), 2)
        for client_id in set(invoiced) | set(paid)
    }


def auto_apply_credits(invoice: Invoice) -> None:
    """Apply any unapplied payment credits to this invoice, oldest payment first.

    Pre-loads all PaymentApplication totals in one query to avoid N+1 when
    iterating payments with lazy-loaded applications relationships.
    """
    balance = invoice.balance_due
    if balance <= PAYMENT_TOLERANCE:
        refresh_client_invoice_statuses(invoice.client)
        return

    payments = Payment.query.filter(
        Payment.client_id == invoice.client_id
    ).order_by(Payment.payment_date.asc(), Payment.id.asc()).all()

    if not payments:
        refresh_client_invoice_statuses(invoice.client)
        return

    # One query for all applied amounts — avoids N lazy loads inside the loop
    payment_ids = [p.id for p in payments]
    applied_totals: dict[int, float] = dict(
        db.session.query(
            PaymentApplication.payment_id,
            func.sum(PaymentApplication.amount_applied)
        ).filter(
            PaymentApplication.payment_id.in_(payment_ids)
        ).group_by(PaymentApplication.payment_id).all()
    )

    for payment in payments:
        if balance <= PAYMENT_TOLERANCE:
            break
        unapplied = round(payment.amount - applied_totals.get(payment.id, 0.0), 2)
        if unapplied <= 0:
            continue
        apply_amount = round(min(balance, unapplied), 2)
        pa = PaymentApplication(
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount_applied=apply_amount
        )
        db.session.add(pa)
        db.session.flush()
        # Track locally so subsequent iterations see the consumed amount
        applied_totals[payment.id] = applied_totals.get(payment.id, 0.0) + apply_amount
        balance = round(balance - apply_amount, 2)

    refresh_client_invoice_statuses(invoice.client)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_outstanding_invoices(invoice: Invoice) -> list[tuple[str, float]]:
    """Return [(invoice_number, balance_due), ...] for older unpaid invoices."""
    older = Invoice.query.filter(
        Invoice.client_id == invoice.client_id,
        Invoice.status != InvoiceStatus.VOID,
        Invoice.id != invoice.id,
        db.or_(
            Invoice.invoice_date < invoice.invoice_date,
            db.and_(Invoice.invoice_date == invoice.invoice_date,
                    Invoice.id < invoice.id)
        )
    ).order_by(Invoice.invoice_date.asc(), Invoice.id.asc()).all()
    return [(inv.invoice_number, inv.balance_due) for inv in older if inv.balance_due > 0.005]


def apply_date_filters(query, column, date_from: str, date_to: str):
    """Apply from/to date filters to a SQLAlchemy query.
    Returns (query, date_from, date_to) with invalid strings cleared to ''."""
    if date_from:
        try:
            query = query.filter(column >= date.fromisoformat(date_from))
        except ValueError:
            date_from = ''
    if date_to:
        try:
            query = query.filter(column <= date.fromisoformat(date_to))
        except ValueError:
            date_to = ''
    return query, date_from, date_to


# ---------------------------------------------------------------------------
# Financial reports — helpers
# ---------------------------------------------------------------------------

def get_monthly_financials(year: int) -> list[dict[str, Any]]:
    """Return 12 monthly dicts with income, payroll, expenses, and profit. 4 DB queries."""
    income_by_month = dict(db.session.query(
        extract('month', Service.service_date),
        func.sum(Service.amount)
    ).filter(extract('year', Service.service_date) == year
    ).group_by(extract('month', Service.service_date)).all())

    payroll_by_month = dict(db.session.query(
        extract('month', Payroll.work_date),
        func.sum(Payroll.total_payment)
    ).filter(extract('year', Payroll.work_date) == year
    ).group_by(extract('month', Payroll.work_date)).all())

    expense_by_month = dict(db.session.query(
        extract('month', Expense.expense_date),
        func.sum(Expense.amount)
    ).filter(
        extract('year', Expense.expense_date) == year,
        Expense.is_refund.is_(False)
    ).group_by(extract('month', Expense.expense_date)).all())

    refund_by_month = dict(db.session.query(
        extract('month', Expense.expense_date),
        func.sum(Expense.amount)
    ).filter(
        extract('year', Expense.expense_date) == year,
        Expense.is_refund.is_(True)
    ).group_by(extract('month', Expense.expense_date)).all())

    rows = []
    for month in range(1, 13):
        income = income_by_month.get(month, 0) or 0.0
        payroll_cost = payroll_by_month.get(month, 0) or 0.0
        expense_cost = expense_by_month.get(month, 0) or 0.0
        refund_amount = refund_by_month.get(month, 0) or 0.0
        net_expenses = expense_cost - refund_amount
        rows.append({
            'month': date(year, month, 1).strftime('%B'),
            'income': income,
            'payroll': payroll_cost,
            'expenses': net_expenses,
            'total_costs': payroll_cost + net_expenses,
            'profit': income - payroll_cost - net_expenses,
        })
    return rows


def get_service_breakdown(year: int) -> list[dict[str, Any]]:
    """Revenue and count by service type for the year."""
    type_labels = dict(Service.SERVICE_TYPES)
    rows = db.session.query(
        Service.service_type,
        func.sum(Service.amount),
        func.count(Service.id)
    ).filter(extract('year', Service.service_date) == year
    ).group_by(Service.service_type).all()
    return [
        {'type': type_labels.get(t, t), 'revenue': rev or 0, 'count': cnt}
        for t, rev, cnt in rows
    ]


def get_category_breakdown(year: int) -> list[dict[str, Any]]:
    """Expense amount and count by category for the year (excluding refunds)."""
    rows = db.session.query(
        ExpenseCategory.name,
        func.sum(Expense.amount),
        func.count(Expense.id)
    ).join(Expense).filter(
        extract('year', Expense.expense_date) == year,
        Expense.is_refund.is_(False)
    ).group_by(ExpenseCategory.name).order_by(func.sum(Expense.amount).desc()).all()
    return [{'name': name, 'amount': amt or 0, 'count': cnt} for name, amt, cnt in rows]


def get_available_years(current_year: int) -> list[int]:
    """Sorted list of years that have any financial data, always including current_year."""
    min_service = db.session.query(func.min(extract('year', Service.service_date))).scalar()
    min_payroll = db.session.query(func.min(extract('year', Payroll.work_date))).scalar()
    min_expense = db.session.query(func.min(extract('year', Expense.expense_date))).scalar()
    candidates = [y for y in [min_service, min_payroll, min_expense, current_year] if y]
    return list(range(int(min(candidates)), current_year + 1))
