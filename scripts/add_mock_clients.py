"""Add 7 mock clients to the existing database (no drop/recreate).

Usage:
  python scripts/add_mock_clients.py
"""

import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

os.environ.setdefault('APP_ENV', 'development')  # dev-only script

from app import app  # noqa: E402
from models import (
    Client,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLineItem,
    Payment,
    PaymentApplication,
    Service,
    Vendor,
    db,
)
from tz import eastern_today  # noqa: E402

NEW_CLIENTS = [
    # (first, last, street, city, state, zip, phone, email, rate, is_weekly, notes)
    ('Olivia', 'Bennett', '14 Stonegate Dr', 'Springfield', 'VA', '22150',
     '(555) 310-1001', 'obennett@email.com', 48.00, True, 'Side gate stays unlocked'),
    ('Marcus', 'Fleming', '237 Ridgecrest Ln', 'Fairfax', 'VA', '22030',
     '(555) 310-1002', '', 52.00, True, 'Two story house, large front yard'),
    ('Tanya', 'Okafor', '85 Cloverleaf Ct', 'Burke', 'VA', '22015',
     '(555) 310-1003', 'tanya.o@email.com', 45.00, True, 'Dog sometimes in yard - call ahead'),
    ('Evan', 'Kowalski', '301 Silverbell Rd', 'Annandale', 'VA', '22003',
     '(555) 310-1004', 'ekowalski@email.com', 55.00, True, 'Edging along driveway each visit'),
    ('Priya', 'Nair', '47 Westbrook Ave', 'Springfield', 'VA', '22152',
     '(555) 310-1005', 'priya.nair@email.com', 43.00, True, ''),
    ('Jerome', 'Hutchins', '612 Millpond Way', 'Fairfax', 'VA', '22031',
     '(555) 310-1006', '', 50.00, True, 'Skip front if cars in driveway'),
    ('Stephanie', 'Larsen', '29 Copperfield Blvd', 'Burke', 'VA', '22015',
     '(555) 310-1007', 'slarsen@email.com', 47.00, False, 'Spring mulch and fall cleanup only'),
]


def add_clients():
    with app.app_context():
        today = eastern_today()
        payment_methods = ['check', 'cash', 'credit_card', 'bank_transfer']

        # Determine next invoice sequence number
        from models import Invoice as Inv
        last_inv = Inv.query.order_by(Inv.id.desc()).first()
        if last_inv and last_inv.invoice_number:
            try:
                seq = int(last_inv.invoice_number.split('-')[-1]) + 1
            except ValueError:
                seq = 1000
        else:
            seq = 1000

        clients_added = []
        services_added = []
        invoices_created = 0
        payments_created = 0

        for first, last, street, city, state, zipcode, phone, email, rate, is_weekly, notes in NEW_CLIENTS:
            c = Client(
                first_name=first, last_name=last,
                address_street=street, city=city, state=state, zip_code=zipcode,
                phone=phone, email=email,
                default_mowing_rate=rate, is_weekly=is_weekly, notes=notes,
            )
            db.session.add(c)
            clients_added.append(c)

        db.session.flush()  # assign IDs

        # Generate 6 weeks of services for each new client
        for c in clients_added:
            client_svcs = []

            if c.is_weekly:
                for week_offset in range(6, 0, -1):
                    week_start = today - timedelta(weeks=week_offset)
                    if random.random() < 0.90:  # 90% attendance
                        svc = Service(
                            client_id=c.id,
                            service_date=week_start + timedelta(days=random.randint(0, 3)),
                            service_type='mowing',
                            description='Weekly mowing',
                            amount=c.default_mowing_rate,
                        )
                        db.session.add(svc)
                        client_svcs.append(svc)
                        services_added.append(svc)

                # One seasonal service in the mix
                if random.random() < 0.6:
                    stype, desc, low, high = random.choice([
                        ('mulching', 'Spring mulch application', 120.00, 250.00),
                        ('hedge_trimming', 'Hedge trimming', 75.00, 180.00),
                    ])
                    svc = Service(
                        client_id=c.id,
                        service_date=today - timedelta(days=random.randint(14, 50)),
                        service_type=stype,
                        description=desc,
                        amount=round(random.uniform(low, high), 2),
                    )
                    db.session.add(svc)
                    client_svcs.append(svc)
                    services_added.append(svc)
            else:
                # Seasonal-only: 1-2 services
                for _ in range(random.randint(1, 2)):
                    stype, desc, low, high = random.choice([
                        ('mulching', 'Spring mulch application', 150.00, 280.00),
                        ('leaf_cleanup', 'Fall leaf cleanup', 100.00, 220.00),
                    ])
                    svc = Service(
                        client_id=c.id,
                        service_date=today - timedelta(days=random.randint(7, 60)),
                        service_type=stype,
                        description=desc,
                        amount=round(random.uniform(low, high), 2),
                    )
                    db.session.add(svc)
                    client_svcs.append(svc)
                    services_added.append(svc)

            db.session.flush()

            # Invoice services older than 2 weeks
            cutoff = today - timedelta(weeks=2)
            invoiceable = sorted([s for s in client_svcs if s.service_date < cutoff],
                                 key=lambda s: s.service_date)

            if not invoiceable:
                continue

            # Group into biweekly batches
            batches = []
            current_batch = []
            batch_start = invoiceable[0].service_date
            for svc in invoiceable:
                if (svc.service_date - batch_start).days > 14:
                    if current_batch:
                        batches.append(current_batch)
                    current_batch = [svc]
                    batch_start = svc.service_date
                else:
                    current_batch.append(svc)
            if current_batch:
                batches.append(current_batch)

            for batch in batches:
                inv_date = batch[-1].service_date + timedelta(days=1)

                invoice = Invoice(
                    invoice_number=f'INV-{today.year}-{seq:04d}',
                    client_id=c.id,
                    invoice_date=inv_date,
                    status='unpaid',
                )
                db.session.add(invoice)
                db.session.flush()
                seq += 1

                for svc in batch:
                    li = InvoiceLineItem(
                        invoice_id=invoice.id,
                        service_id=svc.id,
                        description=f'{svc.service_type_display} - {svc.service_date.strftime("%m/%d/%Y")}',
                        amount=svc.amount,
                    )
                    db.session.add(li)

                db.session.flush()
                invoice.recalculate_subtotal()
                invoices_created += 1

                # Payment scenario: 50% paid, 20% partial, 30% unpaid
                roll = random.random()
                if roll < 0.50:
                    pay_date = inv_date + timedelta(days=random.randint(5, 20))
                    payment = Payment(
                        client_id=c.id,
                        payment_date=pay_date,
                        amount=invoice.subtotal,
                        payment_method=random.choice(payment_methods),
                        reference=f'Check #{random.randint(1000, 9999)}' if random.random() < 0.6 else '',
                    )
                    db.session.add(payment)
                    db.session.flush()
                    db.session.add(PaymentApplication(
                        payment_id=payment.id,
                        invoice_id=invoice.id,
                        amount_applied=invoice.subtotal,
                    ))
                    invoice.status = 'paid'
                    invoice.paid_at = datetime(pay_date.year, pay_date.month, pay_date.day)
                    payments_created += 1
                elif roll < 0.70:
                    partial_amt = round(invoice.subtotal * random.uniform(0.3, 0.7), 2)
                    pay_date = inv_date + timedelta(days=random.randint(10, 25))
                    payment = Payment(
                        client_id=c.id,
                        payment_date=pay_date,
                        amount=partial_amt,
                        payment_method=random.choice(payment_methods),
                        notes='Partial payment',
                    )
                    db.session.add(payment)
                    db.session.flush()
                    db.session.add(PaymentApplication(
                        payment_id=payment.id,
                        invoice_id=invoice.id,
                        amount_applied=partial_amt,
                    ))
                    invoice.status = 'partial'
                    payments_created += 1
                # else: stays unpaid

        db.session.commit()

        print('Mock clients added successfully!')
        print(f'  Clients: {len(clients_added)}')
        print(f'  Services: {len(services_added)}')
        print(f'  Invoices: {invoices_created}')
        print(f'  Payments: {payments_created}')
        for c in clients_added:
            print(f'    {c.full_name} — {c.city}, {c.state}  rate=${c.default_mowing_rate:.2f}  weekly={c.is_weekly}')


def add_expenses():
    with app.app_context():
        today = eastern_today()

        # Seed categories if missing
        if not ExpenseCategory.query.first():
            for name in ['Equipment Purchase', 'Equipment Rental', 'Equipment Maintenance',
                         'Materials', 'Fuel', 'Overhead', 'Other']:
                db.session.add(ExpenseCategory(name=name))
            db.session.flush()
            print('  Seeded expense categories')

        # Look up existing categories and vendors
        cats = {c.name: c for c in ExpenseCategory.query.all()}
        vendors = {v.name: v for v in Vendor.query.all()}

        # Pick a few clients to link some material/rental expenses to
        weekly_clients = Client.query.filter_by(is_weekly=True, active=True).all()

        def vid(name):
            v = vendors.get(name)
            return v.id if v else None

        def cid(name):
            c = cats.get(name)
            return c.id if c else None


        expense_templates = [
            # (days_ago, amount, category, vendor_name, payment_method, description, is_refund)
            # --- Fuel (weekly-ish) ---
            (3,  62.40, 'Fuel', 'Shell Gas Station', 'debit_card',  'Fuel for mowers and truck', False),
            (10, 55.80, 'Fuel', 'BP Gas Station',    'debit_card',  'Fuel fill-up',               False),
            (17, 71.20, 'Fuel', 'Shell Gas Station', 'debit_card',  'Fuel for mowers and truck',  False),
            (24, 48.60, 'Fuel', 'BP Gas Station',    'debit_card',  'Fuel fill-up',               False),
            (31, 66.00, 'Fuel', 'Shell Gas Station', 'debit_card',  'Fuel for mowers and truck',  False),
            (38, 59.40, 'Fuel', 'Shell Gas Station', 'debit_card',  'Fuel fill-up',               False),

            # --- Equipment Maintenance ---
            (5,  34.99, 'Equipment Maintenance', 'NAPA Auto Parts',      'credit_card', 'Mower air filters x2',           False),
            (12, 89.50, 'Equipment Maintenance', 'Mow Town Equipment',   'credit_card', 'Blade sharpening and belt replacement', False),
            (20, 22.75, 'Equipment Maintenance', 'NAPA Auto Parts',      'credit_card', 'Spark plugs and oil filter',     False),
            (45, 245.00,'Equipment Maintenance', 'Mow Town Equipment',   'credit_card', 'Annual mower tune-up',           False),
            (52, 18.40, 'Equipment Maintenance', 'NAPA Auto Parts',      'cash',        'Two-stroke oil mix',             False),

            # --- Equipment Purchase ---
            (8,  389.99,'Equipment Purchase', 'Mow Town Equipment', 'credit_card', 'Husqvarna string trimmer',     False),
            (35, 129.00,'Equipment Purchase', "Home Depot",         'credit_card', 'Backpack leaf blower',         False),
            (60, 749.00,'Equipment Purchase', 'Mow Town Equipment', 'credit_card', 'Walk-behind edger',            False),

            # --- Equipment Rental ---
            (14, 195.00,'Equipment Rental', 'Sunbelt Rentals', 'credit_card', 'Stump grinder rental - half day', False),
            (42, 320.00,'Equipment Rental', 'Sunbelt Rentals', 'credit_card', 'Aerator rental - full day',       False),

            # --- Materials ---
            (6,  87.50, 'Materials', 'SiteOne Landscape Supply', 'credit_card', '5 bags black mulch',             False),
            (15, 210.00,'Materials', 'SiteOne Landscape Supply', 'credit_card', '15 yards bulk mulch delivery',   False),
            (22, 44.30, 'Materials', "Home Depot",               'credit_card', 'Landscape edging and stakes',    False),
            (50, 155.00,'Materials', 'SiteOne Landscape Supply', 'credit_card', 'Spring flower bed mulch',        False),
            # Refund on a materials return
            (16, 44.30, 'Materials', "Home Depot",               'credit_card', 'Return - wrong edging size',     True),

            # --- Overhead ---
            (2,  187.00,'Overhead', 'State Farm Insurance', 'bank_transfer', 'Monthly liability insurance premium', False),
            (32, 187.00,'Overhead', 'State Farm Insurance', 'bank_transfer', 'Monthly liability insurance premium', False),
            (7,  95.00, 'Overhead', 'Verizon Wireless',     'bank_transfer', 'Business phone plan - monthly',      False),
            (37, 95.00, 'Overhead', 'Verizon Wireless',     'bank_transfer', 'Business phone plan - monthly',      False),

            # --- Other ---
            (9,  65.00, 'Other', "Lowe's",    'credit_card', 'Safety gloves and eye protection (6 sets)', False),
            (28, 38.00, 'Other', "Home Depot", 'cash',       'Marking flags and chalk line',               False),
        ]

        expenses_added = 0
        for days_ago, amount, cat_name, vendor_name, method, desc, is_refund in expense_templates:
            exp_date = today - timedelta(days=days_ago)
            cat_id = cid(cat_name)
            if not cat_id:
                print(f'  Warning: category "{cat_name}" not found — skipping')
                continue

            exp = Expense(
                expense_date=exp_date,
                amount=amount,
                category_id=cat_id,
                vendor_id=vid(vendor_name),
                payment_method=method,
                description=desc,
                is_refund=is_refund,
            )
            db.session.add(exp)
            expenses_added += 1

        # A few client-linked material expenses (mulch/supplies for specific jobs)
        if weekly_clients and cid('Materials'):
            for client in random.sample(weekly_clients, min(4, len(weekly_clients))):
                exp = Expense(
                    expense_date=today - timedelta(days=random.randint(5, 30)),
                    amount=round(random.uniform(35, 120), 2),
                    category_id=cid('Materials'),
                    vendor_id=vid('SiteOne Landscape Supply'),
                    payment_method='credit_card',
                    description='Mulch and edging supplies for job',
                    client_id=client.id,
                )
                db.session.add(exp)
                expenses_added += 1

        # A couple client-linked equipment rentals
        if weekly_clients and cid('Equipment Rental'):
            for client in random.sample(weekly_clients, min(2, len(weekly_clients))):
                exp = Expense(
                    expense_date=today - timedelta(days=random.randint(10, 45)),
                    amount=round(random.uniform(150, 350), 2),
                    category_id=cid('Equipment Rental'),
                    vendor_id=vid('Sunbelt Rentals'),
                    payment_method='credit_card',
                    description='Equipment rental for client job',
                    client_id=client.id,
                )
                db.session.add(exp)
                expenses_added += 1

        db.session.commit()
        print(f'\nExpenses added: {expenses_added}')


def add_revenue():
    """Add 6 months of back-history: mowing + seasonal services, invoices, and payments."""
    with app.app_context():
        today = eastern_today()
        payment_methods = ['check', 'cash', 'credit_card', 'bank_transfer']

        # Next invoice seq
        last_inv = Invoice.query.order_by(Invoice.id.desc()).first()
        try:
            seq = int(last_inv.invoice_number.split('-')[-1]) + 1 if last_inv else 2000
        except ValueError:
            seq = 2000

        weekly_clients = Client.query.filter_by(is_weekly=True, active=True).all()
        all_clients    = Client.query.filter_by(active=True).all()

        services_added   = []
        invoices_created = 0
        payments_created = 0

        seasonal_pool = [
            ('mulching',       'Spring mulch application',     150.00, 300.00),
            ('mulching',       'Mulch refresh — flower beds',  120.00, 220.00),
            ('hedge_trimming', 'Hedge and shrub trimming',      80.00, 200.00),
            ('hedge_trimming', 'Overgrown hedge clearance',    150.00, 350.00),
            ('leaf_cleanup',   'Fall leaf cleanup',            100.00, 250.00),
            ('leaf_cleanup',   'Leaf removal — full property', 200.00, 400.00),
            ('other',          'Bush removal',                 120.00, 300.00),
            ('other',          'Gutter clearing',               75.00, 150.00),
            ('other',          'Aeration and overseeding',     175.00, 400.00),
        ]

        # --- 26 weeks of mowing (going back ~6 months from oldest existing service) ---
        # Find the earliest existing service date so we don't overlap
        oldest_svc = Service.query.order_by(Service.service_date).first()
        history_end = oldest_svc.service_date if oldest_svc else today - timedelta(weeks=6)

        for week_offset in range(26, 0, -1):
            week_start = history_end - timedelta(weeks=week_offset)
            mow_day = week_start + timedelta(days=random.randint(0, 4))
            for c in weekly_clients:
                if random.random() < 0.93:
                    svc = Service(
                        client_id=c.id,
                        service_date=mow_day + timedelta(days=random.randint(0, 1)),
                        service_type='mowing',
                        description='Weekly mowing',
                        amount=c.default_mowing_rate,
                    )
                    db.session.add(svc)
                    services_added.append(svc)

        # --- Seasonal upsells: ~3 per client over the 6-month window ---
        for c in all_clients:
            n = 3 if c.is_weekly else 2
            for _ in range(n):
                stype, desc, lo, hi = random.choice(seasonal_pool)
                days_back = random.randint(14, 26 * 7)
                svc_date = today - timedelta(days=days_back)
                # Keep it before the existing history boundary
                if oldest_svc and svc_date >= oldest_svc.service_date:
                    svc_date = oldest_svc.service_date - timedelta(days=random.randint(7, 60))
                svc = Service(
                    client_id=c.id,
                    service_date=svc_date,
                    service_type=stype,
                    description=desc,
                    amount=round(random.uniform(lo, hi), 2),
                )
                db.session.add(svc)
                services_added.append(svc)

        db.session.flush()

        # --- Invoice all new services in biweekly batches ---
        client_svcs = {}
        for svc in services_added:
            client_svcs.setdefault(svc.client_id, []).append(svc)

        for c in all_clients:
            svcs = sorted(client_svcs.get(c.id, []), key=lambda s: s.service_date)
            if not svcs:
                continue

            # Batch into biweekly groups; leave the most recent 2 weeks uninvoiced
            cutoff = today - timedelta(weeks=2)
            invoiceable = [s for s in svcs if s.service_date < cutoff]

            batches, current, batch_start = [], [], invoiceable[0].service_date if invoiceable else None
            for svc in invoiceable:
                if (svc.service_date - batch_start).days > 14:
                    if current:
                        batches.append(current)
                    current = [svc]
                    batch_start = svc.service_date
                else:
                    current.append(svc)
            if current:
                batches.append(current)

            for batch in batches:
                inv_date = batch[-1].service_date + timedelta(days=1)
                invoice = Invoice(
                    invoice_number=f'INV-{today.year}-{seq:04d}',
                    client_id=c.id,
                    invoice_date=inv_date,
                    status='unpaid',
                )
                db.session.add(invoice)
                db.session.flush()
                seq += 1

                for svc in batch:
                    db.session.add(InvoiceLineItem(
                        invoice_id=invoice.id,
                        service_id=svc.id,
                        description=f'{svc.service_type_display} - {svc.service_date.strftime("%m/%d/%Y")}',
                        amount=svc.amount,
                    ))

                db.session.flush()
                invoice.recalculate_subtotal()
                invoices_created += 1

                # Payment mix: 65% paid, 20% partial, 15% unpaid
                # Older invoices more likely to be fully paid
                age_days = (today - inv_date).days
                paid_threshold = 0.75 if age_days > 60 else 0.60

                roll = random.random()
                if roll < paid_threshold:
                    pay_date = inv_date + timedelta(days=random.randint(5, 28))
                    pmt = Payment(
                        client_id=c.id,
                        payment_date=pay_date,
                        amount=invoice.subtotal,
                        payment_method=random.choice(payment_methods),
                        reference=f'Check #{random.randint(1000, 9999)}' if random.random() < 0.65 else '',
                    )
                    db.session.add(pmt)
                    db.session.flush()
                    db.session.add(PaymentApplication(
                        payment_id=pmt.id, invoice_id=invoice.id,
                        amount_applied=invoice.subtotal,
                    ))
                    invoice.status = 'paid'
                    invoice.paid_at = datetime(pay_date.year, pay_date.month, pay_date.day)
                    payments_created += 1
                elif roll < paid_threshold + 0.20:
                    partial_pct = random.uniform(0.30, 0.75)
                    partial_amt = round(invoice.subtotal * partial_pct, 2)
                    pay_date = inv_date + timedelta(days=random.randint(10, 30))
                    pmt = Payment(
                        client_id=c.id,
                        payment_date=pay_date,
                        amount=partial_amt,
                        payment_method=random.choice(payment_methods),
                        notes='Partial payment',
                    )
                    db.session.add(pmt)
                    db.session.flush()
                    db.session.add(PaymentApplication(
                        payment_id=pmt.id, invoice_id=invoice.id,
                        amount_applied=partial_amt,
                    ))
                    invoice.status = 'partial'
                    payments_created += 1
                else:
                    pass  # unpaid

        db.session.commit()
        print('Revenue added successfully!')
        print(f'  Services: {len(services_added)}')
        print(f'  Invoices: {invoices_created}')
        print(f'  Payments: {payments_created}')


if __name__ == '__main__':
    # Guard: only add clients if they haven't been inserted yet
    with app.app_context():
        already_exists = Client.query.filter_by(first_name='Olivia', last_name='Bennett').first()
    if not already_exists:
        add_clients()
    else:
        print('Clients already present — skipping add_clients()')
    add_expenses()
    add_revenue()
