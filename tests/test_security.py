"""Security and configuration behaviour introduced by the 2026-09 hardening pass:
startup checks, cookie/header defaults, redirect safety, setup-token binding,
the admin account page, and the portal/staff auth flows.
"""

import pytest

from app import create_app
from extensions import limiter
from models import Client, Employee, User
from utils import (
    is_safe_redirect_target,
    make_setup_token,
    parse_amount,
    portal_token_nonce,
    setup_token_matches,
)

# ---------------------------------------------------------------------------
# Startup configuration
# ---------------------------------------------------------------------------

class TestStartupChecks:

    def test_production_refuses_without_secret_key(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.delenv('SECRET_KEY', raising=False)
        with pytest.raises(RuntimeError, match='SECRET_KEY'):
            create_app()

    def test_production_refuses_without_database_url(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.delenv('DATABASE_URL', raising=False)
        with pytest.raises(RuntimeError, match='DATABASE_URL'):
            create_app()

    def test_unknown_app_env_rejected(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'staging')
        with pytest.raises(RuntimeError, match='APP_ENV'):
            create_app()

    def test_production_defaults(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')
        monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
        app = create_app()
        limiter.enabled = False
        assert app.config['SESSION_COOKIE_SECURE'] is True
        assert app.config['REMEMBER_COOKIE_SECURE'] is True
        assert app.config['PREFERRED_URL_SCHEME'] == 'https'
        with app.app_context():
            assert User.query.first() is None, 'production must not seed a default admin'
        resp = app.test_client().get('/login', base_url='https://example.test')
        assert 'Strict-Transport-Security' in resp.headers

    def test_production_bootstraps_admin_from_env(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')
        monkeypatch.setenv('ADMIN_USERNAME', 'owner')
        monkeypatch.setenv('ADMIN_PASSWORD', 'BootstrapPass123')
        app = create_app()
        with app.app_context():
            user = User.query.one()
            assert user.username == 'owner'
            assert user.check_password('BootstrapPass123')

    def test_session_cookie_secure_override(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('SECRET_KEY', 'x' * 32)
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')
        monkeypatch.setenv('SESSION_COOKIE_SECURE', 'false')
        app = create_app()
        assert app.config['SESSION_COOKIE_SECURE'] is False

    def test_development_defaults(self, app):
        assert app.config['SESSION_COOKIE_SECURE'] is False
        resp = app.test_client().get('/login')
        assert resp.headers['X-Content-Type-Options'] == 'nosniff'
        assert resp.headers['X-Frame-Options'] == 'SAMEORIGIN'
        assert 'Strict-Transport-Security' not in resp.headers


# ---------------------------------------------------------------------------
# utils
# ---------------------------------------------------------------------------

class TestUtils:

    @pytest.mark.parametrize('target', ['/dashboard', '/portal/invoices/3?x=1', '/a/b#c'])
    def test_safe_targets(self, target):
        assert is_safe_redirect_target(target)

    @pytest.mark.parametrize('target', [
        None, '', '//evil.example', '/\\evil.example', 'https://evil.example/x',
        'javascript:alert(1)', 'dashboard', 'http:/evil',
    ])
    def test_unsafe_targets(self, target):
        assert not is_safe_redirect_target(target)

    def test_nonce_changes_with_password_hash(self):
        assert portal_token_nonce(None) == portal_token_nonce('')
        assert portal_token_nonce('hash-a') != portal_token_nonce('hash-b')

    @pytest.mark.parametrize('raw, expected', [
        ('40', 40.0), (' 40.5 ', 40.5), ('19.99', 19.99), ('2.675', 2.68),
        ('40.004', 40.0), ('-12.345', -12.35), ('0', 0.0), (0, 0.0),
    ])
    def test_parse_amount_rounds_half_up(self, raw, expected):
        assert parse_amount(raw) == expected

    @pytest.mark.parametrize('raw', [
        '', 'abc', None, 'nan', 'NaN', '-nan', 'inf', '-Infinity', '1e309', '1000000.01',
    ])
    def test_parse_amount_rejects(self, raw):
        with pytest.raises(ValueError):
            parse_amount(raw)

    def test_parse_amount_custom_maximum(self):
        assert parse_amount('24', maximum=24) == 24.0
        with pytest.raises(ValueError):
            parse_amount('24.25', maximum=24)

    def test_token_round_trip(self, app, client_obj):
        token = make_setup_token(app.config['SECRET_KEY'], 'client_id', client_obj)
        from itsdangerous import URLSafeTimedSerializer
        data = URLSafeTimedSerializer(app.config['SECRET_KEY']).loads(token)
        assert data['client_id'] == client_obj.id
        assert setup_token_matches(data, client_obj)
        client_obj.set_portal_password('SomethingNew1')
        assert not setup_token_matches(data, client_obj)


# ---------------------------------------------------------------------------
# Admin login + account page
# ---------------------------------------------------------------------------

class TestAdminAuth:

    def test_login_rejects_protocol_relative_next(self, anon_client):
        resp = anon_client.post('/login?next=//evil.example',
                                data={'username': 'admin', 'password': 'clips2026'})
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/dashboard')

    def test_login_honours_safe_next(self, anon_client):
        resp = anon_client.post('/login?next=/clients',
                                data={'username': 'admin', 'password': 'clips2026'})
        assert resp.headers['Location'].endswith('/clients')

    def test_account_page_requires_login(self, anon_client):
        assert anon_client.get('/settings/account').status_code == 302

    def test_account_page_renders(self, http_client):
        resp = http_client.get('/settings/account')
        assert resp.status_code == 200
        assert b'Change Password' in resp.data

    def test_sidebar_links_account_page(self, http_client):
        assert b'/settings/account' in http_client.get('/dashboard').data

    def test_wrong_current_password(self, http_client):
        resp = http_client.post('/settings/account', data={
            'current_password': 'wrong', 'new_password': 'NewPassword12345',
            'confirm_password': 'NewPassword12345'}, follow_redirects=True)
        assert b'Current password is incorrect' in resp.data

    def test_short_new_password(self, http_client):
        resp = http_client.post('/settings/account', data={
            'current_password': 'clips2026', 'new_password': 'short',
            'confirm_password': 'short'}, follow_redirects=True)
        assert b'at least 12 characters' in resp.data

    def test_mismatched_confirmation(self, http_client):
        resp = http_client.post('/settings/account', data={
            'current_password': 'clips2026', 'new_password': 'NewPassword12345',
            'confirm_password': 'NewPassword12346'}, follow_redirects=True)
        assert b'do not match' in resp.data

    def test_same_password_rejected(self, http_client):
        resp = http_client.post('/settings/account', data={
            'current_password': 'clips2026', 'new_password': 'clips2026clips2026',
            'confirm_password': 'clips2026clips2026'}, follow_redirects=True)
        # 'clips2026clips2026' differs from the current one, so this must succeed;
        # the same-password check is exercised below with the exact value.
        assert b'Password updated' in resp.data
        resp = http_client.post('/settings/account', data={
            'current_password': 'clips2026clips2026', 'new_password': 'clips2026clips2026',
            'confirm_password': 'clips2026clips2026'}, follow_redirects=True)
        assert b'must be different' in resp.data

    def test_password_change_round_trip(self, app, http_client):
        resp = http_client.post('/settings/account', data={
            'current_password': 'clips2026', 'new_password': 'NewPassword12345',
            'confirm_password': 'NewPassword12345'}, follow_redirects=True)
        assert b'Password updated' in resp.data
        http_client.post('/logout')
        fresh = app.test_client()
        assert fresh.post('/login', data={'username': 'admin', 'password': 'clips2026'}).status_code == 200
        assert fresh.post('/login', data={'username': 'admin', 'password': 'NewPassword12345'}).status_code == 302


# ---------------------------------------------------------------------------
# Customer portal
# ---------------------------------------------------------------------------

@pytest.fixture
def portal_client_obj(db):
    c = Client(first_name='Ann', last_name='Portal', address_street='1 A St', city='X',
               state='OH', zip_code='44313', email='ann@example.com')
    db.session.add(c)
    db.session.commit()
    return c


class TestPortalAuth:

    def test_setup_token_lifecycle(self, app, anon_client, portal_client_obj, db):
        token = make_setup_token(app.config['SECRET_KEY'], 'client_id', portal_client_obj)

        assert anon_client.get(f'/portal/setup-password?token={token}').status_code == 200

        resp = anon_client.post(f'/portal/setup-password?token={token}',
                                data={'password': 'ClientPass1', 'confirm_password': 'ClientPass1'})
        assert resp.status_code == 302 and '/portal' in resp.headers['Location']
        anon_client.post('/portal/logout')

        # The same link must not reset the password a second time.
        resp = anon_client.get(f'/portal/setup-password?token={token}', follow_redirects=True)
        assert b'no longer valid' in resp.data

        # A link issued after the change (the "email me a link" flow) still works.
        db.session.expire_all()
        fresh = make_setup_token(app.config['SECRET_KEY'], 'client_id', db.session.get(Client, portal_client_obj.id))
        assert anon_client.get(f'/portal/setup-password?token={fresh}').status_code == 200

    def test_setup_rejects_short_password(self, app, anon_client, portal_client_obj):
        token = make_setup_token(app.config['SECRET_KEY'], 'client_id', portal_client_obj)
        resp = anon_client.post(f'/portal/setup-password?token={token}',
                                data={'password': 'short', 'confirm_password': 'short'})
        assert b'at least 8 characters' in resp.data
        assert not portal_client_obj.has_portal_access

    def test_setup_rejects_garbage_token(self, anon_client):
        resp = anon_client.get('/portal/setup-password?token=not-a-token', follow_redirects=True)
        assert b'Invalid link' in resp.data

    def test_setup_rejects_inactive_client(self, app, anon_client, portal_client_obj, db):
        token = make_setup_token(app.config['SECRET_KEY'], 'client_id', portal_client_obj)
        portal_client_obj.active = False
        db.session.commit()
        resp = anon_client.get(f'/portal/setup-password?token={token}', follow_redirects=True)
        assert b'deactivated' in resp.data

    def test_login_next_handling(self, anon_client, portal_client_obj, db):
        portal_client_obj.set_portal_password('ClientPass1')
        db.session.commit()
        resp = anon_client.post('/portal/login?next=//evil.example',
                                data={'email': 'ann@example.com', 'password': 'ClientPass1'})
        assert resp.status_code == 302
        assert resp.headers['Location'].rstrip('/').endswith('/portal')
        anon_client.post('/portal/logout')
        resp = anon_client.post('/portal/login?next=/portal/invoices/1',
                                data={'email': 'ann@example.com', 'password': 'ClientPass1'})
        assert resp.headers['Location'].endswith('/portal/invoices/1')

    def test_login_is_case_insensitive_on_email(self, anon_client, portal_client_obj, db):
        portal_client_obj.set_portal_password('ClientPass1')
        db.session.commit()
        resp = anon_client.post('/portal/login', data={'email': 'ANN@Example.com', 'password': 'ClientPass1'})
        assert resp.status_code == 302

    def test_client_cannot_see_other_clients_invoice(self, anon_client, portal_client_obj, client_obj,
                                                      make_invoice, db):
        other_inv = make_invoice(client_obj, 100)
        portal_client_obj.set_portal_password('ClientPass1')
        db.session.commit()
        anon_client.post('/portal/login', data={'email': 'ann@example.com', 'password': 'ClientPass1'})
        assert anon_client.get(f'/portal/invoices/{other_inv.id}').status_code == 404

    def test_dashboard_requires_portal_login(self, anon_client):
        resp = anon_client.get('/portal/')
        assert resp.status_code == 302
        assert '/portal/login' in resp.headers['Location']

    def test_request_link_never_reveals_existence(self, anon_client):
        resp = anon_client.post('/portal/request-link', data={'email': 'nobody@example.com'},
                                follow_redirects=True)
        assert b'If an account exists' in resp.data


# ---------------------------------------------------------------------------
# Staff portal
# ---------------------------------------------------------------------------

class TestStaffAuth:

    def test_setup_token_lifecycle(self, app, anon_client, make_employee, db):
        emp = make_employee(email='crew@example.com')
        db.session.commit()
        token = make_setup_token(app.config['SECRET_KEY'], 'employee_id', emp)
        resp = anon_client.post(f'/staff/setup-password?token={token}',
                                data={'password': 'StaffPass1', 'confirm_password': 'StaffPass1'})
        assert resp.status_code == 302
        anon_client.post('/staff/logout')
        resp = anon_client.get(f'/staff/setup-password?token={token}', follow_redirects=True)
        assert b'no longer valid' in resp.data
        db.session.expire_all()
        assert db.session.get(Employee, emp.id).has_portal_access

    def test_login_rejects_protocol_relative_next(self, anon_client, make_employee, db):
        emp = make_employee(email='crew@example.com')
        emp.set_portal_password('StaffPass1')
        db.session.commit()
        resp = anon_client.post('/staff/login?next=//evil.example',
                                data={'email': 'crew@example.com', 'password': 'StaffPass1'})
        assert resp.status_code == 302
        assert resp.headers['Location'].rstrip('/').endswith('/staff')

    def test_timesheet_requires_staff_login(self, anon_client):
        resp = anon_client.get('/staff/timesheet')
        assert resp.status_code == 302
        assert '/staff/login' in resp.headers['Location']
