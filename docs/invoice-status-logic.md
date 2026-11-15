# Invoice Status Logic — Design Document

**Date:** 2026-02-28
**Status:** Implemented — Approach 2 is live as of 2026-02-28
**Purpose:** Capture current behavior, identify known bugs, and evaluate approaches before
changing any invoice or payment code.

---

## Table of Contents

1. [Current Functionality (As-Is)](#1-current-functionality-as-is)
2. [Full Dependency Map](#2-full-dependency-map)
3. [Four Approaches](#3-four-approaches)
4. [Recommended Implementation Path (Approach 2)](#4-recommended-implementation-path-approach-2)
5. [Open Questions](#5-open-questions)

---

## 1. Current Functionality (As-Is)

### 1.1 Invoice Status Values

| Status | Meaning |
|--------|---------|
| `unpaid` | Invoice exists, no payment applied |
| `partial` | Some payment applied, balance remains |
| `paid` | Fully settled — balance ≤ $0.005 |
| `void` | Cancelled — excluded from all AR calculations |

Invoices are created immediately as `unpaid`. There is no draft or pending step.

### 1.2 `Invoice.update_status(client_balance)` — `models.py:177`

```python
def update_status(self, client_balance):
    if self.status == 'void':
        return
    if client_balance <= 0:
        self.status = 'paid'
        if not self.paid_at:
            self.paid_at = datetime.utcnow()
    elif client_balance < self.subtotal:
        self.status = 'partial'
        self.paid_at = None
    else:
        self.status = 'unpaid'
        self.paid_at = None
```

**Parameter:** `client_balance` — the global client account balance (total non-void invoiced
minus total payments). This is a single number applied identically to every invoice.

**`paid_at` behavior:**
- Set once when transitioning to `paid` — not overwritten if already set.
- Cleared when regressing to `partial` or `unpaid`.
- Void invoices are never touched.

**Rounding tolerance:** Status is `paid` when `client_balance <= 0` (effectively ≤ $0.005
after payment rounding). `balance_due` is explicitly `round(subtotal - amount_paid, 2)`.

### 1.3 `refresh_client_invoice_statuses(client)` — `app.py:44`

```python
def refresh_client_invoice_statuses(client):
    balance = client.account_balance()          # one DB query
    for invoice in client.invoices.filter(Invoice.status != 'void').all():
        invoice.update_status(balance)          # same balance for all invoices
```

Computes the global client balance once, then applies it to every non-void invoice.
This is the only place where `update_status()` is called.

### 1.4 `apply_payment_fifo(payment)` — `app.py:51`

```
remaining = payment.amount
invoices = client's non-void invoices, ordered by invoice_date ASC, id ASC

for each invoice:
    if remaining <= 0.005: break
    if invoice.balance_due <= 0: skip            # already covered
    apply = round(min(remaining, invoice.balance_due), 2)
    create PaymentApplication(payment, invoice, apply)
    db.session.flush()
    remaining = round(remaining - apply, 2)

refresh_client_invoice_statuses(payment.client)  # one call at the end
return remaining                                 # unapplied credit
```

`balance_due` = `round(subtotal - amount_paid, 2)` where `amount_paid` is the sum of all
`PaymentApplication.amount_applied` rows for that invoice.

### 1.5 `auto_apply_credits(invoice)` — `app.py:80`

Called when a new invoice is created. Applies any existing unapplied credits to it.

```
balance = invoice.balance_due
if balance <= 0.005:
    refresh_client_invoice_statuses(invoice.client)
    return

payments = client's payments, ordered by payment_date ASC, id ASC
for each payment:
    if balance <= 0.005: break
    unapplied = payment.amount_unapplied        # = amount - SUM(applications)
    if unapplied <= 0: continue
    apply = round(min(balance, unapplied), 2)
    create PaymentApplication(payment, invoice, apply)
    db.session.flush()
    balance = round(balance - apply, 2)

refresh_client_invoice_statuses(invoice.client)  # one call at the end
```

### 1.6 Balance Formulas — `models.py`

```python
# Client level (DB queries)
Client.account_balance()   = SUM(invoice.subtotal where status != 'void')
                           - SUM(payment.amount)
                           # Positive = client owes | Negative = client has credit

Client.unapplied_credit()  = SUM(payment.amount)
                           - SUM(payment_application.amount_applied)
                           # Amount available to apply to future invoices

# Invoice level (Python properties — no DB query, uses loaded relationships)
Invoice.amount_paid        = sum(pa.amount_applied for pa in self.payment_applications)
Invoice.balance_due        = round(self.subtotal - self.amount_paid, 2)
```

### 1.7 Known Bug — Global Balance Comparison

**The problem:** `update_status()` receives the *global client balance* and compares it
to `self.subtotal`. This produces wrong statuses for clients with multiple invoices
at different payment levels.

**Concrete example:**

```
Setup:
  Invoice A: subtotal = $100
  Invoice B: subtotal = $100
  Payment:   $150  (FIFO → $100 to A, $50 to B)

PaymentApplications after FIFO:
  PA(payment, A, $100)  →  A.balance_due = $0.00   (fully paid)
  PA(payment, B, $50)   →  B.balance_due = $50.00  (partial)

refresh_client_invoice_statuses():
  client.account_balance() = ($100 + $100) - $150 = $50

  A.update_status($50):
    $50 <= 0?        No
    $50 < $100?      Yes  →  status = 'partial'   ← WRONG (should be 'paid')

  B.update_status($50):
    $50 <= 0?        No
    $50 < $100?      Yes  →  status = 'partial'   ← CORRECT
```

Invoice A has zero balance due (fully covered by PaymentApplications), but its status
is reported as `partial` because the global client balance ($50) is compared to A's
subtotal ($100).

**When the current logic works correctly:** Only when a client has exactly one non-void
invoice at a time. The global balance equals the single invoice's balance in that case.

**When the bug manifests:**
- Any client with 2+ invoices and a payment that fully covers one but not all invoices.
- This is common for the intended use case (50+ weekly clients, monthly invoicing).

**Visible impact:**
- Invoice detail page shows wrong status badge (`partial` instead of `paid`).
- AR aging report may include fully-covered invoices in outstanding balance buckets
  (it uses `inv.balance_due`, which is correct, but the status filter on `unpaid/partial`
  can include A unnecessarily).
- `paid_at` is never set for fully-paid invoices that share a client with other invoices.

### 1.8 Startup Migration

On every app startup (`app.py:1316-1320`), a SQL migration runs:

```sql
UPDATE invoices SET status = 'unpaid' WHERE status IN ('draft', 'sent')
```

This is a legacy conversion from before the status simplification (2026-02-25).

---

## 2. Full Dependency Map

### 2.1 Functions That Call `refresh_client_invoice_statuses()`

| Location | Trigger |
|----------|---------|
| `app.py:76` | After `apply_payment_fifo()` |
| `app.py:84` | Early return in `auto_apply_credits()` (balance already 0) |
| `app.py:107` | After `auto_apply_credits()` loop |
| `app.py:654` | After `invoice_void` route voids an invoice |

### 2.2 Functions That Call `apply_payment_fifo()` or `auto_apply_credits()`

| Location | Context |
|----------|---------|
| `app.py` (payment route, `POST /payments/new`) | Calls `apply_payment_fifo()` on new payment |
| `app.py` (invoice create route, `POST /invoices/new`) | Calls `auto_apply_credits()` after creating invoice |

### 2.3 Code Depending on `Invoice.status`

| File | Line(s) | Dependency |
|------|---------|-----------|
| `app.py` | 47 | `refresh_client_invoice_statuses` — filters `status != 'void'` |
| `app.py` | 55–58 | `apply_payment_fifo` — FIFO query filters `status != 'void'` |
| `app.py` | 113–114 | `compute_aging_buckets` — filters `status IN ('unpaid', 'partial')` |
| `app.py` | 248–256 | Dashboard AR summary — `status IN ('unpaid', 'partial')` |
| `app.py` | 258–268 | Dashboard overdue AR — same filter + `due_date < today` |
| `app.py` | 641–657 | `invoice_void` route — guards against double-void, sets `status = 'void'` |
| `models.py` | 79 | `Client.account_balance()` — excludes `status == 'void'` |
| `models.py` | 179 | `Invoice.update_status()` — guards void invoices |
| `templates/invoices.html` | filter | Status filter dropdown (`unpaid`, `partial`, `paid`, `void`) |
| `templates/invoice_detail.html` | 8, 11 | Displays badge; hides void button if already void |
| `templates/client_detail.html` | 86–89 | Shows unapplied credit if > 0 |
| `templates/aging_report.html` | — | Reads `balance_due` per invoice (correct), filtered by status |
| `templates/dashboard.html` | — | AR totals, overdue totals |
| `templates/payment_detail.html` | 70–71 | Shows `amount_unapplied` |
| `static/style.css` | `.badge-unpaid`, `.badge-partial`, `.badge-paid`, `.badge-void` | Badge colors |

### 2.4 Code Depending on `Client.account_balance()`

| File | Usage |
|------|-------|
| `app.py:46` | `refresh_client_invoice_statuses()` — feeds into `update_status()` |
| `app.py:358–365` | `client_detail` route — displayed on client profile page |
| `app.py:782` | `/api/client/<id>/balance` JSON endpoint |

### 2.5 Code Depending on `Invoice.balance_due`

| File | Usage |
|------|-------|
| `app.py:63` | `apply_payment_fifo` — skip if `<= 0`, compute amount to apply |
| `app.py:82` | `auto_apply_credits` — initial guard check |
| `app.py:124` | `compute_aging_buckets` — bucket amount per invoice |
| `app.py:547` | Invoice detail route — `total_outstanding` display |
| `templates/invoices.html:89` | Invoice list table |
| `templates/invoice_detail.html:98–99` | Balance display with color |
| `templates/invoice_print.html:454` | Print view balance |

### 2.6 Tests

**File:** `test_invoice_status.py` — 37 tests across 6 classes

| Class | Tests | What It Covers |
|-------|-------|---------------|
| `TestUpdateStatus` | 10 | `update_status(client_balance)` transitions and `paid_at` behavior |
| `TestAccountBalance` | 7 | `Client.account_balance()` formula |
| `TestRefreshClientInvoiceStatuses` | 6 | Integration across multiple invoices |
| `TestApplyPaymentFifo` | 6 | FIFO allocation, skip logic, return value |
| `TestAutoApplyCredits` | 5 | Credit application to new invoices |
| `TestInvoiceVoidRoute` | 3 | HTTP route: void, double-void guard |

**Gaps in current test coverage:**
- No test asserts that Invoice A is `paid` when Payment covers A fully but client has Invoice B outstanding (the bug scenario).
- `TestRefreshClientInvoiceStatuses.test_balance_gte_smaller_only_gives_partial` at line 143–150 tests the inverse: balance ≥ $100 subtotal → `unpaid`, which is correct for that direction. But there is no test for "Invoice A balance_due = 0 yet status = partial due to global balance."
- No tests for `Invoice.amount_paid` or `Invoice.balance_due` properties directly.
- No test for the startup migration (draft/sent → unpaid).

---

## 3. Four Approaches

### Approach 1 — Current Global Balance (As-Is)

**How it works:**
- `refresh_client_invoice_statuses()` computes one `client.account_balance()` value.
- Every non-void invoice receives the same `client_balance` in `update_status()`.
- Status is determined by comparing `client_balance` to each invoice's `subtotal`.

**When correct:**
- Client has exactly one non-void invoice. Global balance = that invoice's balance.
- Client pays all outstanding invoices exactly. Global balance = 0 → all paid.

**The bug it contains:**
- Any partial payment on a multi-invoice client will report wrong statuses for invoices
  that are individually fully covered.
- Example: Pay $150 against Invoice A ($100) + Invoice B ($100). A is fully paid per
  PaymentApplications, but `update_status($50)` marks it `partial`.

**Pros:**
- Simple: one DB query per refresh.
- Atomically consistent across all invoices (global balance never double-counts).
- Existing tests pass.

**Cons:**
- Produces incorrect statuses in the common multi-invoice case.
- `paid_at` never set for individually-paid invoices on multi-invoice clients.
- Misleading AR report data and confusing UI.

---

### Approach 2 — Per-Invoice Balance (Fix using PaymentApplications) — Recommended

**How it works:**
- `update_status()` uses `self.balance_due` (already computed from `PaymentApplications`)
  instead of `client_balance`.
- No parameter needed. Each invoice evaluates its own actual balance.
- FIFO algorithm and `PaymentApplication` records remain unchanged.

**Status logic:**
```python
def update_status(self):
    if self.status == 'void':
        return
    if self.balance_due <= 0.005:
        self.status = 'paid'
        if not self.paid_at:
            self.paid_at = datetime.utcnow()
    elif self.amount_paid > 0:
        self.status = 'partial'
        self.paid_at = None
    else:
        self.status = 'unpaid'
        self.paid_at = None
```

**Key difference:** Uses `self.amount_paid > 0` (sum of actual PAs) to detect partial
rather than a comparison with the global client balance.

**`refresh_client_invoice_statuses()` becomes:**
```python
def refresh_client_invoice_statuses(client):
    for invoice in client.invoices.filter(Invoice.status != 'void').all():
        invoice.update_status()    # no argument
```

No `client.account_balance()` call needed.

**Correctness for the bug example:**
```
Invoice A: subtotal=$100, amount_paid=$100  →  balance_due=$0.00  →  'paid'  ✓
Invoice B: subtotal=$100, amount_paid=$50   →  balance_due=$50.00 →  'partial' ✓
```

**Pros:**
- Fixes the bug with minimal code change (~15 lines total).
- Uses the data that is already recorded (PaymentApplications) — no new models.
- FIFO algorithm and credit logic unchanged.
- `Client.account_balance()` can be used independently for balance display without
  conflating it with status logic.

**Cons:**
- Each invoice now reads its own `payment_applications` relationship when
  `update_status()` is called. With lazy loading, this is one extra query per invoice
  during a refresh. For a client with 10 invoices, that is 10 extra queries instead of 1.
  (Mitigable with eager loading in `refresh_client_invoice_statuses()`.)
- Tests in `TestUpdateStatus` all pass `client_balance` directly; they need updating.

---

### Approach 3 — Explicit Payment Assignment

**How it works:**
- Remove automatic FIFO allocation.
- On the payment form, user selects which invoices to apply the payment to and enters
  amounts manually.
- `PaymentApplication` rows are still created, but by the user's explicit choice.
- `update_status()` would use per-invoice `balance_due` (same as Approach 2).

**UI change:** `payment_form.html` gains a table of outstanding invoices with amount
input fields. JavaScript validates that applied amounts don't exceed payment total.

**Pros:**
- Maximum user control over payment allocation.
- Clear audit trail — each application represents a conscious user decision.
- Handles edge cases (disputes, partial payment agreements) naturally.

**Cons:**
- More user effort per payment — critical friction for 50+ weekly clients.
- Payment entry currently takes seconds; explicit assignment would require a form with
  up to 10+ invoices, totals, and validation.
- Does not eliminate the need for FIFO as a default (could offer it as a "fill
  automatically" button, adding complexity).
- Larger scope change: form UI, validation, JavaScript, and possibly a new route.

**Verdict:** Useful as an enhancement after Approach 2, not a replacement. FIFO handles
the common case; explicit assignment handles exceptions.

---

### Approach 4 — Credit Memo / Escrow System

**How it works:**
- Payments go into a client "escrow" balance rather than being applied directly to invoices.
- Invoices are marked paid when the escrow balance is explicitly drawn down against them
  (either automatically on creation or via a user action).
- Credit memos (separate from payments) can be issued for adjustments, refunds, and
  disputes without creating `Expense` records.

**New concepts needed:**
- `ClientCredit` model: amount, source (payment, adjustment, refund), date
- Draw-down logic: create `CreditApplication` (similar to `PaymentApplication`)
- Credit memo UI: create, view, apply, reverse
- Potentially a "settle invoice" action separate from recording a payment

**Pros:**
- Handles pre-payments and seasonal retainers naturally (common in landscaping).
- Clear separation between cash received and invoice settlement.
- Credit memos are a standard accounting pattern.

**Cons:**
- Significant scope: 1+ new models, new routes, new templates.
- Breaks the current simple "record payment → FIFO applies" workflow that works well
  for the primary use case.
- Does not fix the immediate bug — the bug is in status logic, not payment recording.
- Overkill for a 50-client operation with straightforward weekly mowing invoices.

**Verdict:** Not recommended at this time. Could be revisited if pre-payment or
seasonal retainer billing becomes a requirement.

---

## 4. Recommended Implementation Path (Approach 2)

### Summary

Change `update_status()` to use per-invoice `balance_due` instead of global client balance.
Update callers to drop the argument. Update affected tests.

**Scope:** ~30 lines changed across 3 files. No schema changes. No new models.

---

### Implementation Note — SQLAlchemy Lazy-Load Cache

During implementation, a secondary issue was discovered: `Invoice.amount_paid` was
originally `sum(pa.amount_applied for pa in self.payment_applications)`. After FIFO
creates PaymentApplication rows and flushes them, the already-loaded `payment_applications`
collection on the in-memory Invoice object is stale and does not include the new rows.
`update_status()` would then read `amount_paid = 0` and mark the invoice `unpaid`.

The fix: change `amount_paid` to a live DB query (SUM via `db.session.query`), so it
always reflects the current session state regardless of cached relationship collections.
This also benefits `apply_payment_fifo`'s `invoice.balance_due` check, which now always
reads fresh applied amounts on each loop iteration.

---

### Step 1 — Rewrite `Invoice.update_status()` in `models.py`

**File:** `models.py:177`

Remove the `client_balance` parameter. Use `self.balance_due` and `self.amount_paid`.

```python
def update_status(self):
    """Update status based on this invoice's own balance_due. Does not touch void."""
    if self.status == 'void':
        return
    if self.balance_due <= 0.005:
        self.status = 'paid'
        if not self.paid_at:
            self.paid_at = datetime.utcnow()
    elif self.amount_paid > 0:
        self.status = 'partial'
        self.paid_at = None
    else:
        self.status = 'unpaid'
        self.paid_at = None
```

**Why `amount_paid > 0` for partial:** `balance_due` alone can't distinguish `unpaid`
from `partial` — both have a positive balance. `amount_paid` (sum of PAs) is 0 only
when no payment has been applied.

**Rounding:** `balance_due` rounds to 2 decimal places. The `<= 0.005` guard handles
floating-point residuals from FIFO arithmetic.

---

### Step 2 — Update `refresh_client_invoice_statuses()` in `app.py`

**File:** `app.py:44`

```python
def refresh_client_invoice_statuses(client):
    """Recalculate status for all non-void invoices using each invoice's own balance."""
    for invoice in client.invoices.filter(Invoice.status != 'void').all():
        invoice.update_status()
```

Remove the `balance = client.account_balance()` call. Drop the argument from the
`update_status()` call.

**Performance note:** If a client has many invoices, consider eager-loading
`payment_applications` in the query to avoid N+1 queries:

```python
from sqlalchemy.orm import joinedload

def refresh_client_invoice_statuses(client):
    invoices = client.invoices.filter(Invoice.status != 'void').options(
        joinedload(Invoice.payment_applications)
    ).all()
    for invoice in invoices:
        invoice.update_status()
```

---

### Step 3 — Verify All Callers of `refresh_client_invoice_statuses()`

These locations call `refresh_client_invoice_statuses()` and require no change to their
call signature (function takes `client`, not `client_balance`):

| Location | Current call |
|----------|-------------|
| `app.py:76` | `refresh_client_invoice_statuses(payment.client)` |
| `app.py:84` | `refresh_client_invoice_statuses(invoice.client)` |
| `app.py:107` | `refresh_client_invoice_statuses(invoice.client)` |
| `app.py:654` | `refresh_client_invoice_statuses(invoice.client)` |

No changes needed at these call sites.

---

### Step 4 — Update Tests in `test_invoice_status.py`

**`TestUpdateStatus` (10 tests) — complete rewrite of fixture usage:**

Current tests call `inv.update_status(client_balance)` with a raw number. After the
change, `update_status()` takes no argument and reads `self.balance_due`, which depends
on `PaymentApplication` rows.

For each test, replace the `client_balance` argument with actual `PaymentApplication`
rows that produce the intended `balance_due`:

| Current test | Old call | New setup |
|-------------|---------|----------|
| `test_exact_zero_balance_is_paid` | `inv.update_status(0)` | PA covering full subtotal |
| `test_credit_balance_is_paid` | `inv.update_status(-50)` | PA covering subtotal + extra (credit handled at client level) or just cover subtotal |
| `test_partial_balance_is_partial` | `inv.update_status(50)` | PA of $50 on $100 invoice |
| `test_balance_equals_subtotal_is_unpaid` | `inv.update_status(100)` | No PA |
| `test_balance_exceeds_subtotal_is_unpaid` | `inv.update_status(150)` | No PA |
| `test_void_invoice_is_not_changed` | `inv.update_status(0)` | No change needed |
| `test_paid_at_*` (3 tests) | argument-based | PA-based |

**`TestRefreshClientInvoiceStatuses` — update expected statuses:**

The test at line 143–150 currently asserts:
```python
# balance = $150, inv_a subtotal = $100, inv_b subtotal = $200
assert inv_a.status == 'unpaid'   # ← currently expected (bug behavior)
assert inv_b.status == 'partial'
```

After the fix, this test needs to either:
1. Add PaymentApplications so `inv_a.amount_paid` and `inv_b.amount_paid` are set
   correctly, then assert the correct statuses, OR
2. Simply call `apply_payment_fifo()` to exercise the full flow and assert the
   corrected behavior.

**New test to add:**
```python
def test_fully_paid_invoice_not_partial_when_client_has_other_outstanding(
        self, client_obj, make_invoice, make_payment):
    """Core regression test for the global-balance bug."""
    inv_a = make_invoice(client_obj, 100)
    inv_b = make_invoice(client_obj, 100)
    payment = make_payment(client_obj, 150)
    apply_payment_fifo(payment)
    assert inv_a.status == 'paid'     # fully covered by FIFO
    assert inv_b.status == 'partial'  # $50 remaining
```

---

### Step 5 — Verify No Regressions

After implementing, run the full test suite:

```bash
python -m pytest test_invoice_status.py -v
```

Manually verify in the running app:
1. Create client with Invoice A ($100) and Invoice B ($100).
2. Record a $150 payment — FIFO should fully cover A and partially cover B.
3. Invoice A detail should show `paid`. Invoice B should show `partial`.
4. Client AR balance should show $50 owing.
5. Aging report should include Invoice B but not Invoice A.

---

## 5. Open Questions

These decisions do not block Approach 2 but should be resolved before future iterations.

| Question | Current behavior | Options |
|----------|-----------------|---------|
| **`paid_at` on regression** | Cleared when status goes from `paid` → `partial` (e.g., after voiding another payment). | Keep current (cleared on regression) vs. retain first paid_at as "first settled" date. |
| **`auto_apply_credits()` opt-in?** | Always applies credits automatically on invoice creation. | Keep automatic vs. prompt user ("You have $X credit — apply to this invoice?"). |
| **Manual status override?** | No override capability. | Add an admin-only "force mark paid" option for dispute resolution edge cases. |
| **Approach 3 for edge cases?** | FIFO only. | After Approach 2 lands, consider an "override allocation" UI on the payment detail page to manually reassign a PA. |
| **Eager loading in refresh?** | `payment_applications` loaded lazily per invoice. | Switch to `joinedload` in `refresh_client_invoice_statuses()` to avoid N+1 queries on clients with many invoices. |
