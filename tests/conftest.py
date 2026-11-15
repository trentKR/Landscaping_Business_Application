"""Shared fixtures.

Every test gets a fresh application built by ``create_app()`` against its own
in-memory SQLite database, so tests never touch ``instance/clips.db`` and
never see each other's rows. The app boots in development mode, which
creates the dev admin (``admin`` / ``clips2026``) that ``http_client`` signs
in as.
"""

import os

# Must be set before `app` is imported: the module builds an app at import time.
os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ.pop('ADMIN_PASSWORD', None)
os.environ.pop('ADMIN_USERNAME', None)
os.environ.pop('SESSION_COOKIE_SECURE', None)

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from extensions import limiter  # noqa: E402
from models import (  # noqa: E402
    Client,
    Employee,
    Expense,
    ExpenseCategory,
    Invoice,
    Payment,
    Payroll,
    Route,
    Service,
    Vendor,
)
from models import db as _db  # noqa: E402
from tz import eastern_today  # noqa: E402

DEV_ADMIN_USERNAME = 'admin'
DEV_ADMIN_PASSWORD = 'clips2026'


@pytest.fixture
def app():
    flask_app = create_app()
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    limiter.enabled = False  # login/portal limits would trip across tests
    with flask_app.app_context():
        yield flask_app
        _db.session.remove()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def anon_client(app):
    return app.test_client()


@pytest.fixture
def http_client(app):
    """Test client already signed in as the dev admin."""
    client = app.test_client()
    resp = client.post('/login', data={'username': DEV_ADMIN_USERNAME,
                                       'password': DEV_ADMIN_PASSWORD})
    assert resp.status_code == 302, 'admin login failed inside the fixture'
    return client


@pytest.fixture
def client_obj(db):
    c = Client(
        first_name='Test', last_name='Client',
        address_street='123 Main St', city='Springfield',
        state='TX', zip_code='12345',
    )
    db.session.add(c)
    db.session.flush()
    return c


@pytest.fixture
def make_invoice(db):
    counter = [0]

    def _make(client, subtotal, status='unpaid'):
        counter[0] += 1
        inv = Invoice(
            invoice_number=f'TEST-{counter[0]:04d}',
            client_id=client.id,
            invoice_date=eastern_today(),
            subtotal=subtotal,
            status=status,
        )
        db.session.add(inv)
        db.session.flush()
        return inv

    return _make


@pytest.fixture
def make_payment(db):
    def _make(client, amount):
        p = Payment(
            client_id=client.id,
            payment_date=eastern_today(),
            amount=amount,
            payment_method='check',
        )
        db.session.add(p)
        db.session.flush()
        return p

    return _make


@pytest.fixture
def make_service(db):
    def _make(client, amount=50.0, service_type='mowing', service_date=None):
        s = Service(
            client_id=client.id,
            amount=amount,
            service_type=service_type,
            service_date=service_date or eastern_today(),
            description='Test service',
        )
        db.session.add(s)
        db.session.flush()
        return s

    return _make


@pytest.fixture
def make_vendor(db):
    def _make(name='Test Vendor'):
        v = Vendor(name=name)
        db.session.add(v)
        db.session.flush()
        return v

    return _make


@pytest.fixture
def make_employee(db):
    def _make(first_name='Test', last_name='Worker', rate=15.0, email=''):
        e = Employee(first_name=first_name, last_name=last_name,
                     default_hourly_rate=rate, email=email)
        db.session.add(e)
        db.session.flush()
        return e

    return _make


@pytest.fixture
def make_expense_category(db):
    def _make(name='Materials'):
        c = ExpenseCategory(name=name)
        db.session.add(c)
        db.session.flush()
        return c

    return _make


@pytest.fixture
def make_expense(db):
    def _make(category, amount=100.0, vendor=None, expense_date=None, service=None):
        e = Expense(
            category_id=category.id,
            amount=amount,
            vendor_id=vendor.id if vendor else None,
            service_id=service.id if service else None,
            expense_date=expense_date or eastern_today(),
        )
        db.session.add(e)
        db.session.flush()
        return e

    return _make


@pytest.fixture
def make_route(db):
    def _make(name='Monday Route'):
        r = Route(name=name)
        db.session.add(r)
        db.session.flush()
        return r

    return _make


@pytest.fixture
def make_payroll(db):
    def _make(employee_name='Test Worker', hours=8.0, hourly_rate=15.0):
        p = Payroll(
            employee_name=employee_name,
            work_date=eastern_today(),
            hours=hours,
            hourly_rate=hourly_rate,
            total_payment=round(hours * hourly_rate, 2),
        )
        db.session.add(p)
        db.session.flush()
        return p

    return _make
