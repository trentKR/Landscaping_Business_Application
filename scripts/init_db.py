"""Initialize the database: schema, first admin user, expense categories.

Usage:
  Local:   ADMIN_PASSWORD="..." python scripts/init_db.py
  Server:  set -a; . /etc/clips/env; set +a; venv/bin/python scripts/init_db.py

Reads:
  DATABASE_URL    omit locally to use the SQLite dev file
  ADMIN_USERNAME  default "admin"
  ADMIN_PASSWORD  required, at least 12 characters

Refuses to run against a database that already holds business data unless
--force is passed, in which case it DROPS EVERY TABLE and starts over.
"""

import os
import sys

# Schema and admin creation need a database, not a session key, so this script
# runs the app factory in development mode. DATABASE_URL still selects the
# target: pass the production URL explicitly when initializing a server.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

os.environ.setdefault('APP_ENV', 'development')

from utils import MIN_ADMIN_PASSWORD_LENGTH  # noqa: E402

ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin').strip() or 'admin'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
if len(ADMIN_PASSWORD) < MIN_ADMIN_PASSWORD_LENGTH:
    print(f'Error: set ADMIN_PASSWORD (at least {MIN_ADMIN_PASSWORD_LENGTH} characters) '
          'before running scripts/init_db.py.')
    sys.exit(1)

from app import app  # noqa: E402
from models import (  # noqa: E402
    Client,
    Employee,
    Expense,
    ExpenseCategory,
    Invoice,
    Payment,
    Payroll,
    Service,
    User,
    db,
)

EXPENSE_CATEGORIES = [
    'Equipment Purchase',
    'Equipment Rental',
    'Equipment Maintenance',
    'Materials',
    'Fuel',
    'Overhead',
    'Other',
]


def seed_categories() -> None:
    """Insert the standard expense categories if the table is empty.

    Safe to run against a live database — skips entirely if any categories exist.
    """
    if ExpenseCategory.query.first():
        print('Expense categories already present — skipping.')
        return
    for name in EXPENSE_CATEGORIES:
        db.session.add(ExpenseCategory(name=name))
    db.session.commit()
    print(f'Expense categories seeded: {len(EXPENSE_CATEGORIES)}')


def has_business_data() -> bool:
    """True if any table other than users/categories has rows."""
    return any(model.query.first() is not None
               for model in (Client, Service, Invoice, Payment, Expense, Employee, Payroll))


if __name__ == '__main__':
    force = '--force' in sys.argv[1:]
    with app.app_context():
        if has_business_data() and not force:
            print('Error: this database already contains clients, invoices, or other '
                  'business data. Re-run with --force to DROP ALL TABLES and start over.')
            sys.exit(1)

        db.drop_all()
        db.create_all()

        admin = User(username=ADMIN_USERNAME, name='Admin')
        admin.set_password(ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.commit()

        seed_categories()

    print('Database initialized.')
    print(f'  Admin user: {ADMIN_USERNAME}')
    print(f'  Expense categories: {len(EXPENSE_CATEGORIES)}')
