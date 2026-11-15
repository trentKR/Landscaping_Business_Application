"""Edge-case tests for per-invoice-balance invoice status logic.

Functions under test:
  Invoice.update_status()                 models.py
  Client.account_balance()                models.py
  refresh_client_invoice_statuses(client) services.py
  apply_payment_fifo(payment)             services.py
  auto_apply_credits(invoice)             services.py
  invoice_void route                      routes/invoices.py
"""

from datetime import datetime

from models import Invoice, PaymentApplication
from services import apply_payment_fifo, auto_apply_credits, refresh_client_invoice_statuses

# ---------------------------------------------------------------------------
# TestUpdateStatus — unit tests for Invoice.update_status()
# ---------------------------------------------------------------------------

class TestUpdateStatus:
    """Invoice.update_status() — status and paid_at transitions based on per-invoice balance."""

    def test_full_payment_applied_is_paid(self, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv.id, amount_applied=100))
        db.session.flush()
        inv.update_status()
        assert inv.status == 'paid'

    def test_overpayment_applied_is_paid(self, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 150)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv.id, amount_applied=150))
        db.session.flush()
        inv.update_status()
        assert inv.status == 'paid'

    def test_partial_payment_applied_is_partial(self, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 50)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv.id, amount_applied=50))
        db.session.flush()
        inv.update_status()
        assert inv.status == 'partial'

    def test_no_payment_applied_is_unpaid(self, client_obj, make_invoice):
        inv = make_invoice(client_obj, 100)
        inv.update_status()
        assert inv.status == 'unpaid'

    def test_payment_exists_but_not_applied_is_unpaid(self, client_obj, make_invoice, make_payment):
        inv = make_invoice(client_obj, 100)
        make_payment(client_obj, 100)   # unapplied credit — not linked to inv
        inv.update_status()
        assert inv.status == 'unpaid'

    def test_void_invoice_is_not_changed(self, client_obj, make_invoice):
        inv = make_invoice(client_obj, 100, status='void')
        inv.update_status()
        assert inv.status == 'void'

    def test_paid_at_set_when_transitioning_to_paid(self, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        assert inv.paid_at is None
        payment = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv.id, amount_applied=100))
        db.session.flush()
        inv.update_status()
        assert inv.paid_at is not None

    def test_paid_at_preserved_if_already_set(self, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        original = datetime(2025, 1, 15, 12, 0, 0)
        inv.paid_at = original
        payment = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv.id, amount_applied=100))
        db.session.flush()
        inv.update_status()
        assert inv.paid_at == original

    def test_paid_at_cleared_when_transitioning_to_partial(self, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        inv.paid_at = datetime(2025, 1, 15)
        payment = make_payment(client_obj, 50)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv.id, amount_applied=50))
        db.session.flush()
        inv.update_status()
        assert inv.paid_at is None

    def test_paid_at_cleared_when_transitioning_to_unpaid(self, client_obj, make_invoice):
        inv = make_invoice(client_obj, 100)
        inv.paid_at = datetime(2025, 1, 15)
        inv.update_status()
        assert inv.paid_at is None


# ---------------------------------------------------------------------------
# TestAccountBalance — unit tests for Client.account_balance()
# ---------------------------------------------------------------------------

class TestAccountBalance:
    """Client.account_balance() = non-void invoiced − total paid."""

    def test_no_invoices_no_payments_is_zero(self, client_obj):
        assert client_obj.account_balance() == 0

    def test_two_invoices_no_payments(self, client_obj, make_invoice):
        make_invoice(client_obj, 100)
        make_invoice(client_obj, 200)
        assert client_obj.account_balance() == 300

    def test_two_invoices_partial_payment(self, client_obj, make_invoice, make_payment):
        make_invoice(client_obj, 100)
        make_invoice(client_obj, 200)
        make_payment(client_obj, 150)
        assert client_obj.account_balance() == 150

    def test_two_invoices_full_payment(self, client_obj, make_invoice, make_payment):
        make_invoice(client_obj, 100)
        make_invoice(client_obj, 200)
        make_payment(client_obj, 300)
        assert client_obj.account_balance() == 0

    def test_two_invoices_overpayment_is_credit(self, client_obj, make_invoice, make_payment):
        make_invoice(client_obj, 100)
        make_invoice(client_obj, 200)
        make_payment(client_obj, 350)
        assert client_obj.account_balance() == -50

    def test_void_invoice_excluded_from_balance(self, client_obj, make_invoice):
        make_invoice(client_obj, 100)
        make_invoice(client_obj, 200, status='void')
        assert client_obj.account_balance() == 100

    def test_payment_with_no_invoices_gives_negative_balance(self, client_obj, make_payment):
        make_payment(client_obj, 100)
        assert client_obj.account_balance() == -100


# ---------------------------------------------------------------------------
# TestRefreshClientInvoiceStatuses — integration
# ---------------------------------------------------------------------------

class TestRefreshClientInvoiceStatuses:
    """refresh_client_invoice_statuses updates all non-void invoices via per-invoice balance_due."""

    def test_no_payments_both_invoices_unpaid(self, client_obj, make_invoice):
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 100)
        refresh_client_invoice_statuses(client_obj)
        assert inv_a.status == 'unpaid'
        assert inv_b.status == 'unpaid'

    def test_full_and_partial_applications_set_correct_statuses(
            self, client_obj, make_invoice, make_payment, db):
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 200)
        payment = make_payment(client_obj, 150)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv_a.id, amount_applied=100))
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv_b.id, amount_applied=50))
        db.session.flush()
        refresh_client_invoice_statuses(client_obj)
        assert inv_a.status == 'paid'
        assert inv_b.status == 'partial'

    def test_full_applications_all_invoices_paid(self, client_obj, make_invoice, make_payment, db):
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 200)
        payment = make_payment(client_obj, 300)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv_a.id, amount_applied=100))
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv_b.id, amount_applied=200))
        db.session.flush()
        refresh_client_invoice_statuses(client_obj)
        assert inv_a.status == 'paid'
        assert inv_b.status == 'paid'

    def test_full_application_marks_invoice_paid(self, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 200)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv.id, amount_applied=100))
        db.session.flush()
        refresh_client_invoice_statuses(client_obj)
        assert inv.status == 'paid'

    def test_void_invoice_not_touched_by_refresh(self, client_obj, make_invoice, make_payment, db):
        inv_a = make_invoice(client_obj, 100, status='void')
        inv_b = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv_b.id, amount_applied=100))
        db.session.flush()
        refresh_client_invoice_statuses(client_obj)
        assert inv_a.status == 'void'
        assert inv_b.status == 'paid'

    def test_client_with_no_invoices_does_not_crash(self, client_obj, make_payment):
        make_payment(client_obj, 100)
        refresh_client_invoice_statuses(client_obj)  # must not raise


# ---------------------------------------------------------------------------
# TestApplyPaymentFifo — integration
# ---------------------------------------------------------------------------

class TestApplyPaymentFifo:
    """apply_payment_fifo allocates oldest-first and refreshes statuses."""

    def test_payment_less_than_all_subtotals_applies_to_oldest(
            self, client_obj, make_invoice, make_payment, db):
        inv_a = make_invoice(client_obj, 100)
        make_invoice(client_obj, 200)
        payment = make_payment(client_obj, 50)

        remaining = apply_payment_fifo(payment)

        assert remaining == 0
        apps = PaymentApplication.query.filter_by(payment_id=payment.id).all()
        assert len(apps) == 1
        assert apps[0].invoice_id == inv_a.id
        assert apps[0].amount_applied == 50

    def test_payment_exactly_covers_invoice_balance_due(
            self, client_obj, make_invoice, make_payment):
        inv = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 100)

        remaining = apply_payment_fifo(payment)

        assert remaining == 0
        assert inv.status == 'paid'

    def test_payment_exceeds_all_invoices_returns_credit(
            self, client_obj, make_invoice, make_payment):
        inv = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 150)

        remaining = apply_payment_fifo(payment)

        assert remaining == 50
        assert inv.status == 'paid'

    def test_no_non_void_invoices_no_applications_full_credit(
            self, client_obj, make_invoice, make_payment):
        make_invoice(client_obj, 100, status='void')
        payment = make_payment(client_obj, 100)

        remaining = apply_payment_fifo(payment)

        assert remaining == 100
        assert PaymentApplication.query.filter_by(payment_id=payment.id).count() == 0

    def test_invoice_with_zero_balance_due_is_skipped(
            self, client_obj, make_invoice, make_payment, db):
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 100)
        prior = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(
            payment_id=prior.id, invoice_id=inv_a.id, amount_applied=100))
        db.session.flush()

        new_payment = make_payment(client_obj, 50)
        apply_payment_fifo(new_payment)

        apps = PaymentApplication.query.filter_by(payment_id=new_payment.id).all()
        assert len(apps) == 1
        assert apps[0].invoice_id == inv_b.id

    def test_paid_invoice_with_zero_balance_due_skipped_unpaid_applied(
            self, client_obj, make_invoice, make_payment, db):
        inv_a = make_invoice(client_obj, 100, status='paid')
        inv_b = make_invoice(client_obj, 100)
        prior = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(
            payment_id=prior.id, invoice_id=inv_a.id, amount_applied=100))
        db.session.flush()

        new_payment = make_payment(client_obj, 60)
        apply_payment_fifo(new_payment)

        apps = PaymentApplication.query.filter_by(payment_id=new_payment.id).all()
        assert len(apps) == 1
        assert apps[0].invoice_id == inv_b.id

    def test_rounding_does_not_leave_phantom_cents(self, client_obj, make_invoice, make_payment):
        """Three invoices of $33.33 paid with $99.99 → all paid, nothing left over."""
        invoices = [make_invoice(client_obj, 33.33) for _ in range(3)]
        payment = make_payment(client_obj, 99.99)
        remaining = apply_payment_fifo(payment)
        assert remaining == 0
        assert all(inv.status == 'paid' for inv in invoices)


# ---------------------------------------------------------------------------
# TestAutoApplyCredits — integration
# ---------------------------------------------------------------------------

class TestAutoApplyCredits:
    """auto_apply_credits applies unapplied credits to a new invoice."""

    def test_credit_fully_covers_new_invoice(self, client_obj, make_invoice, make_payment):
        make_payment(client_obj, 100)  # unapplied credit
        inv = make_invoice(client_obj, 100)
        auto_apply_credits(inv)
        assert inv.status == 'paid'

    def test_credit_partially_covers_new_invoice(self, client_obj, make_invoice, make_payment):
        make_payment(client_obj, 50)
        inv = make_invoice(client_obj, 100)
        auto_apply_credits(inv)
        assert inv.status == 'partial'

    def test_no_credits_status_remains_unpaid(self, client_obj, make_invoice):
        inv = make_invoice(client_obj, 100)
        auto_apply_credits(inv)
        assert inv.status == 'unpaid'

    def test_zero_balance_due_at_entry_calls_refresh(
            self, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(
            payment_id=payment.id, invoice_id=inv.id, amount_applied=100))
        db.session.flush()

        auto_apply_credits(inv)
        assert inv.status == 'paid'

    def test_oldest_credit_consumed_first(self, client_obj, make_invoice, make_payment, db):
        old = make_payment(client_obj, 30)
        new = make_payment(client_obj, 30)
        inv = make_invoice(client_obj, 40)
        auto_apply_credits(inv)
        applied = {pa.payment_id: pa.amount_applied
                   for pa in PaymentApplication.query.filter_by(invoice_id=inv.id)}
        assert applied[old.id] == 30
        assert applied[new.id] == 10
        assert inv.status == 'paid'


# ---------------------------------------------------------------------------
# TestInvoiceVoidRoute — route-level via Flask test client
# ---------------------------------------------------------------------------

class TestInvoiceVoidRoute:
    """POST /invoices/<id>/void — integration through the full request cycle."""

    def test_void_releases_credits_and_other_invoice_becomes_paid(
            self, http_client, client_obj, make_invoice, make_payment, db):
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 200)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv_a.id, amount_applied=100))
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv_b.id, amount_applied=100))
        db.session.commit()

        inv_a_id, inv_b_id = inv_a.id, inv_b.id
        resp = http_client.post(f'/invoices/{inv_a_id}/void')
        assert resp.status_code == 302

        db.session.expire_all()
        assert db.session.get(Invoice, inv_b_id).status == 'paid'

    def test_void_when_balance_still_covers_remaining_invoice_is_unpaid(
            self, http_client, client_obj, make_invoice, db):
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 100)
        db.session.commit()

        inv_a_id, inv_b_id = inv_a.id, inv_b.id
        resp = http_client.post(f'/invoices/{inv_a_id}/void')
        assert resp.status_code == 302

        db.session.expire_all()
        assert db.session.get(Invoice, inv_b_id).status == 'unpaid'

    def test_already_void_invoice_redirects_without_double_void(
            self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 100, status='void')
        db.session.commit()
        inv_id = inv.id

        resp = http_client.post(f'/invoices/{inv_id}/void')
        assert resp.status_code == 302

        db.session.expire_all()
        assert db.session.get(Invoice, inv_id).status == 'void'

    def test_void_deletes_all_payment_applications_on_invoice(
            self, http_client, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv.id, amount_applied=100))
        db.session.commit()
        inv_id = inv.id

        http_client.post(f'/invoices/{inv_id}/void')

        assert PaymentApplication.query.filter_by(invoice_id=inv_id).count() == 0

    def test_void_reapplies_freed_credits_to_remaining_invoices(
            self, http_client, client_obj, make_invoice, make_payment, db):
        """Void inv_a ($100 paid) → $100 freed → auto-reapplied to inv_b ($100) → paid."""
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv_a.id, amount_applied=100))
        db.session.commit()

        inv_a_id, inv_b_id = inv_a.id, inv_b.id
        resp = http_client.post(f'/invoices/{inv_a_id}/void')
        assert resp.status_code == 302

        db.session.expire_all()
        inv_b = db.session.get(Invoice, inv_b_id)
        assert inv_b.status == 'paid'
        assert inv_b.balance_due == 0

    def test_void_reapplies_partial_credit_to_remaining_invoice(
            self, http_client, client_obj, make_invoice, make_payment, db):
        """Void inv_a ($100 paid) → $100 freed → partial cover on inv_b ($150)."""
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 150)
        payment = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(payment_id=payment.id, invoice_id=inv_a.id, amount_applied=100))
        db.session.commit()

        inv_a_id, inv_b_id = inv_a.id, inv_b.id
        resp = http_client.post(f'/invoices/{inv_a_id}/void')
        assert resp.status_code == 302

        db.session.expire_all()
        inv_b = db.session.get(Invoice, inv_b_id)
        assert inv_b.status == 'partial'
        assert inv_b.balance_due == 50


# ---------------------------------------------------------------------------
# TestPerInvoiceStatusRegression — proves the fix for the global-balance bug
# ---------------------------------------------------------------------------

class TestPerInvoiceStatusRegression:
    """An invoice with zero balance_due must be 'paid' even when other invoices
    on the same client are still outstanding."""

    def test_fully_covered_invoice_is_paid_when_client_has_other_outstanding(
            self, client_obj, make_invoice, make_payment):
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 150)
        apply_payment_fifo(payment)
        assert inv_a.status == 'paid'
        assert inv_b.status == 'partial'

    def test_three_invoices_fifo_correct_statuses(
            self, client_obj, make_invoice, make_payment):
        inv_a = make_invoice(client_obj, 100)
        inv_b = make_invoice(client_obj, 100)
        inv_c = make_invoice(client_obj, 100)
        payment = make_payment(client_obj, 200)
        apply_payment_fifo(payment)
        assert inv_a.status == 'paid'
        assert inv_b.status == 'paid'
        assert inv_c.status == 'unpaid'
