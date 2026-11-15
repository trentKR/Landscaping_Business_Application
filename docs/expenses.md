# Expenses System

## Overview

The expense tracking system records business costs (materials, fuel, equipment, etc.) and optional vendor refunds. Expenses feed into the dashboard's monthly profit calculation and the financials report. They can optionally be linked to a specific client or service job for profitability tracking.

---

## Models

### `Expense` (`models.py` line 347)

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer | PK |
| `expense_date` | Date | Indexed, default today |
| `amount` | Float | **Always stored positive**, even for refunds |
| `category_id` | Integer | FK → `expense_categories`, required |
| `vendor_id` | Integer | FK → `vendors`, nullable |
| `payment_method` | String(20) | `cash` \| `check` \| `credit_card` \| `debit_card` \| `bank_transfer` \| `other` |
| `description` | String(250) | Default `''` |
| `receipt_path` | String(500) | Nullable — column exists but unused |
| `service_id` | Integer | FK → `services`, nullable — links to a specific job |
| `client_id` | Integer | FK → `clients`, nullable — links to a client |
| `is_refund` | Boolean | Default `False` — negates amount in all calculations |
| `created_at` | DateTime | Auto |

**Key property:**
```python
@property
def effective_amount(self):
    return -self.amount if self.is_refund else self.amount
```
Use `effective_amount` (not `amount`) in any sum that should treat refunds as negative.

**Payment methods list:** `Expense.PAYMENT_METHODS` — use this for form dropdowns, not a hardcoded list.

---

### `ExpenseCategory` (`models.py` line 325)

> ⚠️ **This section is out of date.** The model is now **flat** — `ExpenseCategory` has
> only `id`, `name`, `created_at`. There is no `parent_id`, no `subcategories`, and no
> `display_name`; `/api/expense-categories` returns just `{id, name}`. The seeded set is
> the 7 flat categories in `scripts/init_db.py` (Equipment Purchase, Equipment Rental, Equipment
> Maintenance, Materials, Fuel, Overhead, Other), not the tree listed below. Use
> `cat.name` everywhere. The hierarchy described here was never restored after the
> refactor — treat the rest of this section as historical.

Two-level hierarchy: parent categories hold child subcategories via self-referential FK.

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer | PK |
| `name` | String(100) | Required |
| `parent_id` | Integer | FK → self, nullable — `None` = top-level category |
| `created_at` | DateTime | Auto |

**Key property:**
```python
@property
def display_name(self):
    if self.parent:
        return f'{self.parent.name} > {self.name}'  # e.g. "Materials > Mulch"
    return self.name
```

**Relationships:**
- `parent` — M:1 back to parent category
- `subcategories` — backref list of child categories
- `expenses` — dynamic queryset of all expenses in this category

**Seeded categories:**
```
Materials
  ├── Mulch
  ├── Fertilizer
  ├── Grass Seed
  └── Plants & Shrubs
Equipment Purchase
Equipment Maintenance
  ├── Mower Parts
  ├── Trimmer Repair
  └── Blade Sharpening
Fuel
Insurance
Supplies
Other
```

---

### `Vendor` (`models.py` line 296)

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer | PK |
| `name` | String(120) | Required |
| `contact_name` | String(120) | Default `''` |
| `phone` | String(20) | Default `''` |
| `email` | String(120) | Default `''` |
| `address` | String(250) | Default `''` |
| `notes` | Text | Default `''` |
| `active` | Boolean | Default `True` — soft delete |
| `created_at` | DateTime | Auto |

**Soft delete:** `vendor.active = False` — deactivated vendors do not appear in dropdowns or the vendor list, but their expenses remain intact.

**Methods:**
- `total_spent()` — `SUM(expense.amount)` for this vendor (does not net refunds)
- `expense_count()` — count of expenses for this vendor

---

## Routes (`routes/expenses.py`)

> The line numbers below (and in the sections that follow) predate the blueprint
> refactor and point at the old monolithic `app.py`. Expense and vendor routes now
> live in `routes/expenses.py` and `routes/vendors.py`.

### Expense Routes

| Route | Purpose |
|-------|---------|
| `GET /expenses` | List with filters; total = `SUM(effective_amount)` |
| `GET/POST /expenses/new` | Create a single expense (owns client links + splits) |
| `GET/POST /expenses/quick-add` | Bulk grid — many expenses in one submit (see below) |
| `GET /expenses/<id>` | Detail view |
| `GET/POST /expenses/<id>/edit` | Edit expense |
| `POST /expenses/<id>/delete` | Hard delete — no soft delete |
| `GET /expenses/export` | CSV export — respects `date_from`/`date_to` params |

### Vendor Routes

| Route | Lines | Purpose |
|-------|-------|---------|
| `GET /vendors` | 1176–1187 | List, filtered by `active=True`, search by name/contact |
| `GET/POST /vendors/new` | 1190–1206 | Create vendor |
| `GET /vendors/<id>` | 1209–1216 | Detail: last 50 expenses + `total_spent()` |
| `GET/POST /vendors/<id>/edit` | 1219–1234 | Edit vendor (includes `active` toggle) |
| `POST /vendors/<id>/delete` | 1237–1244 | Soft deactivate (`active = False`) |

### API Endpoints

| Route | Lines | Returns |
|-------|-------|---------|
| `GET /api/vendors/search?q=` | 1251–1262 | `[{id, name}]` — autocomplete, min 1 char, limit 10 |
| `GET /api/expense-categories` | 1265–1277 | `[{id, name, display_name, parent_id}]` — all categories |

---

## Quick Add — bulk entry (`expense_quick_add`)

A spreadsheet-style grid at `/expenses/quick-add` for entering a stack of receipts in
one submit. Deliberately narrower than `/expenses/new`: **no client attribution and no
splits** — those stay on the single-expense form.

**Form encoding.** One hidden `row_index` per live row acts as the manifest; every other
field is suffixed with that index (`amount_<i>`, `category_id_<i>`, `is_refund_<i>`, …).
Parallel `field[]` arrays are *not* usable here because an unchecked `is_refund` doesn't
submit, which would desynchronize the arrays. The server reads
`request.form.getlist('row_index')` to learn which rows are live, so JS never has to
renumber anything when a row is deleted. `MAX_QUICK_ADD_ROWS = 200` caps a single POST.

> The hidden `row_index` input must stay inside a `<td>`. As a direct child of `<tr>` the
> HTML parser foster-parents it out of the table and it detaches from its row.

**Blank rows are skipped.** A row counts as blank when `amount`, `category_id`,
`vendor_id` and `description` are all empty — `expense_date` and `payment_method` are
excluded because the template pre-fills them, and `is_refund` alone doesn't describe an
expense. This rule is implemented twice, in `expense_quick_add()` and in `rowIsBlank()`
in `expense_quick_add.html`; **they must stay identical** or rows get silently dropped or
spuriously rejected.

**Validation.** The browser blocks submit and flags bad cells inline (`is-invalid` +
`.invalid-feedback`); the route re-validates everything from scratch as a backstop and is
**all-or-nothing** — every row is built in memory and only added to the session once all
of them are clean. Unlike `/expenses/new`, this route validates `payment_method` against
`Expense.PAYMENT_METHODS`, and whitelists `category_id`/`vendor_id` against the DB.
Those whitelists are load-bearing, not defensive: SQLite has no `PRAGMA foreign_keys=ON`
here, so an unknown ID would insert a dangling reference and break the expense list page.

**Template notes.** The row is defined once as a Jinja macro, rendered both for the
initial 5 blank rows and into a `<template id="rowTemplate">` with a literal `__INDEX__`
that JS substitutes. Never do arithmetic on the macro's `idx` — it may be that string.
The form is `novalidate` and no control carries `required`, because the permanently
present trailing blank row would otherwise block submission. Typing into the last row
auto-appends a new one that inherits the previous row's date and payment method.

---

## Filtering Logic (`expense_list`, app.py line 1000)

Filters are additive (AND). Applied in this order:

1. **Search** — `description ILIKE '%query%'`
2. **Category** — if a parent category is selected, includes that parent AND all its subcategories (`cat.id` + all `sc.id` in `cat.subcategories`)
3. **Vendor** — exact `vendor_id` match
4. **Method** — exact `payment_method` match (skipped if `'all'`)
5. **Date range** — `expense_date >= date_from` and/or `expense_date <= date_to`

Result: ordered by `expense_date DESC`, limited to **200 records**.

List total: `sum(e.effective_amount for e in expenses)` — refunds subtract from the total.

---

## Create / Edit Behavior

**`expense_new` (POST, app.py line 1049):**
```python
amount = abs(float(request.form['amount']))  # always stored positive
is_refund = 'is_refund' in request.form      # checkbox → bool
vendor_id = request.form.get('vendor_id', type=int) or None   # empty = None
service_id = request.form.get('service_id', type=int) or None
client_id = request.form.get('client_id', type=int) or None
```
After commit, flashes `"Refund of $X recorded."` or `"Expense of $X recorded."` depending on `is_refund`.

**`expense_edit` (POST, app.py line 1093):** Same field handling. Redirects to `expense_detail` on success.

**No cascade effects:** Editing or deleting an expense does not trigger any recalculations elsewhere. Expenses are independent of the invoice/payment system.

---

## CSV Export (`expense_export`, app.py line 1130)

- Filtered only by `date_from` / `date_to` (other filters not applied)
- Ordered by `expense_date ASC`
- Filename: `expenses_YYYY-MM-DD.csv`

Columns:
```
Date | Amount | Type | Category | Vendor | Payment Method | Description | Client | Job Linked
```
- `Amount` uses `effective_amount` (negative for refunds)
- `Type` = `"Refund"` or `"Expense"`
- `Category` uses `display_name` (e.g. `"Materials > Mulch"`)
- `Job Linked` = `"Yes"` if `service_id` is set, else `"No"`

---

## How Expenses Flow Into Financial Reports

### Dashboard (`app.py` lines 210–229)

```python
monthly_expenses = SUM(amount WHERE expense_date >= start_of_month AND is_refund = False)
monthly_refunds  = SUM(amount WHERE expense_date >= start_of_month AND is_refund = True)
monthly_expenses_net = monthly_expenses - monthly_refunds

monthly_profit = monthly_revenue - monthly_payroll - monthly_expenses_net
```

Dashboard also shows top expense categories this month (top 3 by amount, non-refunds only).

### Financials Page (`app.py` lines 877–944)

Expenses are queried in two separate passes per year — non-refunds and refunds — grouped by month:

```python
expense_by_month = SUM(amount) GROUP BY month WHERE is_refund = False
refund_by_month  = SUM(amount) GROUP BY month WHERE is_refund = True
net_expenses = expense_cost - refund_amount  # per month
profit = income - payroll_cost - net_expenses
```

Category breakdown for the year: `SUM(amount)` per category (non-refunds only), ordered by total descending. Used for the pie/donut chart.

---

## Templates

| Template | Purpose |
|----------|---------|
| `templates/expenses.html` | List view with filter bar, table, footer total |
| `templates/expense_form.html` | Create/edit form (shared, `expense=None` for new) |
| `templates/expense_detail.html` | Read-only detail with Edit/Delete actions |
| `templates/vendors.html` | Vendor list with search |
| `templates/vendor_form.html` | Create/edit vendor (shared) |
| `templates/vendor_detail.html` | Vendor profile + last 50 expenses |

### `expense_form.html` Notes
- Category select uses `<optgroup>` — iterates parent categories, then filters `sub.parent_id == cat.id` inline (all categories passed in flat list from route)
- Default payment method on new expense: `credit_card`
- `service_id` field is in the model and route but **not rendered in the current form** — it would need to be added manually if job-linking via the form is needed
- Vendor field is a simple `<select>` from all active vendors — not the autocomplete API

---

## Key Invariants

- `amount` is **always stored positive** — the `is_refund` flag is the sole signal for sign
- Expenses are **hard deleted** (no soft delete); vendors are soft deleted (`active = False`)
- Deleting a vendor does not delete its expenses — `vendor_id` on the expense becomes a dangling FK (vendor still exists in DB, just `active = False`)
- The `receipt_path` column exists in the DB but no upload UI or file-serving code exists
- Expenses have **no relationship to invoices or payments** — they are purely cost-tracking, separate from the A/R system
- `service_id` FK exists for job-level cost attribution but the current form does not expose it as a user-facing field
