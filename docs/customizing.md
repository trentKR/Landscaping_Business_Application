# Clips Fork-and-Rebrand Guide

**Purpose:** This file is the playbook for taking a fresh fork of the Clips landscaping admin app and rebranding it for a new landscaping company. It is dual-purpose: a checklist for the human owner, and an executable spec for Claude.

**Audience:**
- **Human:** Read sections 1–3 to understand the workflow. Hand this file to Claude when you're ready to execute.
- **Claude:** This file is your source of truth. Execute the phases in order. Do not skip Phase 0 (the owner interview). Do not invent values — ask. Do not deploy until the local app is verified in Phase 6.

---

## Table of contents

1. [How to use this file](#1-how-to-use-this-file)
2. [Prerequisites](#2-prerequisites)
3. [Workflow overview](#3-workflow-overview)
4. [Phase 0 — Owner interview](#4-phase-0--owner-interview-claude-runs-this)
5. [Phase 1 — Write `config.py`](#5-phase-1--write-configpy)
6. [Phase 2 — One-time refactor (skip if `refactor_complete = True`)](#6-phase-2--one-time-refactor)
7. [Phase 3 — Asset swap](#7-phase-3--asset-swap)
8. [Phase 4 — Service types & pricing](#8-phase-4--service-types--pricing)
9. [Phase 5 — Email tone](#9-phase-5--email-tone)
10. [Phase 6 — Boot & verify](#10-phase-6--boot--verify)
11. [Appendix A — AWS Lightsail deployment runbook](#11-appendix-a--aws-lightsail-deployment-runbook)
12. [Appendix B — Brand touchpoint inventory](#12-appendix-b--brand-touchpoint-inventory)
13. [Appendix C — Troubleshooting](#13-appendix-c--troubleshooting)

---

## 1. How to use this file

### For the human owner

1. Clone the canonical Clips repo and rename it for the new company.
2. Run `python -m venv venv && pip install -r requirements.txt` and confirm `python app.py` boots the (still unbranded) app at http://127.0.0.1:5000.
3. Open this guide alongside Claude in the new repo and say: "Run the Clips fork guide."
4. Claude will interview you (Phase 0) and execute Phases 1–6. End state: the app runs locally, rebranded.
5. Phase 7 (Appendix A) is the AWS Lightsail deployment, run as a separate session when you're ready to go live.

### For Claude

- **Start every fork session** by reading `config.py`. If it does not exist or does not contain `refactor_complete = True`, you must run Phase 2 (one-time refactor) before continuing. If it exists and `refactor_complete = True`, skip Phase 2.
- **Never skip Phase 0.** Use `AskUserQuestion` for every group of related questions. Confirm ambiguous answers before writing them to disk.
- **Never invent brand values.** If a value wasn't covered in Phase 0, ask before using a placeholder.
- **Verify after every phase.** Run the verification commands listed at the bottom of each phase before moving on.
- **Do not deploy** until Phase 6 passes locally. Appendix A is a separate workflow the owner triggers.

---

## 2. Prerequisites

| Requirement | Verify |
|---|---|
| Python 3.12 | `python --version` |
| Git | `git --version` |
| Clean repo clone | `git status` shows clean working tree |
| Venv + deps | `pip install -r requirements.txt` exits 0 |
| Baseline boots | `python app.py` opens at http://127.0.0.1:5000 with the existing (Clips) branding |

If any of these fail, fix before starting. Don't proceed.

---

## 3. Workflow overview

```
┌──────────────────────────────────────────────────────────────────┐
│ Phase 0: Interview owner (Claude asks via AskUserQuestion)       │
│ Phase 1: Write config.py with answers                            │
│ Phase 2: ONE-TIME — refactor templates/routes to read config.py  │
│          (skip if config.py has `refactor_complete = True`)      │
│ Phase 3: Swap 4 logo/favicon assets in static/                   │
│ Phase 4: Set SERVICE_TYPES; update fallback slug if needed       │
│ Phase 5: Apply email signoff tone                                │
│ Phase 6: Boot, smoke test, grep for leftover "Clips" strings     │
│ ──────────────────────────────────────────────────────────────── │
│ Appendix A: Deploy to AWS Lightsail (separate session)           │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Phase 0 — Owner interview (Claude runs this)

Ask the owner every question below before touching code. Group related questions with `AskUserQuestion`. Confirm values that look ambiguous (e.g. ZIP without state).

### Group A — Company identity
- Display name (shown in sidebar, page titles, emails). e.g. `Mike's Mowing`
- Legal name (shown on invoice footer + email footer). e.g. `Mike's Mowing, LLC`
- Owner name (used as invoice "From" and check payee). e.g. `Mike Caldwell`
- Mailing address line 1. e.g. `1212 Lake Rd`
- Mailing address line 2 (city, state ZIP). e.g. `Cleveland, OH 44114`
- Business phone. e.g. `(216) 555-0142`
- Business email (also the default sender). e.g. `mike@mikesmowing.com`
- One-line tagline for the landing page. e.g. `Reliable lawn care in Greater Cleveland`

### Group B — Domain & deployment
- Production domain. e.g. `app.mikesmowing.com`
- Lightsail instance name. e.g. `mikes-mowing-prod`
- Postgres database name. e.g. `mikes_mowing`
- Systemd service name (lowercase, no spaces). e.g. `mikesmowing`
- AWS region. e.g. `us-east-2`

### Group C — Service catalog

Ask the owner for **all** services the company offers. For each: a slug (lowercase, snake_case) and a display label. Replaces Clips's `[('mowing','Mowing'), ('mulching','Mulching'), ('hedge_trimming','Hedge Trimming'), ('leaf_cleanup','Clean Up'), ('other','Other')]`.

Recommendation: keep `('other', 'Other')` as the last entry — `templates/invoice_print.html:427` and `templates/portal_invoice.html:233` use the slug `other` to suppress the auto-generated description on manual line items. If the owner wants a different fallback slug, note it and update both templates in Phase 4.

Also ask the default rate per service type (used in client form defaults). At minimum, ask for the primary recurring service rate (in Clips this is `Client.default_mowing_rate`).

### Group D — Branding
- Primary brand color (hex). e.g. `#2D6A4F`. Claude will derive the 50→900 ramp from this; show the derived palette and confirm.
- Accent color (hex), optional.
- Font preference: keep Fredoka (Clips default) or specify a Google Font.
- Email signoff tone — present three options via `AskUserQuestion`:
  - **Warm:** "Thanks so much! — Mike"
  - **Professional:** "Best regards, Mike's Mowing, LLC"
  - **Casual:** "Cheers, Mike"

### Group E — Payment handles

Ask which of these the company accepts. Mark unset ones as `None` in config; the templates will hide rows where the value is `None`.
- Venmo handle (e.g. `@Mike-Caldwell`) or skip
- Cash App handle (e.g. `$MikeCaldwell`) or skip
- Zelle (usually the business phone or email) or skip
- Check payee name (defaults to legal name)

### Group F — Admin login
- Initial admin username (default `admin`)
- Initial admin password (will be set into `ADMIN_PASSWORD` env var or seeded into the DB)

### Group G — Module confirmation

Confirm via `AskUserQuestion`:
- Keep the customer portal + Stripe payments? (yes/no — if no, the spec keeps the code but you'll never advertise the routes)
- Keep mowing routes? (yes/no)
- Keep payroll & employees? (yes/no)
- Keep expenses & vendors? (yes/no)

Default to "yes" for all. If the owner says no to any, note it but do **not** delete the modules — only stop linking them in the sidebar (`templates/base.html`). The code stays for the next session.

### End of Phase 0

Write the answers to `config.py` (Phase 1). Read the file back to the owner before continuing.

---

## 5. Phase 1 — Write `config.py`

Create `config.py` at the repo root using this template. Fill in every value from Phase 0. Leave `refactor_complete = False` unless this fork has already been through Phase 2.

```python
"""
Per-company configuration.
Edit this file when forking Clips for a new landscaping company.
Read by app.py via a context processor that injects `config` into all templates.
"""

# ─── Refactor sentinel ────────────────────────────────────────────────────
# Flip to True after Phase 2 has run once on this repo.
refactor_complete = False

# ─── Company identity ────────────────────────────────────────────────────
COMPANY_NAME           = "Mike's Mowing"
COMPANY_LEGAL_NAME     = "Mike's Mowing, LLC"
OWNER_NAME             = "Mike Caldwell"
COMPANY_ADDRESS_LINE1  = "1212 Lake Rd"
COMPANY_ADDRESS_LINE2  = "Cleveland, OH 44114"
COMPANY_PHONE          = "(216) 555-0142"
COMPANY_EMAIL          = "mike@mikesmowing.com"
BRAND_TAGLINE          = "Reliable lawn care in Greater Cleveland"

# ─── Payment handles (None = hide row in invoice/email) ──────────────────
PAYMENT_VENMO          = "@Mike-Caldwell"   # or None
PAYMENT_CASHAPP        = "$MikeCaldwell"    # or None
PAYMENT_ZELLE          = "(216) 555-0142"   # or None
PAYMENT_CHECK_PAYEE    = "Mike's Mowing, LLC"

# ─── Service catalog (slug, label). Must include an 'other'-like fallback. ─
SERVICE_TYPES = [
    ("mowing",         "Mowing"),
    ("aeration",       "Aeration"),
    ("fertilization",  "Fertilization"),
    ("leaf_cleanup",   "Leaf Cleanup"),
    ("other",          "Other"),
]
FALLBACK_SERVICE_SLUG = "other"   # used by templates to skip auto-description
DEFAULT_MOWING_RATE   = 45.0      # client form default

# ─── Branding ────────────────────────────────────────────────────────────
COLOR_PRIMARY = "#2D6A4F"   # hex — ramp auto-derived (see static/style.css :root)
COLOR_ACCENT  = "#8B5A2B"   # optional, for badges and highlights
BRAND_FONT    = "Fredoka"   # Google Font name; loaded in base.html

# ─── Email ───────────────────────────────────────────────────────────────
EMAIL_SENDER_NAME = COMPANY_LEGAL_NAME            # appears in MAIL_DEFAULT_SENDER
EMAIL_TONE        = "warm"                        # "warm" | "professional" | "casual"
EMAIL_SIGNOFF     = "Thanks so much!\n— Mike"     # rendered at bottom of every email

# ─── Deployment ──────────────────────────────────────────────────────────
DEPLOY_DOMAIN       = "app.mikesmowing.com"
DEPLOY_SERVICE_NAME = "mikesmowing"        # systemd unit name
DEPLOY_DB_NAME      = "mikes_mowing"       # Postgres DB name
DEPLOY_REGION       = "us-east-2"

# ─── Module toggles (sidebar visibility only — code stays) ───────────────
SHOW_PORTAL_NAV   = True
SHOW_ROUTES_NAV   = True
SHOW_PAYROLL_NAV  = True
SHOW_EXPENSES_NAV = True
```

After writing, run `python -c "import config; print(config.COMPANY_NAME)"` to sanity-check imports.

---

## 6. Phase 2 — One-time refactor

**Skip this phase if `config.refactor_complete == True`.** Read `config.py` first; if the sentinel is set, jump to Phase 3.

This phase converts every hardcoded brand string into a `config.*` reference. After running once, every future fork only edits `config.py` and swaps assets.

### 2.1 Register a context processor in `app.py`

Add after the blueprint registration (near the bottom of `create_app()`):

```python
import config

@app.context_processor
def inject_config():
    return {"config": config}
```

This makes `config.COMPANY_NAME`, `config.PAYMENT_VENMO`, etc. available in every Jinja template.

### 2.2 Move runtime constants out of `app.py`

Replace these literal defaults at `app.py:21, 25, 37-39, 47, 137` with config-aware lookups:

| Old (line) | New |
|---|---|
| `'clips-dev-secret-key-change-in-production'` (21) | unchanged — secrets stay in env, not config |
| `'sqlite:///clips.db'` (25) | `f'sqlite:///{config.DEPLOY_DB_NAME}.db'` |
| `os.environ.get('MAIL_DEFAULT_SENDER', 'Clips by Rorabaugh <noreply@example.com>')` (39) | `os.environ.get('MAIL_DEFAULT_SENDER', f'{config.EMAIL_SENDER_NAME} <{config.COMPANY_EMAIL}>')` |
| `os.environ.get('BASE_URL', 'http://127.0.0.1:5000/')` (47) | unchanged — keep as env var |
| `os.environ.get('ADMIN_PASSWORD', 'clips2026')` (137) | `os.environ.get('ADMIN_PASSWORD', 'change-me')` |

### 2.3 Move `SERVICE_TYPES` out of `models.py`

At `models.py:197-203`, replace the literal list with:

```python
import config
SERVICE_TYPES: list[tuple[str, str]] = config.SERVICE_TYPES
```

Verify `routes/services_bp.py` still works — it reads `Service.SERVICE_TYPES` via the class attribute, so no change needed there.

### 2.4 Template edits

For each file:line below, replace the literal string with the corresponding Jinja expression. Use the inventory in Appendix B for the exact line content if anything is ambiguous.

| File | Line | Replace | With |
|---|---|---|---|
| `templates/base.html` | 7 | `Clips Admin` | `{{ config.COMPANY_NAME }} Admin` |
| `templates/base.html` | 24 | `alt="Clips"` | `alt="{{ config.COMPANY_NAME }}"` |
| `templates/base.html` | 164 | `Clips Admin — Landscaping Business Manager` | `{{ config.COMPANY_NAME }} — Landscaping Business Manager` |
| `templates/portal_base.html` | 7 | `Customer Portal - Clips` | `Customer Portal — {{ config.COMPANY_NAME }}` |
| `templates/portal_base.html` | 114 | `Clips by Rorabaugh, LLC` | `{{ config.COMPANY_LEGAL_NAME }}` |
| `templates/landing.html` | 7 | full SEO title | `{{ config.COMPANY_NAME }} — {{ config.BRAND_TAGLINE }}` |
| `templates/landing.html` | 8 | meta description | rewrite using `{{ config.COMPANY_NAME }}` + service area |
| `templates/landing.html` | 13–15 | OG site name / title / description | same values as the title + description above |
| `templates/landing.html` | 37–47 | JSON-LD `LocalBusiness` block | `name`, `legalName`, `telephone`, `address`, `areaServed` all hardcoded |
| `templates/landing.html` | 78 | logo `alt` | `alt="{{ config.COMPANY_NAME }}"` |
| `templates/landing.html` | 103–106 | hero headline + subhead | rewrite using `{{ config.COMPANY_NAME }}` and tagline |
| `templates/landing.html` | 110, 303, 393 | phone (`tel:` href + display, 3 places) | `{{ config.COMPANY_PHONE }}` |
| `templates/landing.html` | 112, 203, 307, 395 | service-area strings | `{{ config.SERVICE_AREA }}` |
| `templates/landing.html` | 123, 145, 262 | photo `alt` text naming the company | `{{ config.COMPANY_NAME }}` |
| `templates/landing.html` | 154–158 | "who shows up" statement tile (owner + family framing) | rewrite per business |
| `templates/landing.html` | 332 | address placeholder `123 Maple St, Fairlawn` | local street + town |
| `templates/landing.html` | 377, 411 | footer tagline + service area | rewrite per business |
| `templates/landing.html` | 410 | `Clips by Rorabaugh, LLC` | `{{ config.COMPANY_LEGAL_NAME }}` |
| `routes/landing.py` | 14–52 | `REVIEWS` list — nine real customer reviews | replace wholesale; do not ship another company's reviews |
| `routes/landing.py` | 114 | fallback phone in the send-failure flash | `{{ config.COMPANY_PHONE }}` |
| `routes/core.py` | 67 | phone in the 429 error message | `{{ config.COMPANY_PHONE }}` |
| `static/landing.css` | 31–45 | landing palette (`--canvas`, `--canopy`, `--clipping`, `--stone`, `--on-dark-*`) | retune to the new brand; independent of `style.css`. Re-check contrast after changing `--canopy` or `--canvas` — the `--on-dark-*` steps are tuned against those two. |
| `templates/invoice_print.html` | 6 | `Clips Admin` | `{{ config.COMPANY_NAME }}` |
| `templates/invoice_print.html` | 378 | `alt="Clips Landscaping Services"` | `alt="{{ config.COMPANY_NAME }}"` |
| `templates/invoice_print.html` | 383 | owner name | `{{ config.OWNER_NAME }}` |
| `templates/invoice_print.html` | 384–385 | address | `{{ config.COMPANY_ADDRESS_LINE1 }}` / `{{ config.COMPANY_ADDRESS_LINE2 }}` |
| `templates/invoice_print.html` | 386 | phone | `{{ config.COMPANY_PHONE }}` |
| `templates/invoice_print.html` | 427 | `'other'` | `'{{ config.FALLBACK_SERVICE_SLUG }}'` (or leave `'other'` if FALLBACK_SERVICE_SLUG is `'other'`) |
| `templates/invoice_print.html` | 458 | Venmo handle | `{% if config.PAYMENT_VENMO %}{{ config.PAYMENT_VENMO }}{% endif %}` |
| `templates/invoice_print.html` | 459 | Cash App handle | same pattern |
| `templates/invoice_print.html` | 460 | Zelle | same pattern |
| `templates/invoice_detail.html` | 206 | payee | `{{ config.PAYMENT_CHECK_PAYEE }}` |
| `templates/invoice_detail.html` | 210 | Venmo | `{% if config.PAYMENT_VENMO %}{{ config.PAYMENT_VENMO }}{% endif %}` |
| `templates/invoice_detail.html` | 214 | Cash App | same pattern |
| `templates/email_invoice.html` | 47 | check payee | `{{ config.PAYMENT_CHECK_PAYEE }}` |
| `templates/email_invoice.html` | 51 | Venmo | conditional |
| `templates/email_invoice.html` | 55 | Cash App | conditional |
| `templates/email_invoice.html` | 59 | phone | `{{ config.COMPANY_PHONE }}` |
| `templates/email_invoice.html` | 63 | legal name | `{{ config.COMPANY_LEGAL_NAME }}` |
| `templates/email_portal_link.html` | 5, 11 | invitation + footer | use `{{ config.COMPANY_NAME }}` / `{{ config.COMPANY_LEGAL_NAME }}` |
| `templates/portal_invoice.html` | 233 | `'other'` | `'{{ config.FALLBACK_SERVICE_SLUG }}'` |

### 2.5 Route-handler email-string edits

| File | Line | Old | New |
|---|---|---|---|
| `routes/clients.py` | 180, 185, 191 | portal-invite subject + body strings | f-string using `config.COMPANY_NAME` / `config.COMPANY_LEGAL_NAME` |
| `routes/invoices.py` | 314, 344 | `f'Invoice {number} — Clips by Rorabaugh, LLC'` | `f'Invoice {number} — {config.COMPANY_LEGAL_NAME}'` |
| `routes/portal.py` | 154 | `'Your Clips Portal Access Link'` | `f'Your {config.COMPANY_NAME} Portal Access Link'` |
| `routes/portal.py` | 240 | `'Clips Landscaping - {client_name}'` (Stripe product name) | `f'{config.COMPANY_NAME} - {client_name}'` |

Each of these route files needs `import config` at the top.

### 2.6 CSS variable theming

In `static/style.css:7-70`, the `:root` block declares the green palette + semantic vars (`--primary`, `--primary-hover`, etc.) and layout vars. We want the **primary ramp** to be derivable from `config.COLOR_PRIMARY`.

Cleanest approach: keep `style.css` static (the default ramp), but add a small `<style>` block in `templates/base.html` (and `portal_base.html`, `landing.html`) that overrides the key vars from config:

```html
<style>
  :root {
    --primary: {{ config.COLOR_PRIMARY }};
    --primary-hover: {{ config.COLOR_PRIMARY }}cc;  /* 80% alpha */
    --green-600: {{ config.COLOR_PRIMARY }};
    --green-700: {{ config.COLOR_PRIMARY }}dd;
    /* sidebar bg / active states pull from same color */
    --sidebar-active-bg: {{ config.COLOR_PRIMARY }};
  }
</style>
```

If the auto-derivation produces a poor ramp (low contrast, ugly hover), the owner can override individual `--green-*` vars in `static/style.css:7-70` directly. Document this in Appendix C.

### 2.7 Deployment files

| File | Edit |
|---|---|
| `build.sh:8` | Replace `clips` with the systemd service name. Easiest: hardcode `mikesmowing`, or have `scripts/deploy.sh` read `config.DEPLOY_SERVICE_NAME` via `python -c "import config; print(config.DEPLOY_SERVICE_NAME)"` and use the output |
| `docs/aws-lightsail-setup.txt` | Becomes a generic template; per-fork values come from `config.py`. Appendix A of this guide is the canonical version |
| `scripts/clear_db.py:21, 25` | Replace `clips2026` with reading from env or `config` |

### 2.8 Flip the sentinel

When all edits are done and the app boots:

```python
# config.py
refactor_complete = True
```

Commit the refactor with a clear message — every future fork picks up from this state.

### 2.9 Verification

- `python app.py` boots without errors.
- Open http://127.0.0.1:5000/login — page renders with new branding (logo will still be Clips's; that's Phase 3).
- `grep -ri "clips" templates/ routes/ models.py app.py | grep -v "config\|clips.db"` returns nothing (or only intentional matches).
- `python -m pytest -v` — all 41 tests still pass.

---

## 7. Phase 3 — Asset swap

Replace four files in `static/` with the new company's assets. **Keep the filenames identical** so no template paths change.

| Filename | Used in | Recommended dimensions |
|---|---|---|
| `static/logo.png` | Sidebar (admin + portal) | 120×48px, transparent PNG |
| `static/logo_green.png` | Login, invoice header, portal login, portal setup password | 240px wide, height auto, transparent PNG |
| `static/Logo Light Green.png` | Light-background variant | 240px wide |
| `static/Favicon Light Green.png` | Browser tab favicon | 32×32px (or 16×16) |

**Claude:** Ask the owner for the four file paths. Copy them in. Verify dimensions:

```python
from PIL import Image
for name in ["logo.png", "logo_green.png", "Logo Light Green.png", "Favicon Light Green.png"]:
    img = Image.open(f"static/{name}")
    print(name, img.size)
```

If dimensions are off by more than ~20%, ask the owner whether to accept or resize.

### Verification

- Reload http://127.0.0.1:5000/login — new logo on login.
- Login → sidebar shows new sidebar logo.
- Visit /invoices/1/print — invoice header shows new logo.
- Browser tab shows new favicon (may need hard refresh).

---

## 8. Phase 4 — Service types & pricing

### 8.1 SERVICE_TYPES

Already set in `config.py` from Phase 0. Verify:

- New service form (`/services/new`) shows the owner's service types in the dropdown.
- Existing migration is safe — `service_type` is a `String(50)` column, not an enum constraint, so old `'mowing'` rows coexist with new slugs.

### 8.2 Fallback slug

The Clips templates use `service.service_type == 'other'` at two places to suppress the auto-generated description (`templates/invoice_print.html:427`, `templates/portal_invoice.html:233`).

After Phase 2 these compare against `{{ config.FALLBACK_SERVICE_SLUG }}` instead. Just make sure `config.FALLBACK_SERVICE_SLUG` matches a slug actually in `config.SERVICE_TYPES`.

### 8.3 Default rate

In `scripts/init_db.py` and `scripts/add_mock_clients.py`, the seed data uses `default_mowing_rate=...`. Change the literal to `config.DEFAULT_MOWING_RATE` and import config at the top of each file. This only affects seeding; existing client rows are unchanged.

### 8.4 Hardcoded slug references in routes

Search for any other hardcoded slugs:

```
grep -rn "'mowing'\|'mulching'\|'hedge_trimming'\|'leaf_cleanup'" routes/ templates/
```

`routes/services_bp.py:138` sets `service_type='mowing'` as a default for the bulk Quick-Mow form. If the new company's primary recurring service isn't called `mowing`, change this to their primary slug (or add a `config.PRIMARY_SERVICE_SLUG` value).

---

## 9. Phase 5 — Email tone

Edit the body copy in three email-emitting locations to reflect `config.EMAIL_TONE` and end with `config.EMAIL_SIGNOFF`:

- `templates/email_invoice.html` — invoice email body
- `templates/email_portal_link.html` — portal invite/reset body
- `routes/clients.py:180-191` — portal-invite subject + intro
- `routes/portal.py:154` — portal access email subject

### Recommended copy by tone

**Warm:**
```
Hey {{ client.first_name }},

Here's your invoice for {{ invoice.invoice_date.strftime('%B') }}. Thanks so much for letting us take care of your lawn — we really appreciate it!

{{ config.EMAIL_SIGNOFF }}
```

**Professional:**
```
Dear {{ client.last_name }} household,

Please find attached your invoice {{ invoice.invoice_number }} for services rendered. Payment is appreciated within 30 days.

{{ config.EMAIL_SIGNOFF }}
```

**Casual:**
```
Hi {{ client.first_name }} —

Quick one: your invoice for this month is attached. Let me know if anything looks off!

{{ config.EMAIL_SIGNOFF }}
```

Claude picks the matching block based on `config.EMAIL_TONE` and inserts it once per email template. Don't paraphrase further; the owner already chose tone in Phase 0.

### Verification

- From `/invoices/<id>` click "Email Invoice" with a real client email (or `MAIL_SUPPRESS_SEND` in dev).
- Confirm the email body uses the chosen tone + signoff.

---

## 10. Phase 6 — Boot & verify

### 10.1 Reset DB and seed

```bash
python scripts/clear_db.py     # drops all tables, recreates schema, makes admin user
python scripts/init_db.py      # seeds expense categories + sample data
```

Admin login from Phase 0 should work.

### 10.2 Smoke checklist

Claude executes each and reports pass/fail:

- [ ] `python app.py` boots without warnings.
- [ ] http://127.0.0.1:5000/ — landing page renders with new branding.
- [ ] Landing nav logo is visible against the cream canvas (needs `logo_green.png`, not `logo.png`).
- [ ] Landing quote form submits and the request lands in `QUOTE_INBOX`.
- [ ] /login → admin creds from Phase 0 → /dashboard loads.
- [ ] Sidebar shows new logo. Footer shows new company name.
- [ ] /clients/new — create a test client. Default mowing rate matches `config.DEFAULT_MOWING_RATE`.
- [ ] /services/new — service-type dropdown lists the new `SERVICE_TYPES`. Create a service.
- [ ] /invoices/new — create an invoice for the test client. /invoices/N/print shows new owner info, address, phone, payment handles.
- [ ] /portal/setup-password with a test token (or skip if Stripe disabled).
- [ ] Browser tab shows new favicon.
- [ ] `python -m pytest -v` — all tests pass.

### 10.3 Leftover-brand check

```bash
grep -ri "clips\|rorabaugh\|akron" templates/ routes/ static/ models.py app.py \
  | grep -v "config.py\|clips.db\|docs/customizing.md\|.git"
```

Should return zero matches. Any hits = something missed in Phase 2; fix and re-run.

### 10.4 Done

If all smoke items pass and the grep is clean, the local fork is complete. Commit:

```bash
git add -A
git commit -m "Rebrand fork for ${COMPANY_NAME}"
```

Deployment is Appendix A — run as a separate session when the owner is ready to go live.

---

## 11. Appendix A — AWS Lightsail deployment runbook

Run this **only after** Phase 6 passes locally.

### A.1 Provision Lightsail resources

1. https://lightsail.aws.amazon.com
2. Create a managed PostgreSQL database. Note: endpoint, username, password, DB name. Use `config.DEPLOY_DB_NAME` as the DB name.
3. Create an Ubuntu 22.04 LTS instance. Name it `config.DEPLOY_SERVICE_NAME` (or the prefix). Region per `config.DEPLOY_REGION`.
4. Attach a static IP to the instance.
5. Open port 80 in the instance's Networking → IPv4 Firewall.

### A.2 SSH and clone

From Lightsail browser SSH:

```bash
cd /home/ubuntu
git clone https://<GH_PAT>@github.com/<user>/<repo>.git ${config.DEPLOY_SERVICE_NAME}
cd ${config.DEPLOY_SERVICE_NAME}
```

### A.3 Python venv + deps

```bash
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

### A.4 Initialize DB

```bash
DATABASE_URL="postgresql://USER:PASS@ENDPOINT:5432/${config.DEPLOY_DB_NAME}" \
ADMIN_PASSWORD="<chosen-admin-pw>" \
venv/bin/python scripts/init_db.py
```

### A.5 Systemd unit

```bash
sudo nano /etc/systemd/system/${config.DEPLOY_SERVICE_NAME}.service
```

```ini
[Unit]
Description=${config.COMPANY_NAME} Gunicorn App
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/${config.DEPLOY_SERVICE_NAME}
Environment="SECRET_KEY=<generate-random-secret>"
Environment="DATABASE_URL=postgresql://USER:PASS@ENDPOINT:5432/${config.DEPLOY_DB_NAME}"
Environment="MAIL_USERNAME=<smtp-user>"
Environment="MAIL_PASSWORD=<smtp-pass>"
Environment="MAIL_DEFAULT_SENDER=${config.EMAIL_SENDER_NAME} <${config.COMPANY_EMAIL}>"
Environment="STRIPE_SECRET_KEY=<sk_live_...>"
Environment="STRIPE_PUBLISHABLE_KEY=<pk_live_...>"
Environment="STRIPE_WEBHOOK_SECRET=<whsec_...>"
Environment="BASE_URL=https://${config.DEPLOY_DOMAIN}/"
Environment="MARKETING_URL=https://${config.DEPLOY_DOMAIN}"
Environment="QUOTE_INBOX=${config.COMPANY_EMAIL}"
ExecStart=/home/ubuntu/${config.DEPLOY_SERVICE_NAME}/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable & start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ${config.DEPLOY_SERVICE_NAME}
sudo systemctl start ${config.DEPLOY_SERVICE_NAME}
sudo systemctl status ${config.DEPLOY_SERVICE_NAME}
```

### A.6 Nginx reverse proxy

```bash
sudo apt install nginx
sudo nano /etc/nginx/sites-available/${config.DEPLOY_SERVICE_NAME}
```

```nginx
server {
    listen 80;
    server_name ${config.DEPLOY_DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/${config.DEPLOY_SERVICE_NAME} /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

### A.7 DNS

Point an A record for `config.DEPLOY_DOMAIN` at the Lightsail static IP. Wait for propagation (`dig +short ${config.DEPLOY_DOMAIN}`).

### A.8 HTTPS via certbot

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d ${config.DEPLOY_DOMAIN}
```

Renews auto via systemd timer. Verify: `sudo systemctl list-timers | grep certbot`.

### A.9 Verification on prod

- https://${config.DEPLOY_DOMAIN} → landing page.
- /login → admin creds → dashboard.
- Create one real client, one invoice, send a test email to yourself.
- Visit /portal/login as a test client (if portal enabled).

### A.10 Useful commands

```bash
sudo systemctl status ${config.DEPLOY_SERVICE_NAME}     # is gunicorn alive
sudo journalctl -u ${config.DEPLOY_SERVICE_NAME} -n 100 # recent logs
sudo systemctl restart ${config.DEPLOY_SERVICE_NAME}    # after code changes
sudo systemctl reload nginx                             # after nginx config change
cd /home/ubuntu/${config.DEPLOY_SERVICE_NAME} && git pull && sudo systemctl restart ${config.DEPLOY_SERVICE_NAME}
```

---

## 12. Appendix B — Brand touchpoint inventory

Every place "Clips" or owner-specific content is referenced in the canonical repo. Use this in Phase 2 to verify zero misses.

### Brand strings ("Clips" / "clips")

| File | Line | What |
|---|---|---|
| `app.py` | 21 | dev secret key string `'clips-dev-secret-key-...'` |
| `app.py` | 25 | SQLite filename `sqlite:///clips.db` |
| `app.py` | 39 | `MAIL_DEFAULT_SENDER` default `'Clips by Rorabaugh <noreply@example.com>'` |
| `app.py` | 137 | admin password seed `'clips2026'` |
| `templates/base.html` | 7 | `<title>Clips Admin</title>` |
| `templates/base.html` | 24 | `alt="Clips"` |
| `templates/base.html` | 164 | footer `Clips Admin — Landscaping Business Manager` |
| `templates/portal_base.html` | 7 | `<title>Customer Portal - Clips</title>` |
| `templates/portal_base.html` | 114 | portal footer `Clips by Rorabaugh, LLC` |
| `templates/landing.html` | 7–15 | SEO title, meta description, OG tags |
| `templates/landing.html` | 37–47 | JSON-LD `LocalBusiness` (name, phone, areaServed) |
| `templates/landing.html` | 103–112 | hero headline, subhead, phone CTA, service-area note |
| `templates/landing.html` | 154–158 | "who shows up" statement tile |
| `templates/landing.html` | 303–307 | quote panel phone + service area |
| `templates/landing.html` | 376–411 | footer tagline, phone, service area, legal name |
| `routes/landing.py` | 14–52 | `REVIEWS` — nine real customer reviews |
| `routes/landing.py` | 114 | phone in the send-failure flash |
| `routes/core.py` | 67 | phone in the 429 message |
| `templates/email_quote_request.html` | 44 | footer `Clips by Rorabaugh, LLC` |
| `static/landing.css` | 31–45 | landing-only colour palette + contrast tokens |
| `templates/invoice_print.html` | 6 | page title |
| `templates/invoice_print.html` | 378 | logo `alt="Clips Landscaping Services"` |
| `templates/invoice_print.html` | 383 | "From:" payee name |
| `templates/invoice_print.html` | 384–385 | address |
| `templates/invoice_print.html` | 386 | phone |
| `templates/invoice_print.html` | 427 | `if service.service_type == 'other'` |
| `templates/invoice_print.html` | 458 | Venmo handle |
| `templates/invoice_print.html` | 459 | Cash App handle |
| `templates/invoice_print.html` | 460 | Zelle phone |
| `templates/invoice_detail.html` | 206 | check payee |
| `templates/invoice_detail.html` | 210 | Venmo |
| `templates/invoice_detail.html` | 214 | Cash App |
| `templates/email_invoice.html` | 47 | check payee |
| `templates/email_invoice.html` | 51 | Venmo |
| `templates/email_invoice.html` | 55 | Cash App |
| `templates/email_invoice.html` | 59 | phone |
| `templates/email_invoice.html` | 63 | footer `Clips by Rorabaugh, LLC` |
| `templates/email_portal_link.html` | 5, 11 | invitation + footer |
| `templates/portal_invoice.html` | 233 | `if service.service_type == 'other'` |
| `routes/clients.py` | 180, 185, 191 | portal-invite email subject + body |
| `routes/invoices.py` | 314, 344 | invoice email subject `'Invoice ... — Clips by Rorabaugh, LLC'` |
| `routes/portal.py` | 154 | `'Your Clips Portal Access Link'` |
| `routes/portal.py` | 240 | Stripe product name `'Clips Landscaping - {client}'` |
| `static/style.css` | 2 | comment header |
| `scripts/clear_db.py` | 21, 25 | admin password output |
| `scripts/deploy.sh` | 8 | `sudo systemctl restart clips` |
| `docs/aws-lightsail-setup.txt` | many | full deployment doc (superseded by Appendix A) |

### `models.py:197-203` — SERVICE_TYPES

```python
SERVICE_TYPES: list[tuple[str, str]] = [
    ('mowing', 'Mowing'),
    ('mulching', 'Mulching'),
    ('hedge_trimming', 'Hedge Trimming'),
    ('leaf_cleanup', 'Clean Up'),
    ('other', 'Other'),
]
```

### `static/style.css:7-70` — CSS variable groups

| Group | Variables |
|---|---|
| Green palette | `--green-50` through `--green-900` |
| Earth tones | `--earth-50`, `--earth-100`, `--earth-200` |
| Neutrals | `--gray-50` through `--gray-900` |
| Semantic | `--primary`, `--primary-hover`, `--primary-light`, `--danger`, `--danger-hover`, `--warning`, `--info`, `--success` |
| Layout | `--sidebar-width`, `--sidebar-bg`, `--sidebar-text`, `--sidebar-text-active`, `--sidebar-hover-bg`, `--sidebar-active-bg`, `--topbar-height`, `--content-bg`, `--card-bg`, `--card-border`, `--card-radius`, `--card-shadow`, `--card-shadow-hover` |
| Typography | `--font-sans`, `--font-brand` |
| Transitions | `--transition-fast`, `--transition-base`, `--transition-slow` |

### Logo assets

| File | Used in |
|---|---|
| `static/logo.png` | `templates/base.html:24`, `templates/portal_base.html:83`, `templates/landing.html:376` (footer, on dark) |
| `static/logo_green.png` | `templates/login.html:9`, `templates/portal_login.html:18`, `templates/portal_setup_password.html:18`, `templates/invoice_print.html:378`, `templates/landing.html:78` (nav, on light) |
| `static/Logo Light Green.png` | available variant |
| `static/Favicon Light Green.png` | `templates/base.html:8`, `templates/portal_base.html:8`, `templates/landing.html:20` |

The landing page needs **both** logo variants: the nav sits on a cream canvas
(`logo_green.png`) and the footer on dark green (`logo.png`). Supplying only the
light-on-dark mark makes the nav logo invisible.

### Landing page photography

`static/landingPage/` holds the source PNGs plus generated `-<width>.webp`
variants referenced by `<picture>`/`srcset` in `landing.html`. After swapping in
a new company's photos, regenerate the variants at the widths each slot uses —
hero `800/1400`, services feature `640/1120`, gallery anchor `560/1120`, gallery
squares `400/800` — and update the `width`/`height` attributes on each `<img>`
so the reserved aspect ratio still matches.

---

## 13. Appendix C — Troubleshooting

### Email not sending
- Confirm `MAIL_USERNAME` and `MAIL_PASSWORD` env vars are set on the server. They are **not** in `config.py` (secrets stay in env).
- Check `MAIL_DEFAULT_SENDER` matches a domain you control.
- For Gmail SMTP: use an App Password, not the account password.

### Stripe disabled
- If `STRIPE_SECRET_KEY` is absent, `routes/portal.py` falls back to showing Venmo/Zelle/Check on the portal pay page. This is intentional — confirm with the owner whether this is desired.

### Color contrast issues from auto-derived ramp
- The override block in `templates/base.html` only sets a few key vars. If the derived ramp produces low-contrast badges or buttons, edit individual `--green-NNN` values directly in `static/style.css:7-70`.

### "Clips" still appears somewhere after Phase 2
- Run the leftover-brand grep in Phase 6.3. Most likely a string in a route handler that wasn't in the inventory table — edit it inline.

### Tests fail after refactor
- The most common cause is forgetting `import config` at the top of a route file you edited. Re-run `python -m pytest -v` after every route edit.

### `python scripts/clear_db.py` fails with "no such table"
- The DB file was deleted but never recreated. Run `python scripts/init_db.py` instead — it creates tables fresh.

### Service-type dropdown is empty
- `Service.SERVICE_TYPES` in `models.py` should now read `config.SERVICE_TYPES`. Verify the import wasn't lost and `config.SERVICE_TYPES` is a non-empty list of tuples.

---

**End of guide.** When Phase 6 passes, this fork is complete and ready for Appendix A deployment.
