# Landscaping Business Application

A full-stack Flask app for running a small lawn care and landscaping business: clients, weekly mowing routes, services, invoicing, payments, expenses, payroll, and financial reporting, plus a customer portal and an employee timesheet portal.

It runs a real operation with 50+ weekly clients. The branding in the templates (the landing page and email footers) belongs to that business. See [docs/customizing.md](docs/customizing.md) to make it your own.

## Features

- **Clients and mowing routes:** client records with default mowing rates, drag-and-drop route ordering
- **Services:** spreadsheet-style quick-add grid, plus a weekly mowing checklist grouped by route
- **Invoicing:** batch-create invoices for every client with uninvoiced work, PDF generation, email delivery, bulk email/void/delete
- **Payments:** FIFO application across a client's open invoices, partial payments, automatic use of unapplied credit
- **Expenses:** categories, vendors, refunds, and splitting one expense across several clients
- **Payroll and timesheets:** employees submit hours through their own portal; an admin approves them and runs payroll
- **Customer portal:** clients view invoices and pay by card through Stripe Checkout
- **Reporting:** dashboard metrics and a 12-month revenue, expense, payroll, and profit breakdown
- **Public landing page:** marketing site with a spam-hardened quote request form

## Tech stack

- **Backend:** Python 3.12, Flask 3, SQLAlchemy. PostgreSQL in production, SQLite for local development
- **PDFs:** WeasyPrint
- **Payments:** Stripe Checkout
- **Email:** Gmail API (OAuth2) with SMTP fallback
- **Frontend:** server-rendered Jinja templates, Bootstrap 5, vanilla JavaScript

## Quick start

```bash
python -m venv venv
source venv/bin/activate              # Windows: venv\Scripts\activate
pip install -r requirements.txt
python scripts/seed_mock_data.py      # optional: fill the dev database with sample data
python app.py                         # http://127.0.0.1:5000
```

Sign in with `admin` / `clips2026`. That login exists only in development mode, which `python app.py` turns on automatically. Production has no default password.

PDF generation needs WeasyPrint's native libraries (Pango, Cairo). Everything else works without them. See the [WeasyPrint install guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html).

## Configuration

All settings come from environment variables. [.env.example](.env.example) lists every one with a short explanation. The ones that matter most:

- `APP_ENV` defaults to `production`, which requires `SECRET_KEY` and `DATABASE_URL` and refuses to start without them.
- Without the Gmail OAuth variables, email goes out over SMTP (`MAIL_*`).
- Without the Stripe keys, the customer portal shows the other payment methods instead of a card checkout.
- `PAYMENT_NAME`, `VENMO_HANDLE`, `CASHAPP_HANDLE`, `ZELLE_PHONE`, and the `BUSINESS_*` variables are printed on invoices, so set them before sending any.

## Project layout

```
app.py              App factory, configuration, startup migrations
models.py           Database schema and computed properties
services.py         Business logic: FIFO payments, credits, financial reports
routes/             One Flask blueprint per domain (clients, invoices, payments, ...)
templates/          Jinja templates
static/             CSS, images, landing page assets
scripts/            Database setup, mock data, and deploy scripts
tests/              pytest suite
docs/               Deployment, customization, and design notes
```

## Scripts

| Command | What it does |
|---------|--------------|
| `ADMIN_PASSWORD=... python scripts/init_db.py` | Create the schema, the first admin user, and the expense categories. Refuses a non-empty database unless given `--force` |
| `python scripts/seed_mock_data.py` | Drop and rebuild the dev database with realistic sample data |
| `python scripts/add_mock_clients.py` | Add 7 sample clients without wiping anything |
| `python scripts/clear_db.py` | Reset the local SQLite database to an empty schema and admin user |
| `bash scripts/deploy.sh` | On the server: install dependencies and restart the service |

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # full suite
python -m ruff check .       # lint
```

Each test gets a fresh app on an in-memory SQLite database. CI runs ruff, pytest, and `pip-audit` on every push and pull request.

## Documentation

- [docs/deployment.md](docs/deployment.md): deploying to an Ubuntu server with Gunicorn, Nginx, and PostgreSQL (written for AWS Lightsail)
- [docs/customizing.md](docs/customizing.md): rebranding the app for a different business
- [docs/invoice-status-logic.md](docs/invoice-status-logic.md): how invoice status is derived from payment applications
- [docs/expenses.md](docs/expenses.md): the expense and split-allocation model
- [CLAUDE.md](CLAUDE.md): a detailed architecture reference, kept for AI coding assistants but readable by anyone

## License

[MIT](LICENSE)
