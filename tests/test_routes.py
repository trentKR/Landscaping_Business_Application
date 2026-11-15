"""Route-level tests for every blueprint: status codes, redirects, flash
messages, form handling, and JSON APIs.

Ported from the pre-June-2026 suite and updated for the grid-style quick-add
pages that replaced the single-entry /new routes.
"""

import re
from datetime import timedelta

import pytest

from models import (
    Client,
    Employee,
    Expense,
    ExpenseAllocation,
    Invoice,
    InvoiceLineItem,
    Payment,
    PaymentApplication,
    Payroll,
    Route,
    Service,
    Vendor,
)
from routes import landing
from tz import eastern_today


def grid_rows(prefix_map, *rows):
    """Build form data for a quick-add grid.

    ``prefix_map`` is ignored; each row is a dict of field → value and gets
    the ``_<index>`` suffix the grids expect.
    """
    data = {'row_index': [str(i) for i in range(len(rows))]}
    for i, row in enumerate(rows):
        for field, value in row.items():
            data[f'{field}_{i}'] = value
    return data


# ---------------------------------------------------------------------------
# TestCoreRoutes
# ---------------------------------------------------------------------------

class TestCoreRoutes:
    """Landing, dashboard, financials, login, 404, currency filter."""

    def test_landing_renders_for_anonymous(self, anon_client):
        resp = anon_client.get('/')
        assert resp.status_code == 200

    def test_dashboard_requires_login(self, anon_client):
        resp = anon_client.get('/dashboard')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_dashboard_renders(self, http_client, db):
        resp = http_client.get('/dashboard')
        assert resp.status_code == 200

    def test_dashboard_shows_client_count(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.get('/dashboard')
        assert resp.status_code == 200
        assert b'Clients' in resp.data

    def test_financials_renders(self, http_client, db):
        resp = http_client.get('/financials')
        assert resp.status_code == 200

    def test_financials_accepts_year_param(self, http_client, db):
        resp = http_client.get('/financials?year=2025')
        assert resp.status_code == 200

    def test_login_page_renders(self, anon_client):
        resp = anon_client.get('/login')
        assert resp.status_code == 200

    def test_login_bad_password(self, anon_client):
        resp = anon_client.post('/login', data={'username': 'admin', 'password': 'nope'})
        assert resp.status_code == 200
        assert b'Invalid username or password' in resp.data

    def test_logout_requires_post(self, http_client):
        assert http_client.get('/logout').status_code == 405
        resp = http_client.post('/logout')
        assert resp.status_code == 302
        assert http_client.get('/dashboard').status_code == 302

    def test_404_error_page(self, anon_client):
        resp = anon_client.get('/nonexistent-page-xyz')
        assert resp.status_code == 404

    def test_currency_filter(self, app):
        filt = app.jinja_env.filters['currency']
        assert filt(1234.5) == '$1,234.50'
        assert filt(0) == '$0.00'
        assert filt(None) == '$0.00'
        assert filt('bad') == '$0.00'


# ---------------------------------------------------------------------------
# TestQuoteRequest
# ---------------------------------------------------------------------------

class TestQuoteRequest:
    """Landing-page quote form: Ohio-only delivery plus the abuse defenses."""

    THANKS = "we&#39;ll be in touch within a day"

    @pytest.fixture(autouse=True)
    def instant_fills_are_human(self, monkeypatch):
        """Most tests post the instant the token is minted; only the timing test cares."""
        monkeypatch.setattr(landing, 'MIN_FORM_SECONDS', 0)

    @staticmethod
    def form(**overrides):
        data = {
            'name': 'Pat Visitor',
            'street': '123 Maple St',
            'city': 'Fairlawn',
            'state': 'OH',
            'zip': '44333',
            'phone': '330-555-0100',
            'service': 'Weekly mowing',
            'form_ts': landing.make_quote_token(),
        }
        data.update(overrides)
        return data

    @staticmethod
    def capture(monkeypatch):
        """Replace routes.landing.send_email with a recorder; returns the call list."""
        calls = []

        def fake_send_email(**kwargs):
            calls.append(kwargs)
            return True

        monkeypatch.setattr('routes.landing.send_email', fake_send_email)
        return calls

    def test_ohio_request_is_emailed(self, anon_client, app, monkeypatch):
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote', data=self.form())
        assert resp.status_code == 302
        assert len(calls) == 1
        assert calls[0]['recipients'] == [app.config['QUOTE_INBOX']]

    def test_state_spelled_out_and_lowercase_are_ohio(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        anon_client.post('/request-quote', data=self.form(state='Ohio'))
        anon_client.post('/request-quote', data=self.form(state='oh'))
        assert len(calls) == 2

    def test_out_of_state_request_is_not_emailed(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote',
                                data=self.form(city='Reno', state='NV', zip='89501'))
        assert resp.status_code == 302
        assert '/#quote' in resp.headers['Location']
        assert calls == []

    def test_out_of_state_visitor_sees_the_same_success_message(self, anon_client, monkeypatch):
        """The filter must be invisible to whoever submitted the form."""
        self.capture(monkeypatch)
        ohio = anon_client.post('/request-quote', data=self.form(),
                                follow_redirects=True)
        nevada = anon_client.post('/request-quote',
                                  data=self.form(city='Reno', state='NV', zip='89501'),
                                  follow_redirects=True)
        assert self.THANKS in ohio.get_data(as_text=True)
        assert self.THANKS in nevada.get_data(as_text=True)

    def test_ohio_zip_wins_over_a_bad_state(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        anon_client.post('/request-quote', data=self.form(state='XX'))
        anon_client.post('/request-quote', data=self.form(state='XX', zip='44333-1234'))
        assert len(calls) == 2

    def test_ohio_state_wins_over_an_out_of_state_zip(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        anon_client.post('/request-quote', data=self.form(zip='90210'))
        assert len(calls) == 1

    def test_incomplete_address_is_rejected_before_the_ohio_check(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote', data=self.form(street=''),
                                follow_redirects=True)
        assert 'full property address' in resp.get_data(as_text=True)
        assert calls == []

    def test_honeypot_still_short_circuits(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote', data=self.form(website='http://spam'),
                                follow_redirects=True)
        assert self.THANKS in resp.get_data(as_text=True)
        assert calls == []

    # -- form-age token ---------------------------------------------------

    def test_landing_page_carries_a_form_token(self, anon_client):
        body = anon_client.get('/').get_data(as_text=True)
        assert 'name="form_ts"' in body

    def test_missing_token_is_dropped_silently(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        data = self.form()
        del data['form_ts']
        resp = anon_client.post('/request-quote', data=data, follow_redirects=True)
        assert self.THANKS in resp.get_data(as_text=True)
        assert calls == []

    def test_forged_token_is_dropped_silently(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote', data=self.form(form_ts='not.a.token'),
                                follow_redirects=True)
        assert self.THANKS in resp.get_data(as_text=True)
        assert calls == []

    def test_instant_submit_is_dropped_silently(self, anon_client, monkeypatch):
        """A fill that beats MIN_FORM_SECONDS is a script, not a customer."""
        monkeypatch.setattr(landing, 'MIN_FORM_SECONDS', 3)
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote', data=self.form(), follow_redirects=True)
        assert self.THANKS in resp.get_data(as_text=True)
        assert calls == []

    def test_expired_token_asks_them_to_resend(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        monkeypatch.setattr(landing, 'MAX_FORM_SECONDS', -1)
        resp = anon_client.post('/request-quote', data=self.form(), follow_redirects=True)
        assert 'form expired' in resp.get_data(as_text=True)
        assert calls == []

    # -- scrubbing and caps -----------------------------------------------

    def test_crlf_in_the_name_never_reaches_the_subject(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        anon_client.post('/request-quote',
                         data=self.form(name='Bob\r\nBcc: evil@example.com'))
        assert len(calls) == 1
        subject = calls[0]['subject']
        assert '\r' not in subject and '\n' not in subject

    def test_invisible_characters_are_stripped(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        anon_client.post('/request-quote', data=self.form(name='Pat\u200b\u202eVisitor'))
        assert len(calls) == 1
        assert 'PatVisitor' in calls[0]['subject']

    def test_notes_keep_their_line_breaks(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        anon_client.post('/request-quote', data=self.form(notes='Gate code 1234\nDog in yard'))
        assert len(calls) == 1
        assert 'Dog in yard' in calls[0]['html']

    def test_overlong_notes_are_rejected(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote', data=self.form(notes='x' * 5000),
                                follow_redirects=True)
        assert 'longer than we can accept' in resp.get_data(as_text=True)
        assert calls == []

    def test_overlong_name_is_rejected(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote', data=self.form(name='x' * 500),
                                follow_redirects=True)
        assert 'longer than we can accept' in resp.get_data(as_text=True)
        assert calls == []

    def test_oversized_body_is_refused_before_parsing(self, anon_client, monkeypatch):
        """MAX_CONTENT_LENGTH trips before the form is parsed; the 413 handler
        turns that into a redirect rather than an error page."""
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote',
                                data=self.form(notes='x' * (2 * 1024 * 1024)),
                                follow_redirects=True)
        assert 'too much text for one message' in resp.get_data(as_text=True)
        assert calls == []

    # -- format checks ----------------------------------------------------

    def test_malformed_email_is_rejected(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote',
                                data=self.form(phone='', email='not-an-email'),
                                follow_redirects=True)
        assert "email address doesn&#39;t look right" in resp.get_data(as_text=True)
        assert calls == []

    def test_valid_email_alone_is_accepted(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        anon_client.post('/request-quote',
                         data=self.form(phone='', email='pat@example.com'))
        assert len(calls) == 1

    def test_junk_phone_is_rejected(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        resp = anon_client.post('/request-quote', data=self.form(phone='abc'),
                                follow_redirects=True)
        assert "phone number doesn&#39;t look right" in resp.get_data(as_text=True)
        assert calls == []

    def test_unknown_service_falls_back_to_the_catch_all(self, anon_client, monkeypatch):
        calls = self.capture(monkeypatch)
        anon_client.post('/request-quote', data=self.form(service='<script>alert(1)</script>'))
        assert len(calls) == 1
        assert 'Something else' in calls[0]['html']
        assert 'alert(1)' not in calls[0]['html']


# ---------------------------------------------------------------------------
# TestClientRoutes
# ---------------------------------------------------------------------------

class TestClientRoutes:
    """Client CRUD + API endpoints."""

    def test_client_list_renders_empty(self, http_client, db):
        resp = http_client.get('/clients')
        assert resp.status_code == 200

    def test_client_list_shows_active_clients(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.get('/clients')
        assert resp.status_code == 200
        assert b'Test' in resp.data

    def test_client_list_search_filter(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.get('/clients?search=Test')
        assert resp.status_code == 200
        assert b'Test' in resp.data

    def test_client_list_weekly_filter(self, http_client, db):
        c = Client(first_name='Weekly', last_name='Client',
                   address_street='1 St', city='C', state='TX',
                   zip_code='00000', is_weekly=True)
        db.session.add(c)
        db.session.commit()
        resp = http_client.get('/clients?filter=weekly')
        assert resp.status_code == 200
        assert b'Weekly' in resp.data

    def test_client_list_inactive_filter(self, http_client, client_obj, db):
        client_obj.active = False
        db.session.commit()
        resp = http_client.get('/clients?filter=inactive')
        assert resp.status_code == 200
        assert b'Test' in resp.data

    def test_client_new_get_renders_form(self, http_client, db):
        resp = http_client.get('/clients/new')
        assert resp.status_code == 200

    def test_client_create_success(self, http_client, db):
        resp = http_client.post('/clients/new', data={
            'first_name': 'Jane', 'last_name': 'Doe',
            'address_street': '456 Oak', 'city': 'Austin',
            'state': 'TX', 'zip_code': '78701',
            'default_mowing_rate': '75',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'created' in resp.data.lower()
        assert Client.query.filter_by(first_name='Jane').first() is not None

    def test_client_create_with_route(self, http_client, make_route, db):
        route = make_route('Test Route')
        db.session.commit()
        resp = http_client.post('/clients/new', data={
            'first_name': 'Routed', 'last_name': 'Client',
            'address_street': '1 St', 'city': 'C', 'state': 'TX',
            'zip_code': '00000', 'route_id': str(route.id),
            'default_mowing_rate': '0',
        }, follow_redirects=True)
        assert resp.status_code == 200
        c = Client.query.filter_by(first_name='Routed').first()
        assert c is not None
        assert c.route_id == route.id

    def test_client_create_invalid_rate(self, http_client, db):
        resp = http_client.post('/clients/new', data={
            'first_name': 'Bad', 'last_name': 'Rate',
            'address_street': '1 St', 'city': 'C', 'state': 'TX',
            'zip_code': '00000', 'default_mowing_rate': 'abc',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'valid number' in resp.data.lower()

    def test_client_detail_renders(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.get(f'/clients/{client_obj.id}')
        assert resp.status_code == 200
        assert b'Test' in resp.data

    def test_client_detail_404(self, http_client, db):
        resp = http_client.get('/clients/9999')
        assert resp.status_code == 404

    def test_client_edit_get(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.get(f'/clients/{client_obj.id}/edit')
        assert resp.status_code == 200

    def test_client_edit_updates_fields(self, http_client, client_obj, db):
        db.session.commit()
        cid = client_obj.id
        resp = http_client.post(f'/clients/{cid}/edit', data={
            'first_name': 'Updated', 'last_name': 'Name',
            'address_street': '999 New St', 'city': 'Dallas',
            'state': 'TX', 'zip_code': '75001',
            'default_mowing_rate': '80', 'active': 'on',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'updated' in resp.data.lower()
        db.session.expire_all()
        c = db.session.get(Client, cid)
        assert c.first_name == 'Updated'
        assert c.city == 'Dallas'

    def test_client_delete_soft(self, http_client, client_obj, db):
        db.session.commit()
        cid = client_obj.id
        resp = http_client.post(f'/clients/{cid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'deactivated' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Client, cid).active is False

    def test_client_hard_delete_unlinked(self, http_client, client_obj, db):
        db.session.commit()
        cid = client_obj.id
        resp = http_client.post(f'/clients/{cid}/hard-delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'permanently deleted' in resp.data.lower()
        assert db.session.get(Client, cid) is None

    def test_client_hard_delete_refused_when_linked(self, http_client, client_obj, make_service, db):
        make_service(client_obj)
        db.session.commit()
        cid = client_obj.id
        resp = http_client.post(f'/clients/{cid}/hard-delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'Cannot permanently delete' in resp.data
        assert b'1 service' in resp.data
        db.session.expire_all()
        assert db.session.get(Client, cid) is not None

    def test_api_client_rate(self, http_client, client_obj, db):
        client_obj.default_mowing_rate = 65.0
        db.session.commit()
        resp = http_client.get(f'/api/client/{client_obj.id}/rate')
        assert resp.status_code == 200
        assert resp.get_json()['rate'] == 65.0

    def test_api_client_balance(self, http_client, client_obj, make_invoice, db):
        make_invoice(client_obj, 200)
        db.session.commit()
        resp = http_client.get(f'/api/client/{client_obj.id}/balance')
        data = resp.get_json()
        assert data['balance'] == 200
        assert data['unapplied_credit'] == 0

    def test_api_requires_login(self, anon_client):
        resp = anon_client.get('/api/client/1/rate')
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# TestServiceRoutes
# ---------------------------------------------------------------------------

class TestServiceRoutes:
    """Service list/edit/delete, quick-add (grid + checklist), bulk delete, API."""

    def test_service_list_renders(self, http_client, db):
        resp = http_client.get('/services')
        assert resp.status_code == 200

    def test_service_list_type_filter(self, http_client, client_obj, make_service, db):
        make_service(client_obj, service_type='mowing')
        make_service(client_obj, service_type='mulching')
        db.session.commit()
        resp = http_client.get('/services?type=mowing')
        assert resp.status_code == 200

    def test_quick_add_get(self, http_client, db):
        resp = http_client.get('/services/quick-add')
        assert resp.status_code == 200

    def test_grid_create_success(self, http_client, client_obj, db):
        db.session.commit()
        data = grid_rows(None, {
            'service_date': eastern_today().isoformat(),
            'client_id': str(client_obj.id),
            'service_type': 'mowing',
            'amount': '75',
            'description': 'Front and back',
        })
        data['mode'] = 'grid'
        resp = http_client.post('/services/quick-add', data=data, follow_redirects=True)
        assert resp.status_code == 200
        assert b'service(s) recorded' in resp.data
        svc = Service.query.filter_by(client_id=client_obj.id).one()
        assert svc.amount == 75.0
        assert svc.description == 'Front and back'

    def test_grid_create_invalid_type(self, http_client, client_obj, db):
        db.session.commit()
        data = grid_rows(None, {
            'service_date': eastern_today().isoformat(),
            'client_id': str(client_obj.id),
            'service_type': 'invalid_type',
            'amount': '50',
        })
        data['mode'] = 'grid'
        resp = http_client.post('/services/quick-add', data=data, follow_redirects=True)
        assert b'choose a service type' in resp.data
        assert Service.query.count() == 0

    def test_grid_create_invalid_amount(self, http_client, client_obj, db):
        db.session.commit()
        data = grid_rows(None, {
            'service_date': eastern_today().isoformat(),
            'client_id': str(client_obj.id),
            'service_type': 'mowing',
            'amount': 'abc',
        })
        data['mode'] = 'grid'
        resp = http_client.post('/services/quick-add', data=data, follow_redirects=True)
        assert b'enter a valid amount' in resp.data

    def test_grid_create_invalid_date(self, http_client, client_obj, db):
        db.session.commit()
        data = grid_rows(None, {
            'service_date': 'not-a-date',
            'client_id': str(client_obj.id),
            'service_type': 'mowing',
            'amount': '50',
        })
        data['mode'] = 'grid'
        resp = http_client.post('/services/quick-add', data=data, follow_redirects=True)
        assert b'enter a valid date' in resp.data

    def test_grid_create_unknown_client_rejected(self, http_client, db):
        data = grid_rows(None, {
            'service_date': eastern_today().isoformat(),
            'client_id': '9999',
            'service_type': 'mowing',
            'amount': '50',
        })
        data['mode'] = 'grid'
        resp = http_client.post('/services/quick-add', data=data, follow_redirects=True)
        assert b'choose a client' in resp.data
        assert Service.query.count() == 0

    def test_grid_all_or_nothing(self, http_client, client_obj, db):
        """One bad row rejects the whole batch."""
        db.session.commit()
        data = grid_rows(None,
                         {'service_date': eastern_today().isoformat(), 'client_id': str(client_obj.id),
                          'service_type': 'mowing', 'amount': '50'},
                         {'service_date': eastern_today().isoformat(), 'client_id': str(client_obj.id),
                          'service_type': 'mowing', 'amount': '-5'})
        data['mode'] = 'grid'
        resp = http_client.post('/services/quick-add', data=data, follow_redirects=True)
        assert b'Row 2' in resp.data
        assert Service.query.count() == 0

    def test_grid_blank_rows_ignored(self, http_client, db):
        # The Type select has no empty option, so a browser posts a service_type
        # even on an untouched row. Build the row the way the browser sends it.
        data = grid_rows(None, {'service_date': eastern_today().isoformat(),
                                'service_type': 'mowing'})
        data['mode'] = 'grid'
        resp = http_client.post('/services/quick-add', data=data, follow_redirects=True)
        assert b'No services entered' in resp.data

    def test_grid_trailing_blank_row_ignored(self, http_client, client_obj, db):
        """The grid keeps an empty row below the last filled one; it must not block the save."""
        db.session.commit()
        data = grid_rows(None,
                         {'service_date': eastern_today().isoformat(),
                          'client_id': str(client_obj.id),
                          'service_type': 'mowing', 'amount': '75'},
                         {'service_date': eastern_today().isoformat(),
                          'service_type': 'mowing'})
        data['mode'] = 'grid'
        resp = http_client.post('/services/quick-add', data=data, follow_redirects=True)
        assert b'1 service(s) recorded' in resp.data
        assert b'choose a client' not in resp.data
        assert Service.query.count() == 1

    def test_service_edit_get(self, http_client, client_obj, make_service, db):
        svc = make_service(client_obj)
        db.session.commit()
        resp = http_client.get(f'/services/{svc.id}/edit')
        assert resp.status_code == 200

    def test_service_edit_updates(self, http_client, client_obj, make_service, db):
        svc = make_service(client_obj, amount=50)
        db.session.commit()
        sid = svc.id
        resp = http_client.post(f'/services/{sid}/edit', data={
            'service_date': eastern_today().isoformat(),
            'service_type': 'mulching',
            'amount': '120',
            'description': 'Updated',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'Service updated' in resp.data
        db.session.expire_all()
        s = db.session.get(Service, sid)
        assert s.amount == 120.0
        assert s.service_type == 'mulching'

    def test_service_edit_invalid_type(self, http_client, client_obj, make_service, db):
        svc = make_service(client_obj)
        db.session.commit()
        resp = http_client.post(f'/services/{svc.id}/edit', data={
            'service_date': eastern_today().isoformat(),
            'service_type': 'invalid_type',
            'amount': '50',
        }, follow_redirects=True)
        assert b'Invalid service type' in resp.data

    @pytest.mark.parametrize('amount, message', [
        ('nan', b'valid values'), ('-50', b'more than $0'), ('0', b'more than $0'),
    ])
    def test_service_edit_bad_amount_rejected(self, http_client, client_obj, make_service,
                                              amount, message, db):
        svc = make_service(client_obj, amount=50)
        db.session.commit()
        sid = svc.id
        resp = http_client.post(f'/services/{sid}/edit', data={
            'service_date': eastern_today().isoformat(),
            'service_type': 'mowing',
            'amount': amount,
        }, follow_redirects=True)
        assert message in resp.data
        db.session.expire_all()
        assert db.session.get(Service, sid).amount == 50.0

    def test_grid_create_nan_amount(self, http_client, client_obj, db):
        db.session.commit()
        data = grid_rows(None, {
            'service_date': eastern_today().isoformat(),
            'client_id': str(client_obj.id),
            'service_type': 'mowing',
            'amount': 'nan',
        })
        data['mode'] = 'grid'
        resp = http_client.post('/services/quick-add', data=data, follow_redirects=True)
        assert b'enter a valid amount' in resp.data
        assert Service.query.count() == 0

    def test_service_delete_success(self, http_client, client_obj, make_service, db):
        svc = make_service(client_obj)
        db.session.commit()
        sid = svc.id
        resp = http_client.post(f'/services/{sid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'Service deleted' in resp.data
        assert db.session.get(Service, sid) is None

    def test_service_delete_blocked_when_invoiced(self, http_client, client_obj, make_service,
                                                   make_invoice, db):
        svc = make_service(client_obj, amount=100)
        inv = make_invoice(client_obj, 100)
        db.session.add(InvoiceLineItem(invoice_id=inv.id, service_id=svc.id, description='Test', amount=100))
        db.session.commit()
        sid = svc.id
        resp = http_client.post(f'/services/{sid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'Cannot delete' in resp.data
        assert db.session.get(Service, sid) is not None

    def test_service_delete_blocked_when_expense_linked(self, http_client, client_obj, make_service,
                                                         make_expense_category, make_expense, db):
        svc = make_service(client_obj)
        make_expense(make_expense_category(), service=svc)
        db.session.commit()
        sid = svc.id
        resp = http_client.post(f'/services/{sid}/delete', follow_redirects=True)
        assert b'expense(s) are linked' in resp.data
        assert db.session.get(Service, sid) is not None

    def test_checklist_creates_services(self, http_client, db):
        c = Client(first_name='Weekly', last_name='Mow',
                   address_street='1 St', city='C', state='TX',
                   zip_code='00000', is_weekly=True, default_mowing_rate=60)
        db.session.add(c)
        db.session.commit()
        resp = http_client.post('/services/quick-add', data={
            'mode': 'checklist',
            'service_date': eastern_today().isoformat(),
            f'client_{c.id}': 'on',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'mowing service' in resp.data.lower()
        svc = Service.query.filter_by(client_id=c.id).first()
        assert svc is not None
        assert svc.amount == 60.0

    def test_checklist_rate_override(self, http_client, db):
        c = Client(first_name='Override', last_name='Rate',
                   address_street='1 St', city='C', state='TX',
                   zip_code='00000', is_weekly=True, default_mowing_rate=60)
        db.session.add(c)
        db.session.commit()
        http_client.post('/services/quick-add', data={
            'mode': 'checklist',
            'service_date': eastern_today().isoformat(),
            f'client_{c.id}': 'on',
            f'rate_{c.id}': '85',
        }, follow_redirects=True)
        svc = Service.query.filter_by(client_id=c.id).first()
        assert svc.amount == 85.0

    def test_raising_the_rate_does_not_reprice_history(self, http_client, db):
        """A client's default rate is a starting value for new work, never a
        retroactive one: past services, their invoice line items, and the
        invoice total all keep the price that was charged at the time."""
        c = Client(first_name='Rate', last_name='Rise',
                   address_street='1 St', city='C', state='TX',
                   zip_code='00000', is_weekly=True, default_mowing_rate=45)
        db.session.add(c)
        db.session.commit()

        http_client.post('/services/quick-add', data={
            'mode': 'checklist',
            'service_date': eastern_today().isoformat(),
            f'client_{c.id}': 'on',
        })
        svc = Service.query.filter_by(client_id=c.id).one()
        assert svc.amount == 45.0

        http_client.post('/invoices/new', data={
            'client_id': str(c.id),
            'invoice_date': eastern_today().isoformat(),
            'service_ids': [str(svc.id)],
        })
        inv = Invoice.query.filter_by(client_id=c.id).one()

        http_client.post(f'/clients/{c.id}/edit', data={
            'first_name': 'Rate', 'last_name': 'Rise',
            'address_street': '1 St', 'city': 'C', 'state': 'TX',
            'zip_code': '00000', 'is_weekly': 'on',
            'default_mowing_rate': '99', 'active': 'on',
        })
        db.session.refresh(c)
        assert c.default_mowing_rate == 99.0

        db.session.refresh(svc)
        db.session.refresh(inv)
        assert svc.amount == 45.0
        assert [li.amount for li in inv.line_items] == [45.0]
        assert inv.subtotal == 45.0

    def test_service_edit_page_does_not_autofill_on_load(self, http_client, client_obj,
                                                        make_service, db):
        """The mowing autofill must only run on a client/type change.

        It used to also run at page load, so opening an old service after a rate
        rise put the client's *current* rate in the box and the next save
        repriced history. The rendered HTML is identical either way — the tell
        is a statement-level ``updateRate()`` call in the page's script.
        """
        svc = make_service(client_obj, amount=45)
        db.session.commit()
        body = http_client.get(f'/services/{svc.id}/edit').get_data(as_text=True)
        assert 'value="45.0"' in body
        assert re.search(r'^\s*updateRate\(\);', body, re.M) is None

    def test_bulk_delete_success(self, http_client, client_obj, make_service, db):
        s1 = make_service(client_obj)
        s2 = make_service(client_obj)
        db.session.commit()
        resp = http_client.post('/services/bulk-delete', data={
            'service_ids': [str(s1.id), str(s2.id)],
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'deleted' in resp.data.lower()
        assert db.session.get(Service, s1.id) is None
        assert db.session.get(Service, s2.id) is None

    def test_bulk_delete_skips_invoiced(self, http_client, client_obj, make_service, make_invoice, db):
        svc = make_service(client_obj, amount=100)
        inv = make_invoice(client_obj, 100)
        db.session.add(InvoiceLineItem(invoice_id=inv.id, service_id=svc.id, description='Test', amount=100))
        free_svc = make_service(client_obj, amount=50)
        db.session.commit()
        resp = http_client.post('/services/bulk-delete', data={
            'service_ids': [str(svc.id), str(free_svc.id)],
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert db.session.get(Service, svc.id) is not None
        assert db.session.get(Service, free_svc.id) is None

    def test_api_uninvoiced_services(self, http_client, client_obj, make_service, db):
        make_service(client_obj, amount=75)
        db.session.commit()
        resp = http_client.get(f'/api/client/{client_obj.id}/uninvoiced-services')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['amount'] == 75


# ---------------------------------------------------------------------------
# TestInvoiceRoutes
# ---------------------------------------------------------------------------

class TestInvoiceRoutes:
    """Invoice CRUD, void, delete, print, quick-create, bulk actions."""

    def test_invoice_list_renders(self, http_client, db):
        resp = http_client.get('/invoices')
        assert resp.status_code == 200

    def test_invoice_list_status_filter(self, http_client, client_obj, make_invoice, db):
        make_invoice(client_obj, 100)
        db.session.commit()
        resp = http_client.get('/invoices?status=unpaid')
        assert resp.status_code == 200

    def test_invoice_create_get(self, http_client, db):
        resp = http_client.get('/invoices/new')
        assert resp.status_code == 200

    def test_invoice_create_with_services(self, http_client, client_obj, make_service, db):
        svc1 = make_service(client_obj, amount=50)
        svc2 = make_service(client_obj, amount=75)
        db.session.commit()
        resp = http_client.post('/invoices/new', data={
            'client_id': str(client_obj.id),
            'invoice_date': eastern_today().isoformat(),
            'service_ids': [str(svc1.id), str(svc2.id)],
        }, follow_redirects=True)
        assert resp.status_code == 200
        inv = Invoice.query.filter_by(client_id=client_obj.id).first()
        assert inv is not None
        assert inv.subtotal == 125.0
        assert len(inv.line_items) == 2

    def test_invoice_generates_number(self, http_client, client_obj, make_service, db):
        svc = make_service(client_obj, amount=100)
        db.session.commit()
        http_client.post('/invoices/new', data={
            'client_id': str(client_obj.id),
            'invoice_date': eastern_today().isoformat(),
            'service_ids': [str(svc.id)],
        })
        inv = Invoice.query.filter_by(client_id=client_obj.id).first()
        assert inv.invoice_number.startswith(f'INV-{eastern_today().year}-')

    def test_invoice_create_auto_applies_credits(self, http_client, client_obj, make_service,
                                                  make_payment, db):
        make_payment(client_obj, 100)
        svc = make_service(client_obj, amount=100)
        db.session.commit()
        http_client.post('/invoices/new', data={
            'client_id': str(client_obj.id),
            'invoice_date': eastern_today().isoformat(),
            'service_ids': [str(svc.id)],
        })
        inv = Invoice.query.filter_by(client_id=client_obj.id).first()
        assert inv.status == 'paid'

    def test_invoice_create_ignores_other_clients_services(self, http_client, client_obj,
                                                            make_service, db):
        other = Client(first_name='Other', last_name='Person', address_street='1',
                       city='C', state='TX', zip_code='00000')
        db.session.add(other)
        db.session.flush()
        foreign = make_service(other, amount=999)
        db.session.commit()
        http_client.post('/invoices/new', data={
            'client_id': str(client_obj.id),
            'invoice_date': eastern_today().isoformat(),
            'service_ids': [str(foreign.id)],
        })
        inv = Invoice.query.filter_by(client_id=client_obj.id).first()
        assert inv.subtotal == 0
        assert inv.line_items == []

    def test_invoice_create_invalid_client(self, http_client, db):
        resp = http_client.post('/invoices/new', data={
            'client_id': 'bad',
            'invoice_date': eastern_today().isoformat(),
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'valid values' in resp.data.lower()

    def test_invoice_create_invalid_date(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.post('/invoices/new', data={
            'client_id': str(client_obj.id),
            'invoice_date': 'not-a-date',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'valid values' in resp.data.lower()

    def test_invoice_detail_renders(self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 200)
        db.session.commit()
        resp = http_client.get(f'/invoices/{inv.id}')
        assert resp.status_code == 200

    def test_invoice_detail_404(self, http_client, db):
        resp = http_client.get('/invoices/9999')
        assert resp.status_code == 404

    def test_invoice_void_success(self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 100)
        db.session.commit()
        inv_id = inv.id
        resp = http_client.post(f'/invoices/{inv_id}/void', follow_redirects=True)
        assert resp.status_code == 200
        assert b'voided' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Invoice, inv_id).status == 'void'
        assert db.session.get(Invoice, inv_id).voided_at is not None

    def test_invoice_void_frees_services(self, http_client, client_obj, make_service, make_invoice, db):
        svc = make_service(client_obj, amount=100)
        inv = make_invoice(client_obj, 100)
        db.session.add(InvoiceLineItem(invoice_id=inv.id, service_id=svc.id, description='Test', amount=100))
        db.session.commit()
        inv_id = inv.id
        http_client.post(f'/invoices/{inv_id}/void')
        db.session.expire_all()
        c = db.session.get(Client, client_obj.id)
        assert any(s.id == svc.id for s in c.uninvoiced_services())

    def test_invoice_void_already_void(self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 100, status='void')
        db.session.commit()
        resp = http_client.post(f'/invoices/{inv.id}/void', follow_redirects=True)
        assert resp.status_code == 200
        assert b'already void' in resp.data.lower()

    def test_invoice_delete_void_success(self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 100, status='void')
        db.session.commit()
        inv_id = inv.id
        resp = http_client.post(f'/invoices/{inv_id}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert db.session.get(Invoice, inv_id) is None

    def test_invoice_delete_non_void_blocked(self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 100)
        db.session.commit()
        inv_id = inv.id
        resp = http_client.post(f'/invoices/{inv_id}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'Only void invoices' in resp.data
        assert db.session.get(Invoice, inv_id) is not None

    def test_invoice_print_renders(self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 100)
        db.session.commit()
        resp = http_client.get(f'/invoices/{inv.id}/print')
        assert resp.status_code == 200

    def test_invoice_quick_create_get(self, http_client, db):
        resp = http_client.get('/invoices/quick-create')
        assert resp.status_code == 200

    def test_invoice_quick_create_batch(self, http_client, client_obj, make_service, db):
        make_service(client_obj, amount=80)
        db.session.commit()
        resp = http_client.post('/invoices/quick-create', data={
            'client_ids': [str(client_obj.id)],
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'invoice' in resp.data.lower()
        inv = Invoice.query.filter_by(client_id=client_obj.id).first()
        assert inv is not None
        assert inv.subtotal == 80.0

    def test_invoice_quick_create_skips_client_without_services(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.post('/invoices/quick-create', data={
            'client_ids': [str(client_obj.id)],
        }, follow_redirects=True)
        assert b'no uninvoiced services' in resp.data
        assert Invoice.query.count() == 0

    def test_bulk_void_and_delete(self, http_client, client_obj, make_invoice, db):
        a = make_invoice(client_obj, 100)
        b = make_invoice(client_obj, 100)
        db.session.commit()
        a_id, b_id = a.id, b.id
        resp = http_client.post('/invoices/bulk-void', data={'invoice_ids[]': [str(a_id), str(b_id)]},
                                follow_redirects=True)
        assert b'2 voided' in resp.data
        db.session.expire_all()
        assert db.session.get(Invoice, a_id).status == 'void'
        resp = http_client.post('/invoices/bulk-delete', data={'invoice_ids[]': [str(a_id)]},
                                follow_redirects=True)
        assert b'1 deleted' in resp.data
        assert db.session.get(Invoice, a_id) is None
        assert db.session.get(Invoice, b_id) is not None

    def test_bulk_delete_skips_non_void(self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 100)
        db.session.commit()
        inv_id = inv.id
        resp = http_client.post('/invoices/bulk-delete', data={'invoice_ids[]': [str(inv_id)]},
                                follow_redirects=True)
        assert b'skipped (not void)' in resp.data
        assert db.session.get(Invoice, inv_id) is not None

    def test_email_without_client_email_is_refused(self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 100)
        db.session.commit()
        resp = http_client.post(f'/invoices/{inv.id}/email', follow_redirects=True)
        assert b'no email address' in resp.data
        db.session.expire_all()
        assert db.session.get(Invoice, inv.id).sent_at is None


# ---------------------------------------------------------------------------
# TestPaymentRoutes
# ---------------------------------------------------------------------------

class TestPaymentRoutes:
    """Payment list, quick-add grid, FIFO application, edit, delete."""

    def _row(self, client, amount, **extra):
        row = {
            'payment_date': eastern_today().isoformat(),
            'client_id': str(client.id),
            'amount': str(amount),
            'payment_method': 'check',
        }
        row.update(extra)
        return row

    def test_payment_list_renders(self, http_client, db):
        resp = http_client.get('/payments')
        assert resp.status_code == 200

    def test_payment_list_method_filter(self, http_client, client_obj, make_payment, db):
        make_payment(client_obj, 100)
        db.session.commit()
        resp = http_client.get('/payments?method=check')
        assert resp.status_code == 200

    def test_quick_add_get(self, http_client, db):
        resp = http_client.get('/payments/quick-add')
        assert resp.status_code == 200

    def test_quick_add_creates_payment(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.post('/payments/quick-add',
                                data=grid_rows(None, self._row(client_obj, 150, reference='Check #1234')),
                                follow_redirects=True)
        assert resp.status_code == 200
        assert b'payment(s) recorded' in resp.data
        p = Payment.query.filter_by(client_id=client_obj.id).one()
        assert p.amount == 150.0
        assert p.reference == 'Check #1234'

    def test_quick_add_applies_fifo(self, http_client, client_obj, make_invoice, db):
        inv1 = make_invoice(client_obj, 100)
        inv2 = make_invoice(client_obj, 100)
        db.session.commit()
        inv1_id, inv2_id = inv1.id, inv2.id
        http_client.post('/payments/quick-add', data=grid_rows(None, self._row(client_obj, 150)))
        db.session.expire_all()
        assert db.session.get(Invoice, inv1_id).status == 'paid'
        assert db.session.get(Invoice, inv2_id).status == 'partial'

    def test_quick_add_reports_unapplied_credit(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.post('/payments/quick-add', data=grid_rows(None, self._row(client_obj, 200)),
                                follow_redirects=True)
        assert b'remains as unapplied credit' in resp.data

    def test_quick_add_zero_rejected(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.post('/payments/quick-add', data=grid_rows(None, self._row(client_obj, 0)),
                                follow_redirects=True)
        assert b'more than $0' in resp.data
        assert Payment.query.count() == 0

    def test_quick_add_negative_rejected(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.post('/payments/quick-add', data=grid_rows(None, self._row(client_obj, -50)),
                                follow_redirects=True)
        assert b'more than $0' in resp.data

    def test_quick_add_invalid_amount(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.post('/payments/quick-add', data=grid_rows(None, self._row(client_obj, 'abc')),
                                follow_redirects=True)
        assert b'enter a valid amount' in resp.data

    @pytest.mark.parametrize('amount', ['nan', 'inf', '1e309'])
    def test_quick_add_non_finite_rejected(self, http_client, client_obj, make_invoice, amount, db):
        # Postgres stores NaN/inf in a float column; SQLite would turn NaN into NULL
        # and fail NOT NULL instead, so the route must reject these before the DB.
        make_invoice(client_obj, 100)
        db.session.commit()
        resp = http_client.post('/payments/quick-add', data=grid_rows(None, self._row(client_obj, amount)),
                                follow_redirects=True)
        assert b'enter a valid amount' in resp.data
        assert Payment.query.count() == 0

    def test_quick_add_rounds_to_cents(self, http_client, client_obj, db):
        db.session.commit()
        http_client.post('/payments/quick-add', data=grid_rows(None, self._row(client_obj, '40.005')))
        assert Payment.query.one().amount == 40.01

    def test_quick_add_unknown_client_rejected(self, http_client, db):
        data = grid_rows(None, {'payment_date': eastern_today().isoformat(), 'client_id': '9999',
                                'amount': '10', 'payment_method': 'check'})
        resp = http_client.post('/payments/quick-add', data=data, follow_redirects=True)
        assert b'choose a client' in resp.data
        assert Payment.query.count() == 0

    def test_quick_add_two_rows_same_client_row_order(self, http_client, client_obj, make_invoice, db):
        inv = make_invoice(client_obj, 100)
        db.session.commit()
        inv_id = inv.id
        http_client.post('/payments/quick-add',
                         data=grid_rows(None, self._row(client_obj, 60), self._row(client_obj, 60)))
        db.session.expire_all()
        assert db.session.get(Invoice, inv_id).status == 'paid'
        payments = Payment.query.order_by(Payment.id).all()
        assert [p.amount_applied for p in payments] == [60.0, 40.0]

    def test_payment_detail_renders(self, http_client, client_obj, make_payment, db):
        p = make_payment(client_obj, 100)
        db.session.commit()
        resp = http_client.get(f'/payments/{p.id}')
        assert resp.status_code == 200

    def test_payment_edit_get(self, http_client, client_obj, make_payment, db):
        p = make_payment(client_obj, 100)
        db.session.commit()
        resp = http_client.get(f'/payments/{p.id}/edit')
        assert resp.status_code == 200

    def test_payment_edit_reapplies_fifo(self, http_client, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        p = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(payment_id=p.id, invoice_id=inv.id, amount_applied=100))
        db.session.commit()
        pid = p.id
        resp = http_client.post(f'/payments/{pid}/edit', data={
            'amount': '50',
            'payment_date': eastern_today().isoformat(),
            'payment_method': 'check',
        }, follow_redirects=True)
        assert resp.status_code == 200
        db.session.expire_all()
        apps = PaymentApplication.query.filter_by(payment_id=pid).all()
        assert len(apps) == 1
        assert apps[0].amount_applied == 50.0

    def test_payment_edit_same_amount_no_reapply(self, http_client, client_obj, make_invoice,
                                                  make_payment, db):
        inv = make_invoice(client_obj, 100)
        p = make_payment(client_obj, 100)
        pa = PaymentApplication(payment_id=p.id, invoice_id=inv.id, amount_applied=100)
        db.session.add(pa)
        db.session.commit()
        pid, pa_id = p.id, pa.id
        resp = http_client.post(f'/payments/{pid}/edit', data={
            'amount': '100',
            'payment_date': eastern_today().isoformat(),
            'payment_method': 'cash',
            'notes': 'Updated notes',
        }, follow_redirects=True)
        assert resp.status_code == 200
        db.session.expire_all()
        assert db.session.get(PaymentApplication, pa_id) is not None

    def test_payment_edit_zero_rejected(self, http_client, client_obj, make_payment, db):
        p = make_payment(client_obj, 100)
        db.session.commit()
        resp = http_client.post(f'/payments/{p.id}/edit', data={
            'amount': '0', 'payment_date': eastern_today().isoformat(), 'payment_method': 'check',
        }, follow_redirects=True)
        assert b'greater than zero' in resp.data.lower()

    def test_payment_edit_nan_rejected(self, http_client, client_obj, make_payment, db):
        p = make_payment(client_obj, 100)
        db.session.commit()
        pid = p.id
        resp = http_client.post(f'/payments/{pid}/edit', data={
            'amount': 'nan', 'payment_date': eastern_today().isoformat(), 'payment_method': 'check',
        }, follow_redirects=True)
        assert b'valid values' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Payment, pid).amount == 100.0

    def test_payment_delete_updates_statuses(self, http_client, client_obj, make_invoice, make_payment, db):
        inv = make_invoice(client_obj, 100)
        p = make_payment(client_obj, 100)
        db.session.add(PaymentApplication(payment_id=p.id, invoice_id=inv.id, amount_applied=100))
        inv.status = 'paid'
        db.session.commit()
        pid, inv_id = p.id, inv.id
        resp = http_client.post(f'/payments/{pid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'deleted' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Invoice, inv_id).status == 'unpaid'

    def test_payment_detail_404(self, http_client, db):
        resp = http_client.get('/payments/9999')
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestExpenseRoutes
# ---------------------------------------------------------------------------

class TestExpenseRoutes:
    """Expense list, quick-add grid, edit (incl. split allocations), delete, CSV, APIs."""

    def _row(self, category, amount, **extra):
        row = {
            'expense_date': eastern_today().isoformat(),
            'amount': str(amount),
            'category_id': str(category.id),
            'payment_method': 'credit_card',
        }
        row.update(extra)
        return row

    def test_expense_list_renders(self, http_client, db):
        resp = http_client.get('/expenses')
        assert resp.status_code == 200

    def test_expense_list_category_filter(self, http_client, make_expense_category, make_expense, db):
        cat = make_expense_category('Fuel')
        make_expense(cat, amount=50)
        db.session.commit()
        resp = http_client.get(f'/expenses?category_id={cat.id}')
        assert resp.status_code == 200

    def test_quick_add_get(self, http_client, make_expense_category, db):
        make_expense_category()
        db.session.commit()
        resp = http_client.get('/expenses/quick-add')
        assert resp.status_code == 200

    def test_quick_add_creates_expense(self, http_client, make_expense_category, db):
        cat = make_expense_category()
        db.session.commit()
        resp = http_client.post('/expenses/quick-add',
                                data=grid_rows(None, self._row(cat, '99.50', description='Test expense')),
                                follow_redirects=True)
        assert resp.status_code == 200
        assert b'expense(s) recorded' in resp.data
        e = Expense.query.one()
        assert e.amount == 99.5
        assert e.description == 'Test expense'

    def test_quick_add_refund(self, http_client, make_expense_category, db):
        cat = make_expense_category()
        db.session.commit()
        resp = http_client.post('/expenses/quick-add',
                                data=grid_rows(None, self._row(cat, 25, payment_method='cash', is_refund='on')),
                                follow_redirects=True)
        assert b'marked as refund' in resp.data
        e = Expense.query.one()
        assert e.is_refund is True
        assert e.effective_amount == -25

    def test_quick_add_with_vendor(self, http_client, make_expense_category, make_vendor, db):
        cat = make_expense_category()
        vendor = make_vendor('Home Depot')
        db.session.commit()
        http_client.post('/expenses/quick-add',
                         data=grid_rows(None, self._row(cat, 200, payment_method='debit_card',
                                                        vendor_id=str(vendor.id))))
        assert Expense.query.one().vendor_id == vendor.id

    def test_quick_add_unknown_vendor_rejected(self, http_client, make_expense_category, db):
        cat = make_expense_category()
        db.session.commit()
        resp = http_client.post('/expenses/quick-add',
                                data=grid_rows(None, self._row(cat, 200, vendor_id='9999')),
                                follow_redirects=True)
        assert b'unknown vendor' in resp.data

    def test_quick_add_nan_rejected(self, http_client, make_expense_category, db):
        cat = make_expense_category()
        db.session.commit()
        resp = http_client.post('/expenses/quick-add', data=grid_rows(None, self._row(cat, 'nan')),
                                follow_redirects=True)
        assert b'enter a valid amount' in resp.data
        assert Expense.query.count() == 0
        assert Expense.query.count() == 0

    def test_quick_add_invalid_amount(self, http_client, make_expense_category, db):
        cat = make_expense_category()
        db.session.commit()
        resp = http_client.post('/expenses/quick-add', data=grid_rows(None, self._row(cat, 'abc')),
                                follow_redirects=True)
        assert b'enter a valid amount' in resp.data

    def test_quick_add_invalid_date(self, http_client, make_expense_category, db):
        cat = make_expense_category()
        db.session.commit()
        resp = http_client.post('/expenses/quick-add',
                                data=grid_rows(None, self._row(cat, 50, expense_date='bad-date')),
                                follow_redirects=True)
        assert b'enter a valid date' in resp.data

    def test_quick_add_invalid_category(self, http_client, db):
        data = grid_rows(None, {'expense_date': eastern_today().isoformat(), 'amount': '50',
                                'category_id': 'bad', 'payment_method': 'cash'})
        resp = http_client.post('/expenses/quick-add', data=data, follow_redirects=True)
        assert b'choose a category' in resp.data

    def test_edit_split_allocations(self, http_client, make_expense_category, make_expense, db):
        cat = make_expense_category()
        exp = make_expense(cat, amount=200)
        c1 = Client(first_name='Split', last_name='A', address_street='1 St', city='C', state='TX',
                    zip_code='00000')
        c2 = Client(first_name='Split', last_name='B', address_street='2 St', city='C', state='TX',
                    zip_code='00000')
        db.session.add_all([c1, c2])
        db.session.commit()
        eid = exp.id
        resp = http_client.post(f'/expenses/{eid}/edit', data={
            'expense_date': eastern_today().isoformat(),
            'amount': '200',
            'category_id': str(cat.id),
            'payment_method': 'cash',
            'split_mode': 'on',
            'alloc_client_id[]': [str(c1.id), str(c2.id)],
            'alloc_amount[]': ['120', '80'],
        }, follow_redirects=True)
        assert resp.status_code == 200
        allocs = ExpenseAllocation.query.filter_by(expense_id=eid).all()
        assert sorted(a.amount for a in allocs) == [80.0, 120.0]
        db.session.expire_all()
        e = db.session.get(Expense, eid)
        assert e.is_split
        assert e.client_id is None

    def test_expense_detail_renders(self, http_client, make_expense_category, make_expense, db):
        cat = make_expense_category()
        exp = make_expense(cat, amount=50)
        db.session.commit()
        resp = http_client.get(f'/expenses/{exp.id}')
        assert resp.status_code == 200

    def test_expense_edit_get(self, http_client, make_expense_category, make_expense, db):
        cat = make_expense_category()
        exp = make_expense(cat, amount=50)
        db.session.commit()
        resp = http_client.get(f'/expenses/{exp.id}/edit')
        assert resp.status_code == 200

    def test_expense_edit_updates(self, http_client, make_expense_category, make_expense, db):
        cat = make_expense_category()
        exp = make_expense(cat, amount=50)
        db.session.commit()
        eid = exp.id
        resp = http_client.post(f'/expenses/{eid}/edit', data={
            'expense_date': eastern_today().isoformat(),
            'amount': '75',
            'category_id': str(cat.id),
            'payment_method': 'cash',
            'description': 'Updated expense',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'updated' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Expense, eid).amount == 75.0

    def test_expense_edit_invalid_values(self, http_client, make_expense_category, make_expense, db):
        cat = make_expense_category()
        exp = make_expense(cat, amount=50)
        db.session.commit()
        resp = http_client.post(f'/expenses/{exp.id}/edit', data={
            'expense_date': 'bad', 'amount': '75', 'category_id': str(cat.id), 'payment_method': 'cash',
        }, follow_redirects=True)
        assert b'valid values' in resp.data.lower()

    def test_expense_delete(self, http_client, make_expense_category, make_expense, db):
        cat = make_expense_category()
        exp = make_expense(cat, amount=50)
        db.session.commit()
        eid = exp.id
        resp = http_client.post(f'/expenses/{eid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'deleted' in resp.data.lower()
        assert db.session.get(Expense, eid) is None

    def test_expense_export_csv(self, http_client, make_expense_category, make_expense, db):
        cat = make_expense_category()
        make_expense(cat, amount=100)
        db.session.commit()
        resp = http_client.get('/expenses/export')
        assert resp.status_code == 200
        assert 'text/csv' in resp.content_type

    def test_expense_export_date_filter(self, http_client, make_expense_category, make_expense, db):
        cat = make_expense_category()
        yesterday = eastern_today() - timedelta(days=1)
        make_expense(cat, amount=100, expense_date=yesterday)
        make_expense(cat, amount=200, expense_date=eastern_today())
        db.session.commit()
        resp = http_client.get(f'/expenses/export?date_from={yesterday.isoformat()}'
                               f'&date_to={yesterday.isoformat()}')
        assert resp.status_code == 200
        lines = resp.data.decode().strip().split('\n')
        assert len(lines) == 2  # header + 1 row

    def test_api_vendor_search(self, http_client, make_vendor, db):
        make_vendor('Acme Supply')
        db.session.commit()
        resp = http_client.get('/api/vendors/search?q=Acme')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['name'] == 'Acme Supply'

    def test_api_expense_categories(self, http_client, make_expense_category, db):
        make_expense_category('Fuel')
        make_expense_category('Equipment')
        db.session.commit()
        resp = http_client.get('/api/expense-categories')
        assert resp.status_code == 200
        assert len(resp.get_json()) == 2


# ---------------------------------------------------------------------------
# TestVendorRoutes
# ---------------------------------------------------------------------------

class TestVendorRoutes:
    """Vendor CRUD."""

    def test_vendor_list_renders(self, http_client, db):
        resp = http_client.get('/vendors')
        assert resp.status_code == 200

    def test_vendor_list_search(self, http_client, make_vendor, db):
        make_vendor('Unique Vendor Name')
        db.session.commit()
        resp = http_client.get('/vendors?search=Unique')
        assert resp.status_code == 200
        assert b'Unique Vendor Name' in resp.data

    def test_vendor_new_get(self, http_client, db):
        resp = http_client.get('/vendors/new')
        assert resp.status_code == 200

    def test_vendor_create_success(self, http_client, db):
        resp = http_client.post('/vendors/new', data={
            'name': 'New Vendor',
            'contact_name': 'John',
            'phone': '555-1234',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'created' in resp.data.lower()
        assert Vendor.query.filter_by(name='New Vendor').first() is not None

    def test_vendor_detail_renders(self, http_client, make_vendor, db):
        v = make_vendor()
        db.session.commit()
        resp = http_client.get(f'/vendors/{v.id}')
        assert resp.status_code == 200

    def test_vendor_detail_404(self, http_client, db):
        resp = http_client.get('/vendors/9999')
        assert resp.status_code == 404

    def test_vendor_edit_get(self, http_client, make_vendor, db):
        v = make_vendor()
        db.session.commit()
        resp = http_client.get(f'/vendors/{v.id}/edit')
        assert resp.status_code == 200

    def test_vendor_edit_updates(self, http_client, make_vendor, db):
        v = make_vendor('Old Name')
        db.session.commit()
        vid = v.id
        resp = http_client.post(f'/vendors/{vid}/edit', data={
            'name': 'Updated Name',
            'active': 'on',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'updated' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Vendor, vid).name == 'Updated Name'

    def test_vendor_delete_soft(self, http_client, make_vendor, db):
        v = make_vendor()
        db.session.commit()
        vid = v.id
        resp = http_client.post(f'/vendors/{vid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'deactivated' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Vendor, vid).active is False

    def test_vendor_deactivated_hidden(self, http_client, make_vendor, db):
        v = make_vendor('Hidden Vendor')
        v.active = False
        db.session.commit()
        resp = http_client.get('/vendors')
        assert resp.status_code == 200
        assert b'Hidden Vendor' not in resp.data


# ---------------------------------------------------------------------------
# TestPayrollRoutes
# ---------------------------------------------------------------------------

class TestPayrollRoutes:
    """Payroll list, single-entry form, quick-add grid, edit, delete."""

    def test_payroll_list_renders(self, http_client, db):
        resp = http_client.get('/payroll')
        assert resp.status_code == 200

    def test_payroll_new_get(self, http_client, db):
        resp = http_client.get('/payroll/new')
        assert resp.status_code == 200

    def test_payroll_create_success(self, http_client, db):
        resp = http_client.post('/payroll/new', data={
            'employee_name': 'John Doe',
            'work_date': eastern_today().isoformat(),
            'hours': '8',
            'hourly_rate': '15',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'Payroll entry recorded' in resp.data
        entry = Payroll.query.first()
        assert entry is not None
        assert entry.total_payment == 120.0

    def test_payroll_create_invalid_hours(self, http_client, db):
        resp = http_client.post('/payroll/new', data={
            'employee_name': 'John Doe',
            'work_date': eastern_today().isoformat(),
            'hours': 'bad',
            'hourly_rate': '15',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'valid values' in resp.data.lower()

    @pytest.mark.parametrize('hours, rate', [('nan', '15'), ('-8', '15'), ('8', '-15'), ('25', '15')])
    def test_payroll_create_bad_numbers_rejected(self, http_client, hours, rate, db):
        resp = http_client.post('/payroll/new', data={
            'employee_name': 'John Doe',
            'work_date': eastern_today().isoformat(),
            'hours': hours,
            'hourly_rate': rate,
        }, follow_redirects=True)
        assert b'valid values' in resp.data.lower()
        assert Payroll.query.count() == 0

    def test_quick_add_get(self, http_client, db):
        resp = http_client.get('/payroll/quick-add')
        assert resp.status_code == 200

    def test_quick_add_creates_linked_entries(self, http_client, make_employee, db):
        emp = make_employee(first_name='Grid', last_name='Worker', rate=20)
        db.session.commit()
        data = grid_rows(None, {
            'employee_id': str(emp.id),
            'work_date': eastern_today().isoformat(),
            'hours': '6',
            'hourly_rate': '20',
        })
        resp = http_client.post('/payroll/quick-add', data=data, follow_redirects=True)
        assert b'payroll entr' in resp.data
        entry = Payroll.query.one()
        assert entry.employee_id == emp.id
        assert entry.employee_name == 'Grid Worker'
        assert entry.total_payment == 120.0

    def test_quick_add_rejects_zero_hours(self, http_client, make_employee, db):
        emp = make_employee()
        db.session.commit()
        data = grid_rows(None, {'employee_id': str(emp.id), 'work_date': eastern_today().isoformat(),
                                'hours': '0', 'hourly_rate': '20'})
        resp = http_client.post('/payroll/quick-add', data=data, follow_redirects=True)
        assert b'hours must be' in resp.data
        assert Payroll.query.count() == 0

    def test_payroll_edit_get(self, http_client, make_payroll, db):
        entry = make_payroll()
        db.session.commit()
        resp = http_client.get(f'/payroll/{entry.id}/edit')
        assert resp.status_code == 200

    def test_payroll_edit_updates(self, http_client, make_payroll, db):
        entry = make_payroll(hours=8, hourly_rate=15)
        db.session.commit()
        eid = entry.id
        resp = http_client.post(f'/payroll/{eid}/edit', data={
            'employee_name': 'Test Worker',
            'work_date': eastern_today().isoformat(),
            'hours': '10',
            'hourly_rate': '20',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'updated' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Payroll, eid).total_payment == 200.0

    def test_payroll_delete(self, http_client, make_payroll, db):
        entry = make_payroll()
        db.session.commit()
        eid = entry.id
        resp = http_client.post(f'/payroll/{eid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'deleted' in resp.data.lower()
        assert db.session.get(Payroll, eid) is None


# ---------------------------------------------------------------------------
# TestEmployeeRoutes
# ---------------------------------------------------------------------------

class TestEmployeeRoutes:
    """Employee CRUD."""

    def test_employee_list_renders(self, http_client, db):
        resp = http_client.get('/employees')
        assert resp.status_code == 200

    def test_employee_list_search(self, http_client, make_employee, db):
        make_employee(first_name='Unique', last_name='Person')
        db.session.commit()
        resp = http_client.get('/employees?search=Unique')
        assert resp.status_code == 200
        assert b'Unique' in resp.data

    def test_employee_new_get(self, http_client, db):
        resp = http_client.get('/employees/new')
        assert resp.status_code == 200

    def test_employee_create_success(self, http_client, db):
        resp = http_client.post('/employees/new', data={
            'first_name': 'Jane',
            'last_name': 'Smith',
            'default_hourly_rate': '18.50',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'added' in resp.data.lower()
        emp = Employee.query.filter_by(first_name='Jane').first()
        assert emp is not None
        assert emp.default_hourly_rate == 18.5

    def test_employee_create_invalid_rate(self, http_client, db):
        resp = http_client.post('/employees/new', data={
            'first_name': 'Bad',
            'last_name': 'Rate',
            'default_hourly_rate': 'xyz',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'valid number' in resp.data.lower()

    def test_employee_create_duplicate_email_rejected(self, http_client, make_employee, db):
        make_employee(email='dup@example.com')
        db.session.commit()
        resp = http_client.post('/employees/new', data={
            'first_name': 'Second', 'last_name': 'Person',
            'default_hourly_rate': '10', 'email': 'DUP@example.com',
        }, follow_redirects=True)
        assert b'already has that email' in resp.data
        assert Employee.query.count() == 1

    def test_employee_edit_get(self, http_client, make_employee, db):
        emp = make_employee()
        db.session.commit()
        resp = http_client.get(f'/employees/{emp.id}/edit')
        assert resp.status_code == 200

    def test_employee_edit_updates(self, http_client, make_employee, db):
        emp = make_employee(first_name='Old', last_name='Name')
        db.session.commit()
        eid = emp.id
        resp = http_client.post(f'/employees/{eid}/edit', data={
            'first_name': 'New',
            'last_name': 'Name',
            'default_hourly_rate': '22',
            'active': 'on',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'updated' in resp.data.lower()
        db.session.expire_all()
        e = db.session.get(Employee, eid)
        assert e.first_name == 'New'
        assert e.default_hourly_rate == 22.0

    def test_employee_delete_soft(self, http_client, make_employee, db):
        emp = make_employee()
        db.session.commit()
        eid = emp.id
        resp = http_client.post(f'/employees/{eid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert b'deactivated' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Employee, eid).active is False

    def test_employee_deactivated_hidden(self, http_client, make_employee, db):
        emp = make_employee(first_name='Ghost', last_name='Worker')
        emp.active = False
        db.session.commit()
        resp = http_client.get('/employees')
        assert resp.status_code == 200
        assert b'Ghost' not in resp.data

    def test_employee_full_name(self, make_employee, db):
        emp = make_employee(first_name='Alice', last_name='Johnson')
        assert emp.full_name == 'Alice Johnson'


# ---------------------------------------------------------------------------
# TestRouteRoutes
# ---------------------------------------------------------------------------

class TestRouteRoutes:
    """Mowing route CRUD + move-client API."""

    def test_route_list_renders(self, http_client, db):
        resp = http_client.get('/mowing-routes')
        assert resp.status_code == 200

    def test_route_list_shows_unassigned(self, http_client, client_obj, db):
        db.session.commit()
        resp = http_client.get('/mowing-routes')
        assert resp.status_code == 200
        assert b'Test' in resp.data

    def test_route_new_get(self, http_client, db):
        resp = http_client.get('/mowing-routes/new')
        assert resp.status_code == 200

    def test_route_create_success(self, http_client, db):
        resp = http_client.post('/mowing-routes/new', data={
            'name': 'Tuesday Route',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'created' in resp.data.lower()
        assert Route.query.filter_by(name='Tuesday Route').first() is not None

    def test_route_create_assigns_clients(self, http_client, client_obj, db):
        db.session.commit()
        cid = client_obj.id
        resp = http_client.post('/mowing-routes/new', data={
            'name': 'With Clients',
            'client_ids': [str(cid)],
        }, follow_redirects=True)
        assert resp.status_code == 200
        db.session.expire_all()
        c = db.session.get(Client, cid)
        assert c.route_id is not None
        assert c.route_position == 1

    def test_route_create_empty_name(self, http_client, db):
        resp = http_client.post('/mowing-routes/new', data={
            'name': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'required' in resp.data.lower()

    def test_route_create_skips_assigned(self, http_client, make_route, client_obj, db):
        route1 = make_route('Existing Route')
        client_obj.route_id = route1.id
        client_obj.route_position = 1
        db.session.commit()
        cid = client_obj.id
        resp = http_client.post('/mowing-routes/new', data={
            'name': 'New Route',
            'client_ids': [str(cid)],
        }, follow_redirects=True)
        assert resp.status_code == 200
        db.session.expire_all()
        assert db.session.get(Client, cid).route_id == route1.id

    def test_route_edit_get(self, http_client, make_route, db):
        route = make_route()
        db.session.commit()
        resp = http_client.get(f'/mowing-routes/{route.id}/edit')
        assert resp.status_code == 200

    def test_route_edit_updates_name(self, http_client, make_route, db):
        route = make_route('Old Name')
        db.session.commit()
        rid = route.id
        resp = http_client.post(f'/mowing-routes/{rid}/edit', data={
            'name': 'New Name',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'updated' in resp.data.lower()
        db.session.expire_all()
        assert db.session.get(Route, rid).name == 'New Name'

    def test_route_edit_removes_clients(self, http_client, make_route, client_obj, db):
        route = make_route()
        client_obj.route_id = route.id
        client_obj.route_position = 1
        db.session.commit()
        rid, cid = route.id, client_obj.id
        resp = http_client.post(f'/mowing-routes/{rid}/edit', data={
            'name': route.name,
            'remove_ids': [str(cid)],
        }, follow_redirects=True)
        assert resp.status_code == 200
        db.session.expire_all()
        assert db.session.get(Client, cid).route_id is None

    def test_route_edit_adds_clients(self, http_client, make_route, db):
        route = make_route()
        c = Client(first_name='New', last_name='Client',
                   address_street='1 St', city='C', state='TX',
                   zip_code='00000')
        db.session.add(c)
        db.session.commit()
        rid, cid = route.id, c.id
        resp = http_client.post(f'/mowing-routes/{rid}/edit', data={
            'name': route.name,
            'client_ids': [str(cid)],
        }, follow_redirects=True)
        assert resp.status_code == 200
        db.session.expire_all()
        assert db.session.get(Client, cid).route_id == rid

    def test_route_edit_reorders(self, http_client, make_route, db):
        route = make_route()
        c1 = Client(first_name='A', last_name='Client',
                    address_street='1 St', city='C', state='TX',
                    zip_code='00000', route_id=route.id, route_position=1)
        c2 = Client(first_name='B', last_name='Client',
                    address_street='2 St', city='C', state='TX',
                    zip_code='00000', route_id=route.id, route_position=2)
        db.session.add_all([c1, c2])
        db.session.commit()
        rid = route.id
        c1_id, c2_id = c1.id, c2.id
        resp = http_client.post(f'/mowing-routes/{rid}/edit', data={
            'name': route.name,
            'ordered_ids': f'{c2_id},{c1_id}',
        }, follow_redirects=True)
        assert resp.status_code == 200
        db.session.expire_all()
        assert db.session.get(Client, c2_id).route_position == 1
        assert db.session.get(Client, c1_id).route_position == 2

    def test_route_delete_unassigns(self, http_client, make_route, client_obj, db):
        route = make_route()
        client_obj.route_id = route.id
        client_obj.route_position = 1
        db.session.commit()
        cid = client_obj.id
        http_client.post(f'/mowing-routes/{route.id}/delete')
        db.session.expire_all()
        assert db.session.get(Client, cid).route_id is None

    def test_route_delete_removes(self, http_client, make_route, db):
        route = make_route()
        db.session.commit()
        rid = route.id
        resp = http_client.post(f'/mowing-routes/{rid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert db.session.get(Route, rid) is None

    def test_api_move_client(self, http_client, make_route, client_obj, db):
        route = make_route('Target Route')
        db.session.commit()
        cid, rid = client_obj.id, route.id
        resp = http_client.post('/api/mowing-routes/move-client', json={
            'client_id': cid,
            'target_route_id': rid,
        })
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        db.session.expire_all()
        assert db.session.get(Client, cid).route_id == rid

    def test_api_move_client_missing_id(self, http_client, db):
        resp = http_client.post('/api/mowing-routes/move-client', json={})
        assert resp.status_code == 400
