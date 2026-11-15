# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Clips Admin

Landscaping business management system for tracking clients, services, invoicing, payments, expenses, payroll, and financial reporting. Built for a lawn care operation managing 50+ weekly clients.

---

## Commands

```bash
python app.py                          # Run locally (development mode) — http://127.0.0.1:5000
ADMIN_PASSWORD=... python scripts/init_db.py   # Schema + admin + expense categories (--force to wipe)
python scripts/seed_mock_data.py               # Drop and recreate DB with full mock data (dev only)
python scripts/add_mock_clients.py             # Append 7 clients to existing DB (no drop)
python scripts/clear_db.py                     # Drop all tables, recreate schema + admin user (SQLite only)

pip install -r requirements-dev.txt    # pytest, ruff, pip-audit (once per venv)
python -m pytest -q                    # Full suite in tests/ (~95 s). Money logic only: pytest tests/test_invoice_status.py
python -m ruff check .                 # Lint (rules in pyproject.toml); CI fails on findings
python -m pip_audit -r requirements.txt   # Dependency vulnerability scan; CI runs it too
```

CI (`.github/workflows/ci.yml`) runs ruff, pytest, and pip-audit on every push to `main` and every PR.

Default dev login: `admin` / `clips2026` — development only. Production has no default password (see Environment Variables).

`APP_ENV` defaults to `production`. `python app.py` and the dev scripts switch themselves to `development`; anything else that imports `app` (gunicorn, `flask run`) gets production rules: `SECRET_KEY` and `DATABASE_URL` required at startup, secure cookies on, no default admin password.

---

## Files to Read First

| File | Why |
|------|-----|
| `models.py` | All DB schema, relationships, key computed properties |
| `services.py` | All business logic: FIFO payment, auto-credit, financial report helpers |
| `tz.py` | Timezone utilities — always use `eastern_now()` / `eastern_today()` here instead of bare `datetime.now()` / `date.today()` anywhere in the codebase |
| `utils.py` | Safe-redirect check for `next=` params, portal/staff setup-token helpers (password-hash nonce), admin password minimum, `parse_amount()` for every money/hours form field |
| `tests/conftest.py` | Each test gets a fresh `create_app()` on an in-memory SQLite DB; `http_client` is a signed-in admin, `anon_client` is not; `make_*` factories for every model |
| `app.py` | App factory (`create_app()`), all config, inline DB migrations |
| `routes/` | Blueprint modules — one per domain |
| `docs/deployment.md` | Deployment reference: Lightsail, env vars, systemctl, deploy steps |

---

## Architecture

### App Factory

`create_app()` in `app.py`:
1. Reads all config from env vars (falls back to safe dev defaults)
2. Initializes extensions from `extensions.py` (db, csrf, mail, limiter, login_manager)
3. Calls `register_blueprints(app)` from `routes/__init__.py`
4. Runs `init_database()` — `db.create_all()` + a set of inline `ALTER TABLE` one-time migrations (safe to re-run)

### Database

No `DATABASE_URL` → SQLite (`clips.db`). With `DATABASE_URL` → PostgreSQL. Auto-corrects `postgres://` → `postgresql://` prefix.

### Blueprints

All domain logic is split into blueprints under `routes/`. Each maps 1:1 to a domain:

| Blueprint | Prefix | Domain |
|-----------|--------|--------|
| `core.py` | (none) | Dashboard, auth, financials, error handlers |
| `landing.py` | `/` | Public marketing page, quote form |
| `portal.py` | `/portal` | Customer portal, Stripe payments, webhook |
| `staff.py` | `/staff` | Employee portal, timesheet CRUD |
| `settings.py` | `/settings` | Gmail OAuth connect/disconnect |
| `clients.py` | `/clients` | Client CRUD + client APIs |
| `services_bp.py` | `/services` | Service CRUD |
| `routes_bp.py` | `/mowing-routes` | Mowing route CRUD |
| `invoices.py` | `/invoices` | Invoice CRUD + bulk actions |
| `payments.py` | `/payments` | Payment CRUD |
| `expenses.py` | `/expenses` | Expense CRUD + split logic |
| `vendors.py` | `/vendors` | Vendor CRUD |
| `payroll.py` | `/payroll` | Payroll + employee CRUD |

### Email

`gmail.py` → `send_email()`: tries Gmail API (OAuth2 tokens from `AppSetting`), falls back to SMTP. Used by all 6 email send sites.

### CSS

`static/style.css` — admin/portal/staff styles (~49 KB).
`static/landing.css` — landing page only, separate design system (no Bootstrap).

---

## Key Business Logic

### Timezone Rule

Always use `eastern_now()` and `eastern_today()` from `tz.py`. Never call `datetime.now()`, `datetime.utcnow()`, or `date.today()` directly anywhere in the codebase.

### Payment Application — FIFO

```
apply_payment_fifo(payment):                    # services.py
  remaining = payment.amount
  for invoice in client's non-void invoices, oldest first:
    apply = min(remaining, invoice.balance_due)
    create PaymentApplication(payment, invoice, apply)
    remaining -= apply
  refresh_client_invoice_statuses(client)
  return remaining  # unapplied credit
```

### Auto-Apply Credits

```
auto_apply_credits(invoice):                    # services.py
  for each client payment with unapplied balance, oldest first:
    apply = min(balance, payment.amount_unapplied)
    create PaymentApplication(payment, invoice, apply)
  refresh_client_invoice_statuses(client)
```

### Invoice Status

```
Invoice.update_status():                        # models.py
  if status == 'void': return                   # void is permanent
  if balance_due <= 0.005: → paid               # paid_at set once
  elif amount_paid > 0:    → partial            # paid_at cleared
  else:                    → unpaid             # paid_at cleared

  balance_due = round(subtotal - amount_paid, 2)
  amount_paid = SUM(PaymentApplication.amount_applied) for this invoice
```

`refresh_client_invoice_statuses(client)` calls `update_status()` on every non-void invoice for a client. Always call it after mutating PaymentApplications — never update statuses inline.

### Client Balance Formulas

```
account_balance    = SUM(invoice.subtotal, non-void) - SUM(payments.amount)
                     Positive = owes money | Negative = has credit

unapplied_credit   = SUM(payments.amount) - SUM(payment_applications.amount_applied)
```

`client_balances()` in `services.py` computes `account_balance` for all clients in 2 grouped queries — use this on list pages instead of calling `Client.account_balance()` in a loop.

### Dashboard Financial Metrics

```
monthly_revenue  = SUM(services this month)
monthly_profit   = monthly_revenue - monthly_payroll - (monthly_expenses - monthly_refunds)
```

Full 12-month breakdown via `get_monthly_financials(year)` in `services.py`.

---

## Database Schema

Every table has `id` (Integer PK) and `created_at` (DateTime, auto). Non-obvious columns and rules only:

**`clients`** — `active` (True, soft-delete); `route_id`/`route_position` for mowing route membership; `password_hash`/`portal_invite_*` for customer portal.

**`invoices`** — `status` = `unpaid` | `partial` | `paid` | `void`. `sent_at` written on every successful email. Status is derived from `PaymentApplication` rows, not stored independently.

**`invoice_line_items`** — `service_id` nullable (null = manual entry line item).

**`payment_applications`** — join between `payments` and `invoices`. Enables partial payments split across multiple invoices.

**`time_entries`** — unique `(employee_id, work_date)`. Status: `draft` → `submitted` → `approved` | `rejected`. Approved entries get a `payroll_id` FK set.

**`expenses`** — `amount` always stored positive; `is_refund` negates it via `effective_amount` property. An expense has either a `client_id` OR `ExpenseAllocation` rows (split), never both. Client attribution only valid for: Equipment Rental, Materials, Other categories.

**`expense_allocations`** — splits one expense across N clients. Modeled like `PaymentApplication`. `_save_allocations()` in `routes/expenses.py` rebuilds rows on every save.

**`app_settings`** — key/value store for instance config. Used for encrypted Gmail OAuth tokens. `AppSetting.get(key)` / `AppSetting.set(key, value)`.

**Expense categories (seeded, 7):** Equipment Purchase, Equipment Rental, Equipment Maintenance, Materials, Fuel, Overhead, Other.

---

## Route Patterns

Admin CRUD follows: `GET /resource` (list), `GET /resource/<id>`, `GET/POST /resource/<id>/edit`, `POST /resource/<id>/delete`. Services, payments, expenses, and payroll use grid-style quick-add pages instead of single-entry `/new` routes.

Non-obvious routes:

| Route | Purpose |
|-------|---------|
| `POST /request-quote` | Public quote form → emails `QUOTE_INBOX`, Ohio addresses only. Honeypot, signed `form_ts` age token, per-field scrub + length caps, 5/hr + 2/min |
| `GET/POST /services/quick-add` | Two-tab page: grid for any service type + mowing checklist |
| `GET/POST /invoices/quick-create` | Batch-create invoices for all clients with uninvoiced services; emails PDFs |
| `GET/POST /invoices/new` | Single-client invoice creation (accessible but demoted from sidebar) |
| `POST /invoices/bulk-email\|bulk-void\|bulk-delete` | Bulk invoice actions |
| `GET/POST /payments/quick-add` | Grid-style bulk payment entry; FIFO-applies each row |
| `GET/POST /expenses/quick-add` | Grid-style bulk expense entry; all-or-nothing |
| `POST /portal/webhook/stripe` | Stripe webhook (no auth, signature verified) |
| `GET/POST /settings/account` | Admin password change (12-char minimum, current password required, 5/min) |
| `POST /staff/timesheet/clear-day` | AJAX: delete single day entry → JSON |
| `POST /api/mowing-routes/move-client` | Drag-and-drop reorder within/between routes |

### JSON APIs

| Route | Returns |
|-------|---------|
| `/api/client/<id>/rate` | `{rate: float}` |
| `/api/client/<id>/uninvoiced-services` | `[{id, date, type, description, amount}]` |
| `/api/client/<id>/balance` | `{balance, unapplied_credit}` |
| `/api/vendors/search?q=` | `[{id, name}]` |
| `/api/expense-categories` | `[{id, name}]` |

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `APP_ENV` | `production` (default) or `development`. Governs every fallback below. |
| `DATABASE_URL` | PostgreSQL URI. **Required in production**; development falls back to `instance/clips.db` |
| `SECRET_KEY` | Session/token signing key. **Required in production**; development uses a fixed dev key |
| `SESSION_COOKIE_SECURE` | Defaults `true` in production, `false` in development; override only for an HTTP-only test box |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Gmail OAuth2 |
| `FERNET_KEY` | Encrypts stored OAuth tokens in `app_settings` |
| `STRIPE_SECRET_KEY` / `STRIPE_PUBLISHABLE_KEY` / `STRIPE_WEBHOOK_SECRET` | Customer portal payments |
| `BASE_URL` | Used in background threads (no request context) |
| `MARKETING_URL` | Linked in invoice emails |
| `QUOTE_INBOX` | Destination for landing page quote form submissions |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | First-boot admin when the users table is empty. Production creates no admin without `ADMIN_PASSWORD` (logs an error instead); development falls back to `admin` / `clips2026` |
| `PAYMENT_NAME` | Name printed on invoices (check payee, header) |
| `VENMO_HANDLE` | Venmo handle shown in "Ways to Pay" on invoices |
| `CASHAPP_HANDLE` | Cash App handle shown on invoices |
| `ZELLE_PHONE` | Zelle phone number shown on invoices |
| `BUSINESS_PHONE` | Business phone on invoices and landing page JSON-LD |
| `BUSINESS_STREET` | Street address line on printed invoices |
| `BUSINESS_CITY_STATE_ZIP` | City/state/ZIP line on printed invoices |

---

## Git & Deploy

Feature branches → PR into `main`. On the Lightsail server after `git pull main`:

```bash
bash scripts/deploy.sh    # pip install -r requirements.txt + sudo systemctl restart clips
```

See `docs/deployment.md` for server commands, service name, and full deployment notes.

---

## Common Issues

**App refuses to start with "SECRET_KEY is not set" / "DATABASE_URL is not set"** — `APP_ENV` defaults to `production`. On a server, set the variable in `/etc/clips/env` (see `docs/deployment.md`). Locally, run via `python app.py` or a dev script, which switch to `development`.

**Portal/staff setup link says "no longer valid"** — setup tokens carry a nonce derived from the account's current password hash (`utils.make_setup_token`). Any link issued before the password was set or changed is dead by design. Use "email me a link" on the login page.

**Cannot permanently delete a client** — hard delete is refused while any service, invoice, payment, expense, or allocation references the client. Deactivate instead. Services linked to an expense are likewise undeletable until the expense is unlinked.

**`postgres://` prefix** — auto-corrected in `app.py`. No action needed.

**Invoice number collision** — `generate_invoice_number()` has a 3-attempt retry loop with `IntegrityError` catch.

**Cannot delete a service** — it's linked to an `InvoiceLineItem`. Void or delete the invoice first.

**Quote form says "That form expired"** — the landing page's `form_ts` token is good for 6 hours (`MAX_FORM_SECONDS` in `routes/landing.py`). A tab left open overnight has to be reloaded. Note `WTF_CSRF_TIME_LIMIT = None` means CSRF is *not* what expired.

**CSRF 400 on bulk-entry grids** — `WTF_CSRF_TIME_LIMIT = None` is set app-wide to prevent this.

**Floating-point rounding** — `update_status()` uses `PAYMENT_TOLERANCE = 0.005` and `balance_due` rounds to 2 decimal places.

---

## Recent Changes

One line per change, newest first. Trim entries older than ~3 months once their behavior is reflected above.

- **2026-11-15** Public Layout — dev/setup scripts moved to `scripts/` (each puts the repo root on `sys.path`); `build.sh` → `scripts/deploy.sh` (cds to repo root); `aws_notes.md` → `docs/deployment.md`; `CLIPS_FORK_GUIDE.md` → `docs/customizing.md`. Added `.env.example` and `.gitattributes`, rewrote `README.md`. Removed `documents/`, `.claude/`, and the Claude Code workflow. No migration.

- **2026-11-15** Public Repo Follow-up — `docs/customizing.md` audit table no longer quotes the old payee name and Venmo/Cash App handles. `.github/workflows/claude.yml` now has an `if:` guard: comments must mention `@claude` from an OWNER/MEMBER/COLLABORATOR, and PR runs only fire for same-repo branches by those roles. No migration.

- **2026-10-13** Safe Amount Parsing — new `utils.parse_amount()` replaces bare `float()` on every money and hours form field (payments, services, expenses + allocations, payroll, client/employee rates). Rejects NaN/infinity (which `float()` accepts, slip past `<= 0` guards, and Postgres would store) and values over `MAX_AMOUNT` ($1M) / `MAX_HOURS` (24); rounds half-up to cents. Also added missing positive checks to service edit and payroll new/edit, and non-negative checks to default rates. Columns stay `Float`. No migration.

- **2026-10-13** Public Repo Prep — Parameterized personal info (name, address, phone, payment handles) into 6 new env vars (`PAYMENT_NAME`, `VENMO_HANDLE`, `CASHAPP_HANDLE`, `ZELLE_PHONE`, `BUSINESS_PHONE`, `BUSINESS_STREET`, `BUSINESS_CITY_STATE_ZIP`); cleared hardcoded `MARKETING_URL` and `QUOTE_INBOX` defaults; updated 5 templates; deleted superseded `docs/aws-lightsail-setup.txt`; added MIT `LICENSE` and public `README.md`; added `.claude/settings.local.json` to `.gitignore`. No migration.
- **2026-09-10** Fix Service Grid Blank Row — `/services/quick-add` grid mode rejected every save with "Row N: choose a client". The server's blank-row test included `service_type`, whose select has no empty option, so the trailing blank row the grid auto-appends was never skipped and no row could ever save. The test now mirrors the grid JS's `MEANINGFUL` set (`client_id`/`amount`/`description`); the payment, expense, and payroll grids were already correct. `test_grid_blank_rows_ignored` omitted `service_type` and so passed against the bug — repaired, plus a new `test_grid_trailing_blank_row_ignored`. No migration.
- **2026-09-10** Service Rate Autofill — the Other Services grid (`service_quick_add.html`) and the service edit page (`service_form.html`) now rewrite amount + description whenever the client or type changes: mowing gives the client's `default_mowing_rate` and "Weekly mowing", any other type blanks both. Previously they only filled blanks, so switching clients stranded the old rate. The edit page no longer runs the autofill on page load, which had been overwriting a saved mowing amount with the client's current rate. A client's default rate is a starting value for new work, never retroactive; `test_raising_the_rate_does_not_reprice_history` locks that in. No migration.
- **2026-09-10** Quote Form Hardening — `POST /request-quote` now scrubs Unicode control/format characters (CRLF, zero-width, bidi) from every field, caps each field by `FIELD_LIMITS`, checks email and phone shape, and coerces `service` to the `SERVICE_OPTIONS` allow-list the `<select>` is rendered from. New signed `form_ts` token (`make_quote_token()`) measures fill time: missing, forged, or faster than `MIN_FORM_SECONDS` (3 s) is dropped silently; past `MAX_FORM_SECONDS` (6 h) asks them to resend. Added a `2 per minute` burst limit alongside the hourly one, app-wide `MAX_CONTENT_LENGTH` of 1 MB, and a 413 handler in `routes/core.py`. No migration.
- **2026-09-10** Ohio-Only Quote Requests — `POST /request-quote` emails `QUOTE_INBOX` only when the state field reads OH/Ohio or the ZIP starts 43/44/45 (`_is_ohio()` in `routes/landing.py`); out-of-area submissions get the same success message, are not emailed, and are not logged. `TestQuoteRequest` in `tests/test_routes.py`. No migration.
- **2026-09-09** Test Suite + CI (audit Phase B) — `tests/` restored from the June-deleted suite and repaired for the grid routes: `test_invoice_status.py` (FIFO, auto-credit, void re-application, status transitions), `test_routes.py` (every blueprint), `test_security.py` (startup checks, redirects, setup tokens, account page, portal/staff auth), `test_pdf.py` (skips without WeasyPrint native libs). 244 tests. `pyproject.toml` holds pytest + ruff config; `requirements-dev.txt`; `.github/workflows/ci.yml` runs ruff + pytest + pip-audit. Ruff import-order autofix touched most modules. `cryptography` → 50.0.1 and `weasyprint` → 70.0 to clear pip-audit findings. No migration.
- **2026-09-09** Production Hardening (audit Phase A) — `APP_ENV` (default `production`) makes `SECRET_KEY`/`DATABASE_URL` required and secure cookies default-on; no default admin password outside development; `GET/POST /settings/account` password change + sidebar "Account" link; new `utils.py`: `is_safe_redirect_target` fixes the `//host` open redirect in portal/staff login, `make_setup_token`/`setup_token_matches` bind setup links to the current password hash; client hard-delete and service delete refuse while linked rows exist; baseline security headers (`nosniff`, `SAMEORIGIN`, HSTS when secure); `requirements.txt` fully pinned (+ `requests`, Flask 3.1.3); `scripts/init_db.py` takes `ADMIN_USERNAME`/`ADMIN_PASSWORD`, refuses non-empty DBs without `--force`; `scripts/clear_db.py` refuses non-SQLite without `--yes`; `docs/deployment.md` rewritten around `EnvironmentFile` + rotation checklist; `docs/aws-lightsail-setup.txt` marked superseded. No migration.
- **2026-09-04** Payroll Grid Quick-Add — `GET/POST /payroll/quick-add` + `payroll_quick_add.html`: grid-style bulk payroll entry matching payment/expense pattern. Employee select autofills rate from `data-rate`. Sidebar and payroll list header link updated. No migration.
- **2026-09-04** Consolidate Entry Forms — Removed single-entry `/services/new`, `/payments/new`, `/expenses/new` routes; each domain now uses one grid-style quick-add page. Service quick-add rewritten with two tabs (grid for any type + mowing checklist). Sidebar consolidated to one link per domain. Invoice sidebar points to quick-create; single-create demoted to secondary link. No migration.
- **2026-08-05** Landing Page Redesign — `landing.html` rewritten on standalone `static/landing.css` ("Cut & Canopy": cream canvas, Bricolage Grotesque, chartreuse accent); Bootstrap dropped from landing only; icons inlined as SVG. New `POST /request-quote` (5/hr, honeypot, emails `QUOTE_INBOX`) + `email_quote_request.html`. Responsive WebP variants in `static/landingPage/`. SEO/OG/JSON-LD added. 429/landing-CSRF error handlers in `routes/core.py`. No migration.
- **2026-07-28** Bulk Payment Entry — `GET/POST /payments/quick-add` + `payment_quick_add.html`: same grid as expense quick-add. Client `<option>`s carry `data-balance` from `client_balances()` (2 grouped queries) to show balance hint and autofill amount. No migration.
- **2026-07-28** Bulk Expense Entry — `GET/POST /expenses/quick-add` + `expense_quick_add.html`: spreadsheet-style grid, all-or-nothing server-side. `WTF_CSRF_TIME_LIMIT = None` set app-wide. CSS §28 `.quick-grid` shared by both grids. No migration.
- **2026-07-27** Sidebar Account Menu — sidebar footer replaced by clickable user card opening a Bootstrap dropup (Settings + Sign out); scroll moved to `.sidebar-nav` so footer stays pinned. No migration.
- **2026-07-21** OAuth2 Gmail Migration — `gmail.py`, `routes/settings.py`, `AppSetting` model, encrypted token storage. All 6 email send sites migrated. New table: `app_settings`.
- **2026-06-14** Outstanding Invoices Breakdown — `get_outstanding_invoices()` in `services.py` lists older unpaid invoices on all 4 invoice views. No migration.
- **2026-06-10** Start/End Time Entry for Timesheets — `start_time`/`end_time` on `time_entries`; hours auto-derived, rounded to 15 min. ALTER TABLE migration.
- **2026-06-07** Employee Portal & Timesheet System — `staff` blueprint, `TimeEntry` model (draft→submitted→approved/rejected), `/timesheets` admin approval. New table + employee portal columns.
- **2026-04-26** Landing Page & Customer Portal — public `/`, dashboard moved to `/dashboard`; `portal` blueprint, Stripe Checkout, portal columns on `clients`.
- **2026-04-18** Mowing Routes — `Route` model, `routes_bp` at `/mowing-routes`; `route_id`/`route_position` on `clients`.

---

## Self-Updating Guide

Always add a one-line entry to **Recent Changes** for every meaningful change.

| Change Type | Sections to Update |
|------------|-------------------|
| New feature | Routes (if new route), Recent Changes |
| New model/table | Database Schema, Recent Changes |
| New business logic | Key Business Logic, Recent Changes |
| Bug fix | Common Issues (if recurring), Recent Changes |
| New env var | Environment Variables, Recent Changes |
| New template | (no section needed unless it introduces a new concept) |
