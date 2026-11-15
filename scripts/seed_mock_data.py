"""Comprehensive mock data seeder.

Wipes the database and populates it with realistic sample data for development/demo.

Usage:
  python scripts/seed_mock_data.py
"""

import os
import random
import sys
from datetime import datetime, timedelta
from datetime import time as dtime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

os.environ.setdefault('APP_ENV', 'development')  # dev-only script

from app import app  # noqa: E402
from models import (
    Client,
    Employee,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLineItem,
    Payment,
    PaymentApplication,
    Payroll,
    Route,
    Service,
    TimeEntry,
    User,
    Vendor,
    db,
)
from tz import eastern_today


def seed():
    with app.app_context():
        # ── Schema ──────────────────────────────────────────────────────────
        db.drop_all()
        db.create_all()

        admin = User(username=app.config['ADMIN_USERNAME'], name='Admin')
        admin.set_password(app.config['ADMIN_PASSWORD'])
        db.session.add(admin)
        db.session.commit()
        print(f'[ok] Schema created, admin user: {admin.username}')

        # ── Expense categories ───────────────────────────────────────────────
        cat_names = ['Equipment Purchase', 'Equipment Rental', 'Equipment Maintenance',
                     'Materials', 'Fuel', 'Overhead', 'Other']
        cats = {}
        for name in cat_names:
            c = ExpenseCategory(name=name)
            db.session.add(c)
            cats[name] = c
        db.session.flush()
        print(f'[ok] {len(cats)} expense categories')

        # ── Vendors ──────────────────────────────────────────────────────────
        vendor_data = [
            ('Shell Gas Station',        '',           '(703) 555-0101', '',                       '401 Main St, Springfield, VA 22150'),
            ('BP Gas Station',           '',           '(703) 555-0102', '',                       '890 Chain Bridge Rd, Fairfax, VA 22030'),
            ('NAPA Auto Parts',          'Dave Kim',   '(703) 555-0103', 'dkim@napaonline.com',    '221 Lee Hwy, Falls Church, VA 22046'),
            ('Mow Town Equipment',       'Rick Hall',  '(703) 555-0104', 'rick@mowtownequip.com',  '1540 Richmond Hwy, Lorton, VA 22079'),
            ('Home Depot',               '',           '(703) 555-0105', '',                       '6194 Multiplex Dr, Centreville, VA 20121'),
            ("Lowe's",                   '',           '(703) 555-0106', '',                       '6480 Dobbin Rd, Columbia, MD 21045'),
            ('Sunbelt Rentals',          'Craig P.',   '(703) 555-0107', 'cphelps@sunbelt.com',   '8051 Alban Rd, Springfield, VA 22150'),
            ('SiteOne Landscape Supply', 'Amy Torres', '(703) 555-0108', 'atorres@siteone.com',   '7260 Fullerton Rd, Springfield, VA 22153'),
            ('State Farm Insurance',     'Tom Ellis',  '(703) 555-0109', 'tellis@statefarm.com',  '8400 Old Keene Mill Rd, Springfield, VA 22152'),
            ('Verizon Wireless',         '',           '(800) 922-0204', '',                       'PO Box 15124, Albany, NY 12212'),
        ]
        vendors = {}
        for name, contact, phone, email, addr in vendor_data:
            v = Vendor(name=name, contact_name=contact, phone=phone, email=email, address=addr)
            db.session.add(v)
            vendors[name] = v
        db.session.flush()
        print(f'[ok] {len(vendors)} vendors')

        # ── Routes ───────────────────────────────────────────────────────────
        route_a = Route(name='Route A — Monday/Tuesday')
        route_b = Route(name='Route B — Wednesday/Thursday')
        db.session.add_all([route_a, route_b])
        db.session.flush()

        # ── Clients ──────────────────────────────────────────────────────────
        # (first, last, street, city, state, zip, phone, email, rate, weekly, notes, route, pos)
        client_data = [
            ('James',    'Whitfield',  '104 Maple Ridge Dr',   'Springfield', 'VA', '22150', '(703) 555-2001', 'jwhitfield@gmail.com',  48.00, True,  'Rear gate code: 4219',                route_a, 1),
            ('Sandra',   'Perez',      '57 Oak Hill Ct',        'Fairfax',    'VA', '22030', '(703) 555-2002', 'sandra.perez@email.com', 52.00, True,  'Dog — keep gate closed',              route_a, 2),
            ('Michael',  'Thornton',   '921 Cedar Glen Ln',     'Burke',      'VA', '22015', '(703) 555-2003', '',                       45.00, True,  '',                                    route_a, 3),
            ('Linda',    'Osei',       '38 Pinehurst Blvd',     'Annandale',  'VA', '22003', '(703) 555-2004', 'linda.osei@email.com',   55.00, True,  'Front yard only per her request',     route_a, 4),
            ('Kevin',    'Marsh',      '29 Thistledown Ct',     'Springfield','VA', '22150', '(703) 555-2009', 'kmarsh@email.com',       51.00, True,  'Use side entrance only',              route_a, 5),
            ('Angela',   'Reeves',     '167 Copperwood Dr',     'Fairfax',    'VA', '22030', '(703) 555-2010', 'areeves@email.com',      46.00, True,  'Skip backyard if raining',            route_a, 6),
            ('Olivia',   'Bennett',    '14 Stonegate Dr',       'Springfield','VA', '22150', '(555) 310-1001', 'obennett@email.com',     48.00, True,  'Side gate stays unlocked',            route_a, 7),
            ('Marcus',   'Fleming',    '237 Ridgecrest Ln',     'Fairfax',    'VA', '22030', '(555) 310-1002', '',                       52.00, True,  'Two story house, large front yard',   route_a, 8),
            ('Priya',    'Nair',       '47 Westbrook Ave',      'Springfield','VA', '22152', '(555) 310-1005', 'priya.nair@email.com',   43.00, True,  '',                                    route_a, 9),
            ('Robert',   'Castillo',   '215 Brookhaven Way',    'Springfield','VA', '22152', '(703) 555-2005', 'rcastillo@gmail.com',    50.00, True,  '',                                    route_b, 1),
            ('Karen',    'Fitzgerald', '73 Willow Creek Rd',    'Fairfax',    'VA', '22031', '(703) 555-2006', 'kfitz@email.com',        47.00, True,  'Call before arrival on Wed',          route_b, 2),
            ('David',    'Nguyen',     '441 Stonewood Terr',    'Burke',      'VA', '22015', '(703) 555-2007', 'd.nguyen@email.com',     53.00, True,  'Extra care around flower beds',       route_b, 3),
            ('Patricia', 'Johnson',    '812 Foxbury Ave',        'Annandale', 'VA', '22003', '(703) 555-2008', '',                       49.00, True,  '',                                    route_b, 4),
            ('Brian',    'Coleman',    '530 Heritage Mill Rd',  'Burke',      'VA', '22015', '(703) 555-2011', 'bcoleman@gmail.com',     54.00, True,  '',                                    route_b, 5),
            ('Tanya',    'Okafor',     '85 Cloverleaf Ct',      'Burke',      'VA', '22015', '(555) 310-1003', 'tanya.o@email.com',      45.00, True,  'Dog sometimes in yard - call ahead',  route_b, 6),
            ('Evan',     'Kowalski',   '301 Silverbell Rd',     'Annandale',  'VA', '22003', '(555) 310-1004', 'ekowalski@email.com',    55.00, True,  'Edging along driveway each visit',    route_b, 7),
            ('Jerome',   'Hutchins',   '612 Millpond Way',      'Fairfax',    'VA', '22031', '(555) 310-1006', '',                       50.00, True,  'Skip front if cars in driveway',      route_b, 8),
            # Seasonal-only
            ('Stephanie','Larsen',     '29 Copperfield Blvd',   'Burke',      'VA', '22015', '(555) 310-1007', 'slarsen@email.com',      47.00, False, 'Spring mulch and fall cleanup only',  None, None),
            ('Harold',   'Watkins',    '3310 Briar Ridge Ct',   'Centreville','VA', '20120', '(703) 555-2012', 'hwatkins@email.com',      0.00, False, 'Seasonal cleanup only — spring/fall', None, None),
        ]

        clients = []
        for first, last, street, city, state, zipcode, phone, email, rate, weekly, notes, route, pos in client_data:
            c = Client(
                first_name=first, last_name=last,
                address_street=street, city=city, state=state, zip_code=zipcode,
                phone=phone, email=email,
                default_mowing_rate=rate, is_weekly=weekly, notes=notes,
                route_id=route.id if route else None,
                route_position=pos,
            )
            db.session.add(c)
            clients.append(c)
        db.session.flush()
        print(f'[ok] {len(clients)} clients on 2 routes')

        # ── Services — 32 weeks mowing + seasonal upsells ────────────────────
        today = eastern_today()
        payment_methods_income = ['check', 'cash', 'credit_card', 'bank_transfer']
        seasonal_pool = [
            ('mulching',       'Spring mulch application',      150.00, 300.00),
            ('mulching',       'Mulch refresh — flower beds',   120.00, 220.00),
            ('hedge_trimming', 'Hedge and shrub trimming',       80.00, 200.00),
            ('hedge_trimming', 'Overgrown hedge clearance',     150.00, 350.00),
            ('leaf_cleanup',   'Fall leaf cleanup',             100.00, 250.00),
            ('leaf_cleanup',   'Leaf removal — full property',  200.00, 400.00),
            ('other',          'Bush removal',                  120.00, 300.00),
            ('other',          'Gutter clearing',                75.00, 150.00),
            ('other',          'Aeration and overseeding',      175.00, 400.00),
        ]

        weekly_clients = [c for c in clients if c.is_weekly]
        client_services = {c.id: [] for c in clients}

        # Weekly mowing — 32 weeks back
        for week_offset in range(32, 0, -1):
            week_start = today - timedelta(weeks=week_offset)
            for c in weekly_clients:
                if random.random() < 0.92:
                    svc = Service(
                        client_id=c.id,
                        service_date=week_start + timedelta(days=random.randint(0, 4)),
                        service_type='mowing',
                        description='Weekly mowing',
                        amount=c.default_mowing_rate,
                    )
                    db.session.add(svc)
                    client_services[c.id].append(svc)

        # Seasonal upsells
        for c in clients:
            n = random.randint(3, 5) if c.is_weekly else random.randint(1, 3)
            for _ in range(n):
                stype, desc, lo, hi = random.choice(seasonal_pool)
                svc = Service(
                    client_id=c.id,
                    service_date=today - timedelta(days=random.randint(14, 32 * 7)),
                    service_type=stype,
                    description=desc,
                    amount=round(random.uniform(lo, hi), 2),
                )
                db.session.add(svc)
                client_services[c.id].append(svc)

        db.session.flush()
        total_svcs = sum(len(v) for v in client_services.values())
        print(f'[ok] {total_svcs} services')

        # ── Invoices + payments ───────────────────────────────────────────────
        seq = 1
        invoices_created = 0
        payments_created = 0
        cutoff = today - timedelta(weeks=2)

        for c in clients:
            svcs = sorted(client_services[c.id], key=lambda s: s.service_date)
            invoiceable = [s for s in svcs if s.service_date < cutoff]
            if not invoiceable:
                continue

            batches, current, batch_start = [], [], invoiceable[0].service_date
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

                age_days = (today - inv_date).days
                paid_thresh = 0.78 if age_days > 60 else 0.62

                roll = random.random()
                if roll < paid_thresh:
                    pay_date = inv_date + timedelta(days=random.randint(5, 28))
                    pmt = Payment(
                        client_id=c.id,
                        payment_date=pay_date,
                        amount=invoice.subtotal,
                        payment_method=random.choice(payment_methods_income),
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
                elif roll < paid_thresh + 0.20:
                    partial_amt = round(invoice.subtotal * random.uniform(0.30, 0.75), 2)
                    pay_date = inv_date + timedelta(days=random.randint(10, 30))
                    pmt = Payment(
                        client_id=c.id,
                        payment_date=pay_date,
                        amount=partial_amt,
                        payment_method=random.choice(payment_methods_income),
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

        db.session.commit()
        print(f'[ok] {invoices_created} invoices, {payments_created} payments')

        # ── Employees ─────────────────────────────────────────────────────────
        employee_data = [
            ('Jose',  'Martinez', 'jmartinez@email.com', 18.00),
            ('Tyler', 'Brooks',   'tbrooks@email.com',   16.00),
            ('Malik', 'Turner',   'mturner@email.com',   17.50),
        ]
        employees = []
        for first, last, email, rate in employee_data:
            e = Employee(first_name=first, last_name=last, email=email, default_hourly_rate=rate)
            db.session.add(e)
            employees.append(e)
        db.session.flush()
        print(f'[ok] {len(employees)} employees')

        # ── Time entries + payroll (12 weeks of history) ──────────────────────
        time_entry_count = 0
        payroll_count = 0

        for week_offset in range(12, 0, -1):
            week_mon = today - timedelta(weeks=week_offset)
            week_mon = week_mon - timedelta(days=week_mon.weekday())  # normalize to Monday

            for emp in employees:
                for day_offset in range(5):  # Mon–Fri
                    work_date = week_mon + timedelta(days=day_offset)
                    if random.random() > 0.88:  # ~12% absence
                        continue

                    start_hour = random.choice([7, 7, 7, 8])
                    start_min = random.choice([0, 0, 30])
                    hours = round(random.uniform(7.5, 9.5) * 4) / 4  # round to 15-min
                    total = round(hours * emp.default_hourly_rate, 2)

                    end_total_min = start_hour * 60 + start_min + int(hours * 60)
                    end_t = dtime(end_total_min // 60 % 24, end_total_min % 60)
                    start_t = dtime(start_hour, start_min)

                    age = (today - work_date).days
                    if age > 21:
                        status = 'approved'
                    elif age > 10:
                        status = random.choice(['submitted', 'approved'])
                    else:
                        status = random.choice(['draft', 'submitted'])

                    submitted_at = datetime(work_date.year, work_date.month, work_date.day, 17, 30) \
                        if status in ('submitted', 'approved') else None
                    approved_at = datetime(work_date.year, work_date.month, work_date.day, 8, 0) + timedelta(days=1) \
                        if status == 'approved' else None

                    te = TimeEntry(
                        employee_id=emp.id,
                        work_date=work_date,
                        start_time=start_t,
                        end_time=end_t,
                        hours=hours,
                        hourly_rate=emp.default_hourly_rate,
                        total_payment=total,
                        status=status,
                        submitted_at=submitted_at,
                        approved_at=approved_at,
                    )
                    db.session.add(te)
                    time_entry_count += 1

                    if status == 'approved':
                        pr = Payroll(
                            employee_name=emp.full_name,
                            employee_id=emp.id,
                            work_date=work_date,
                            hours=hours,
                            hourly_rate=emp.default_hourly_rate,
                            total_payment=total,
                        )
                        db.session.add(pr)
                        payroll_count += 1

        db.session.flush()

        # Link approved time entries to their payroll records
        for pr in Payroll.query.all():
            te = TimeEntry.query.filter_by(employee_id=pr.employee_id, work_date=pr.work_date).first()
            if te:
                te.payroll_id = pr.id

        db.session.commit()
        print(f'[ok] {time_entry_count} time entries, {payroll_count} payroll records')

        # ── Expenses ──────────────────────────────────────────────────────────
        def vid(name):
            v = vendors.get(name)
            return v.id if v else None

        def cid(name):
            c = cats.get(name)
            return c.id if c else None

        expense_templates = [
            # (days_ago, amount, category, vendor, method, description, is_refund)
            # Fuel
            (3,  62.40, 'Fuel', 'Shell Gas Station', 'debit_card', 'Fuel for mowers and truck', False),
            (10, 55.80, 'Fuel', 'BP Gas Station',    'debit_card', 'Fuel fill-up',              False),
            (17, 71.20, 'Fuel', 'Shell Gas Station', 'debit_card', 'Fuel for mowers and truck', False),
            (24, 48.60, 'Fuel', 'BP Gas Station',    'debit_card', 'Fuel fill-up',              False),
            (31, 66.00, 'Fuel', 'Shell Gas Station', 'debit_card', 'Fuel for mowers and truck', False),
            (38, 59.40, 'Fuel', 'Shell Gas Station', 'debit_card', 'Fuel fill-up',              False),
            (45, 72.80, 'Fuel', 'BP Gas Station',    'debit_card', 'Fuel for mowers and truck', False),
            (52, 61.50, 'Fuel', 'Shell Gas Station', 'debit_card', 'Fuel fill-up',              False),
            # Equipment Maintenance
            (5,  34.99, 'Equipment Maintenance', 'NAPA Auto Parts',    'credit_card', 'Mower air filters x2',                  False),
            (12, 89.50, 'Equipment Maintenance', 'Mow Town Equipment', 'credit_card', 'Blade sharpening and belt replacement', False),
            (20, 22.75, 'Equipment Maintenance', 'NAPA Auto Parts',    'credit_card', 'Spark plugs and oil filter',            False),
            (45, 245.00,'Equipment Maintenance', 'Mow Town Equipment', 'credit_card', 'Annual mower tune-up',                  False),
            (52, 18.40, 'Equipment Maintenance', 'NAPA Auto Parts',    'cash',        'Two-stroke oil mix',                    False),
            (65, 135.00,'Equipment Maintenance', 'Mow Town Equipment', 'credit_card', 'Deck belt replacement',                 False),
            # Equipment Purchase
            (8,  389.99,'Equipment Purchase', 'Mow Town Equipment', 'credit_card', 'Husqvarna string trimmer', False),
            (35, 129.00,'Equipment Purchase', 'Home Depot',         'credit_card', 'Backpack leaf blower',     False),
            (60, 749.00,'Equipment Purchase', 'Mow Town Equipment', 'credit_card', 'Walk-behind edger',        False),
            # Equipment Rental
            (14, 195.00,'Equipment Rental', 'Sunbelt Rentals', 'credit_card', 'Stump grinder rental - half day', False),
            (42, 320.00,'Equipment Rental', 'Sunbelt Rentals', 'credit_card', 'Aerator rental - full day',       False),
            # Materials
            (6,  87.50, 'Materials', 'SiteOne Landscape Supply', 'credit_card', '5 bags black mulch',          False),
            (15, 210.00,'Materials', 'SiteOne Landscape Supply', 'credit_card', '15 yards bulk mulch delivery', False),
            (22, 44.30, 'Materials', 'Home Depot',               'credit_card', 'Landscape edging and stakes',  False),
            (50, 155.00,'Materials', 'SiteOne Landscape Supply', 'credit_card', 'Spring flower bed mulch',      False),
            (16, 44.30, 'Materials', 'Home Depot',               'credit_card', 'Return - wrong edging size',   True),
            # Overhead
            (2,  187.00,'Overhead', 'State Farm Insurance', 'bank_transfer', 'Monthly liability insurance premium', False),
            (32, 187.00,'Overhead', 'State Farm Insurance', 'bank_transfer', 'Monthly liability insurance premium', False),
            (62, 187.00,'Overhead', 'State Farm Insurance', 'bank_transfer', 'Monthly liability insurance premium', False),
            (7,  95.00, 'Overhead', 'Verizon Wireless',     'bank_transfer', 'Business phone plan - monthly',      False),
            (37, 95.00, 'Overhead', 'Verizon Wireless',     'bank_transfer', 'Business phone plan - monthly',      False),
            (67, 95.00, 'Overhead', 'Verizon Wireless',     'bank_transfer', 'Business phone plan - monthly',      False),
            # Other
            (9,  65.00, 'Other', "Lowe's",     'credit_card', 'Safety gloves and eye protection (6 sets)', False),
            (28, 38.00, 'Other', 'Home Depot', 'cash',        'Marking flags and chalk line',               False),
        ]

        expenses_added = 0
        for days_ago, amount, cat_name, vendor_name, method, desc, is_refund in expense_templates:
            db.session.add(Expense(
                expense_date=today - timedelta(days=days_ago),
                amount=amount,
                category_id=cid(cat_name),
                vendor_id=vid(vendor_name),
                payment_method=method,
                description=desc,
                is_refund=is_refund,
            ))
            expenses_added += 1

        # Client-linked materials
        for c in random.sample(weekly_clients, min(5, len(weekly_clients))):
            db.session.add(Expense(
                expense_date=today - timedelta(days=random.randint(5, 45)),
                amount=round(random.uniform(35, 120), 2),
                category_id=cid('Materials'),
                vendor_id=vid('SiteOne Landscape Supply'),
                payment_method='credit_card',
                description='Mulch and edging supplies for job',
                client_id=c.id,
            ))
            expenses_added += 1

        # Client-linked equipment rentals
        for c in random.sample(weekly_clients, min(3, len(weekly_clients))):
            db.session.add(Expense(
                expense_date=today - timedelta(days=random.randint(10, 45)),
                amount=round(random.uniform(150, 350), 2),
                category_id=cid('Equipment Rental'),
                vendor_id=vid('Sunbelt Rentals'),
                payment_method='credit_card',
                description='Equipment rental for client job',
                client_id=c.id,
            ))
            expenses_added += 1

        db.session.commit()
        print(f'[ok] {expenses_added} expenses')

        print(f'\nDatabase populated. Login: {app.config["ADMIN_USERNAME"]} / '
              f'{"(ADMIN_PASSWORD)" if os.environ.get("ADMIN_PASSWORD") else "clips2026"}')


if __name__ == '__main__':
    seed()
