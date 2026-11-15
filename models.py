from enum import StrEnum

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

from tz import eastern_now, eastern_today

db = SQLAlchemy()

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Half-cent tolerance for float arithmetic in payment comparisons
PAYMENT_TOLERANCE = 0.005


class InvoiceStatus(StrEnum):
    """Invoice lifecycle statuses. StrEnum so values compare equal to plain strings,
    making SQLAlchemy filter expressions work without casting."""
    UNPAID = 'unpaid'
    PARTIAL = 'partial'
    PAID = 'paid'
    VOID = 'void'


# Leading underscore: these are module-private; access via the class attributes
# Payment.PAYMENT_METHODS and Expense.PAYMENT_METHODS.
_PAYMENT_METHODS: list[tuple[str, str]] = [
    ('check', 'Check'),
    ('cash', 'Cash'),
    ('credit_card', 'Credit Card'),
    ('bank_transfer', 'Bank Transfer'),
    ('other', 'Other'),
]

# Expense adds debit_card — separate list, not a superset of _PAYMENT_METHODS
_EXPENSE_PAYMENT_METHODS: list[tuple[str, str]] = [
    ('cash', 'Cash'),
    ('check', 'Check'),
    ('credit_card', 'Credit Card'),
    ('debit_card', 'Debit Card'),
    ('bank_transfer', 'Bank Transfer'),
    ('other', 'Other'),
]


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=eastern_now)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Client(db.Model):
    __tablename__ = 'clients'

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    address_street = db.Column(db.String(200), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(2), nullable=False)
    zip_code = db.Column(db.String(10), nullable=False)
    phone = db.Column(db.String(20), default='')
    email = db.Column(db.String(120), default='')
    password_hash = db.Column(db.String(256), nullable=True)
    portal_invite_token = db.Column(db.String(256), nullable=True)
    portal_invite_sent_at = db.Column(db.DateTime, nullable=True)
    default_mowing_rate = db.Column(db.Float, nullable=False, default=0.0)
    is_weekly = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text, default='')
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=eastern_now)

    @property
    def full_name(self) -> str:
        return f'{self.first_name} {self.last_name}'

    @property
    def full_address(self) -> str:
        return f'{self.address_street}, {self.city}, {self.state} {self.zip_code}'

    services = db.relationship('Service', backref='client', lazy='dynamic',
                               order_by='Service.service_date.desc()')
    invoices = db.relationship('Invoice', backref='client', lazy='dynamic',
                               order_by='Invoice.invoice_date.desc()')
    payments = db.relationship('Payment', backref='client', lazy='dynamic',
                               order_by='Payment.payment_date.desc()')

    def total_revenue(self) -> float:
        result = db.session.query(db.func.sum(Service.amount)).filter(
            Service.client_id == self.id
        ).scalar()
        return result or 0.0

    def uninvoiced_services(self) -> list['Service']:
        """Services not linked to any InvoiceLineItem."""
        invoiced_ids = db.session.query(InvoiceLineItem.service_id).filter(
            InvoiceLineItem.service_id.isnot(None)
        ).subquery()
        return Service.query.filter(
            Service.client_id == self.id,
            ~Service.id.in_(db.session.query(invoiced_ids))
        ).order_by(Service.service_date.asc()).all()

    def account_balance(self) -> float:
        """Total invoiced (non-void) minus total paid. Positive = client owes."""
        total_invoiced = db.session.query(db.func.sum(Invoice.subtotal)).filter(
            Invoice.client_id == self.id,
            Invoice.status != InvoiceStatus.VOID
        ).scalar() or 0.0
        total_paid = db.session.query(db.func.sum(Payment.amount)).filter(
            Payment.client_id == self.id
        ).scalar() or 0.0
        return round(total_invoiced - total_paid, 2)

    def unapplied_credit(self) -> float:
        """Total payments minus total applications."""
        total_paid = db.session.query(db.func.sum(Payment.amount)).filter(
            Payment.client_id == self.id
        ).scalar() or 0.0
        total_applied = db.session.query(db.func.sum(PaymentApplication.amount_applied)).join(
            Payment
        ).filter(
            Payment.client_id == self.id
        ).scalar() or 0.0
        return round(total_paid - total_applied, 2)

    route_id = db.Column(db.Integer, db.ForeignKey('routes.id'), nullable=True, index=True)
    route_position = db.Column(db.Integer, nullable=True)

    @classmethod
    def active_sorted(cls):
        return cls.query.filter_by(active=True).order_by(cls.last_name, cls.first_name).all()

    def set_portal_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_portal_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def has_portal_access(self) -> bool:
        return bool(self.password_hash)

    def __repr__(self) -> str:
        return f'<Client {self.full_name}>'


class Route(db.Model):
    __tablename__ = 'routes'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=eastern_now)

    clients = db.relationship('Client', backref='route', lazy='dynamic',
                              order_by='Client.route_position')

    @property
    def client_count(self) -> int:
        return self.clients.filter_by(active=True).count()

    @classmethod
    def all_sorted(cls):
        return cls.query.order_by(cls.name).all()

    def __repr__(self) -> str:
        return f'<Route {self.name}>'


class Service(db.Model):
    __tablename__ = 'services'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    service_date = db.Column(db.Date, nullable=False, default=eastern_today, index=True)
    service_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(250), default='')
    amount = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=eastern_now)

    SERVICE_TYPES: list[tuple[str, str]] = [
        ('mowing', 'Mowing'),
        ('mulching', 'Mulching'),
        ('hedge_trimming', 'Hedge Trimming'),
        ('leaf_cleanup', 'Clean Up'),
        ('other', 'Other'),
    ]

    @property
    def service_type_display(self) -> str:
        return SERVICE_TYPE_MAP.get(self.service_type, self.service_type)

    def __repr__(self) -> str:
        return f'<Service {self.service_type} for {self.client_id} on {self.service_date}>'


# Module-level cache — built once after Service is defined, not per property call
SERVICE_TYPE_MAP: dict[str, str] = dict(Service.SERVICE_TYPES)


class Invoice(db.Model):
    __tablename__ = 'invoices'

    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(20), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    invoice_date = db.Column(db.Date, nullable=False, default=eastern_today, index=True)
    subtotal = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(20), nullable=False, default=InvoiceStatus.UNPAID, index=True)
    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=eastern_now)
    sent_at = db.Column(db.DateTime, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    voided_at = db.Column(db.DateTime, nullable=True)

    line_items = db.relationship('InvoiceLineItem', backref='invoice', lazy=True,
                                 cascade='all, delete-orphan')
    payment_applications = db.relationship('PaymentApplication', backref='invoice', lazy=True,
                                           cascade='all, delete-orphan')

    STATUS_CLASSES: dict[str, str] = {
        InvoiceStatus.UNPAID: 'badge-unpaid',
        InvoiceStatus.PARTIAL: 'badge-partial',
        InvoiceStatus.PAID: 'badge-paid',
        InvoiceStatus.VOID: 'badge-void',
    }

    @property
    def amount_paid(self) -> float:
        result = db.session.query(
            db.func.sum(PaymentApplication.amount_applied)
        ).filter(PaymentApplication.invoice_id == self.id).scalar()
        return result or 0.0

    @property
    def balance_due(self) -> float:
        return round(self.subtotal - self.amount_paid, 2)

    @property
    def status_display(self) -> str:
        return self.status.capitalize()

    @property
    def status_class(self) -> str:
        return self.STATUS_CLASSES.get(self.status, 'badge-unpaid')

    def recalculate_subtotal(self) -> None:
        self.subtotal = round(sum(li.amount for li in self.line_items), 2)

    def update_status(self) -> None:
        """Update status based on this invoice's own balance_due. Does not touch void."""
        if self.status == InvoiceStatus.VOID:
            return
        if self.balance_due <= PAYMENT_TOLERANCE:
            self.status = InvoiceStatus.PAID
            if not self.paid_at:
                self.paid_at = eastern_now()
        elif self.amount_paid > 0:
            self.status = InvoiceStatus.PARTIAL
            self.paid_at = None
        else:
            self.status = InvoiceStatus.UNPAID
            self.paid_at = None

    @staticmethod
    def generate_invoice_number() -> str:
        year = eastern_today().year
        last = Invoice.query.filter(
            Invoice.invoice_number.like(f'INV-{year}-%')
        ).order_by(Invoice.id.desc()).first()
        if last:
            seq = int(last.invoice_number.split('-')[-1]) + 1
        else:
            seq = 1
        return f'INV-{year}-{seq:04d}'

    def __repr__(self) -> str:
        return f'<Invoice {self.invoice_number}>'


class InvoiceLineItem(db.Model):
    __tablename__ = 'invoice_line_items'

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=True)
    description = db.Column(db.String(250), nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=eastern_now)

    service = db.relationship('Service', backref=db.backref('line_item', uselist=False))

    def __repr__(self) -> str:
        return f'<InvoiceLineItem {self.description} ${self.amount}>'


class Payment(db.Model):
    __tablename__ = 'payments'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False, index=True)
    payment_date = db.Column(db.Date, nullable=False, default=eastern_today, index=True)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(20), nullable=False, default='check')
    reference = db.Column(db.String(100), default='')
    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=eastern_now)

    applications = db.relationship('PaymentApplication', backref='payment', lazy=True,
                                   cascade='all, delete-orphan')

    PAYMENT_METHODS = _PAYMENT_METHODS

    @property
    def amount_applied(self) -> float:
        return sum(pa.amount_applied for pa in self.applications)

    @property
    def amount_unapplied(self) -> float:
        return round(self.amount - self.amount_applied, 2)

    @property
    def method_display(self) -> str:
        method_map = dict(self.PAYMENT_METHODS)
        return method_map.get(self.payment_method, self.payment_method)

    def __repr__(self) -> str:
        return f'<Payment ${self.amount} from client {self.client_id}>'


class PaymentApplication(db.Model):
    __tablename__ = 'payment_applications'

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payments.id'), nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    amount_applied = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=eastern_now)

    def __repr__(self) -> str:
        return f'<PaymentApplication ${self.amount_applied}>'


class Payroll(db.Model):
    __tablename__ = 'payroll'

    id = db.Column(db.Integer, primary_key=True)
    employee_name = db.Column(db.String(120), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=True, index=True)
    work_date = db.Column(db.Date, nullable=False, default=eastern_today)
    hours = db.Column(db.Float, nullable=False)
    hourly_rate = db.Column(db.Float, nullable=False)
    total_payment = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=eastern_now)

    def __repr__(self) -> str:
        return f'<Payroll {self.employee_name} {self.work_date}>'


class Vendor(db.Model):
    __tablename__ = 'vendors'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    contact_name = db.Column(db.String(120), default='')
    phone = db.Column(db.String(20), default='')
    email = db.Column(db.String(120), default='')
    address = db.Column(db.String(250), default='')
    notes = db.Column(db.Text, default='')
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=eastern_now)

    expenses = db.relationship('Expense', backref='vendor', lazy='dynamic',
                               order_by='Expense.expense_date.desc()')

    def total_spent(self) -> float:
        result = db.session.query(db.func.sum(Expense.amount)).filter(
            Expense.vendor_id == self.id
        ).scalar()
        return result or 0.0

    def expense_count(self) -> int:
        return Expense.query.filter_by(vendor_id=self.id).count()

    @classmethod
    def active_sorted(cls):
        return cls.query.filter_by(active=True).order_by(cls.name).all()

    def __repr__(self) -> str:
        return f'<Vendor {self.name}>'


class Employee(db.Model):
    __tablename__ = 'employees'

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    email = db.Column(db.String(120), default='')
    default_hourly_rate = db.Column(db.Float, default=0.0)
    active = db.Column(db.Boolean, default=True)
    password_hash = db.Column(db.String(256), nullable=True)
    portal_invite_token = db.Column(db.String(256), nullable=True)
    portal_invite_sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=eastern_now)

    @property
    def full_name(self) -> str:
        return f'{self.first_name} {self.last_name}'

    def set_portal_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_portal_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def has_portal_access(self) -> bool:
        return bool(self.password_hash)

    def __repr__(self) -> str:
        return f'<Employee {self.full_name}>'


class TimeEntry(db.Model):
    __tablename__ = 'time_entries'
    __table_args__ = (
        db.UniqueConstraint('employee_id', 'work_date', name='uq_employee_work_date'),
    )

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False, index=True)
    work_date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    hours = db.Column(db.Float, nullable=False, default=0.0)
    hourly_rate = db.Column(db.Float, nullable=False)
    total_payment = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='draft')
    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=eastern_now)
    submitted_at = db.Column(db.DateTime, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    payroll_id = db.Column(db.Integer, db.ForeignKey('payroll.id'), nullable=True)

    employee = db.relationship('Employee', backref=db.backref('time_entries', lazy='dynamic'))
    payroll_entry = db.relationship('Payroll', backref=db.backref('time_entry', uselist=False))

    STATUS_CLASSES: dict[str, str] = {
        'draft': 'badge-draft',
        'submitted': 'badge-submitted',
        'approved': 'badge-approved',
        'rejected': 'badge-rejected',
    }

    @property
    def is_editable(self) -> bool:
        return True

    @property
    def status_class(self) -> str:
        return self.STATUS_CLASSES.get(self.status, 'badge-draft')

    def __repr__(self) -> str:
        return f'<TimeEntry {self.employee_id} {self.work_date} {self.hours}h>'


class ExpenseCategory(db.Model):
    __tablename__ = 'expense_categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=eastern_now)

    expenses = db.relationship('Expense', backref='category', lazy='dynamic')

    def __repr__(self) -> str:
        return f'<ExpenseCategory {self.name}>'


class Expense(db.Model):
    __tablename__ = 'expenses'

    id = db.Column(db.Integer, primary_key=True)
    expense_date = db.Column(db.Date, nullable=False, default=eastern_today, index=True)
    amount = db.Column(db.Float, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('expense_categories.id'), nullable=False, index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendors.id'), nullable=True, index=True)
    payment_method = db.Column(db.String(20), nullable=False, default='credit_card')
    description = db.Column(db.String(250), default='')
    receipt_path = db.Column(db.String(500), nullable=True)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=True)
    is_refund = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=eastern_now)

    service = db.relationship('Service', backref=db.backref('expenses', lazy=True))
    client = db.relationship('Client', backref=db.backref('expenses', lazy='dynamic'))
    allocations = db.relationship('ExpenseAllocation', backref='expense',
                                  lazy=True, cascade='all, delete-orphan')

    PAYMENT_METHODS = _EXPENSE_PAYMENT_METHODS

    @property
    def method_display(self) -> str:
        method_map = dict(self.PAYMENT_METHODS)
        return method_map.get(self.payment_method, self.payment_method)

    @property
    def effective_amount(self) -> float:
        """Negative if refund, positive otherwise."""
        return -self.amount if self.is_refund else self.amount

    @property
    def is_split(self) -> bool:
        return bool(self.allocations)

    @property
    def allocated_amount(self) -> float:
        return round(sum(a.amount for a in self.allocations), 2)

    def __repr__(self) -> str:
        return f'<Expense ${self.amount} on {self.expense_date}>'


class AppSetting(db.Model):
    __tablename__ = 'app_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, default='')
    updated_at = db.Column(db.DateTime, default=eastern_now, onupdate=eastern_now)

    @classmethod
    def get(cls, key: str, default: str = '') -> str:
        row = cls.query.filter_by(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key: str, value: str) -> None:
        row = cls.query.filter_by(key=key).first()
        if row:
            row.value = value
        else:
            row = cls(key=key, value=value)
            db.session.add(row)

    @classmethod
    def delete_key(cls, key: str) -> None:
        cls.query.filter_by(key=key).delete()

    def __repr__(self) -> str:
        return f'<AppSetting {self.key}>'


class ExpenseAllocation(db.Model):
    __tablename__ = 'expense_allocations'

    id         = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey('expenses.id'), nullable=False)
    client_id  = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    amount     = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=eastern_now)

    client = db.relationship('Client', backref=db.backref('expense_allocations', lazy='dynamic'))

    def __repr__(self) -> str:
        return f'<ExpenseAllocation ${self.amount} client={self.client_id}>'
