import logging
import os

from flask import Flask
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from extensions import csrf, limiter, login_manager, mail
from models import AppSetting, Invoice, User, db
from routes import register_blueprints

# Re-export business logic so existing imports still work:
#   from app import apply_payment_fifo, auto_apply_credits, ...
from services import (  # noqa: F401
    apply_payment_fifo,  # noqa: F401
    auto_apply_credits,  # noqa: F401
    refresh_client_invoice_statuses,
)

log = logging.getLogger(__name__)

# Dev-only fallbacks. Never used when APP_ENV=production.
_DEV_SECRET_KEY = 'clips-dev-secret-key-change-in-production'
_DEV_DATABASE_URL = 'sqlite:///clips.db'
_DEV_ADMIN_PASSWORD = 'clips2026'


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def create_app() -> Flask:
    application = Flask(__name__)
    application.wsgi_app = ProxyFix(application.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # APP_ENV defaults to production so a deployment that forgets to set it
    # gets the strict behaviour, not the convenient one. `python app.py` and
    # the dev-only scripts opt into development explicitly.
    app_env = os.environ.get('APP_ENV', 'production').strip().lower()
    if app_env not in ('development', 'production'):
        raise RuntimeError(f"APP_ENV must be 'development' or 'production', got {app_env!r}")
    is_dev = app_env == 'development'
    application.config['APP_ENV'] = app_env

    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        if not is_dev:
            raise RuntimeError(
                'SECRET_KEY is not set. Refusing to start in production with a '
                'predictable session key. Generate one with '
                '`python -c "import secrets; print(secrets.token_hex(32))"` '
                'and set it in the service environment.')
        secret_key = _DEV_SECRET_KEY
    application.config['SECRET_KEY'] = secret_key

    # Database: DATABASE_URL (PostgreSQL in production). Only development may
    # fall back to the local SQLite file — a production process that silently
    # opened an empty SQLite database would look exactly like data loss.
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        if not is_dev:
            raise RuntimeError(
                'DATABASE_URL is not set. Refusing to start in production '
                'without an explicit database (check the service environment '
                'and `systemctl daemon-reload`).')
        database_url = _DEV_DATABASE_URL
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    application.config['SQLALCHEMY_DATABASE_URI'] = database_url
    application.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Cookies: Secure by default in production (the site is HTTPS-only);
    # SESSION_COOKIE_SECURE=false is still honoured for an HTTP-only test box.
    cookie_secure = _env_flag('SESSION_COOKIE_SECURE', default=not is_dev)
    application.config['SESSION_COOKIE_HTTPONLY'] = True
    application.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    application.config['SESSION_COOKIE_SECURE'] = cookie_secure
    application.config['REMEMBER_COOKIE_HTTPONLY'] = True
    application.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
    application.config['REMEMBER_COOKIE_SECURE'] = cookie_secure
    application.config['PREFERRED_URL_SCHEME'] = 'http' if is_dev else 'https'

    # Bulk-entry pages (e.g. the expense quick-add grid) can stay open a long
    # time; Flask-WTF's 1-hour default would 400 away a whole grid of typed
    # rows. Session lifetime bounds the token instead.
    application.config['WTF_CSRF_TIME_LIMIT'] = None

    # Nothing in the app accepts an upload, so no legitimate request needs more
    # than this. Werkzeug rejects a bigger body before the form is ever parsed,
    # which is what keeps a multi-megabyte quote-form note out of memory.
    application.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

    application.config['MAIL_SERVER']         = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    application.config['MAIL_PORT']           = int(os.environ.get('MAIL_PORT', 587))
    application.config['MAIL_USE_TLS']        = _env_flag('MAIL_USE_TLS', default=True)
    application.config['MAIL_USERNAME']       = os.environ.get('MAIL_USERNAME')
    application.config['MAIL_PASSWORD']       = os.environ.get('MAIL_PASSWORD')
    application.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'Clips by Rorabaugh <noreply@example.com>')

    # Stripe (customer portal online payments)
    application.config['STRIPE_SECRET_KEY'] = os.environ.get('STRIPE_SECRET_KEY', '')
    application.config['STRIPE_PUBLISHABLE_KEY'] = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
    application.config['STRIPE_WEBHOOK_SECRET'] = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

    # Base URL for background threads (no request context available)
    application.config['BASE_URL'] = os.environ.get('BASE_URL', 'http://127.0.0.1:5000/')

    # Marketing site URL — surfaced in invoice emails as a bookmarkable portal entry point
    application.config['MARKETING_URL'] = os.environ.get('MARKETING_URL', '')

    # Where landing-page quote requests are delivered
    application.config['QUOTE_INBOX'] = os.environ.get('QUOTE_INBOX', '')

    # Business identity — shown on invoices and the landing page
    application.config['PAYMENT_NAME']     = os.environ.get('PAYMENT_NAME', '')
    application.config['VENMO_HANDLE']     = os.environ.get('VENMO_HANDLE', '')
    application.config['CASHAPP_HANDLE']   = os.environ.get('CASHAPP_HANDLE', '')
    application.config['ZELLE_PHONE']      = os.environ.get('ZELLE_PHONE', '')
    application.config['BUSINESS_PHONE']   = os.environ.get('BUSINESS_PHONE', '')
    application.config['BUSINESS_STREET']         = os.environ.get('BUSINESS_STREET', '')
    application.config['BUSINESS_CITY_STATE_ZIP']  = os.environ.get('BUSINESS_CITY_STATE_ZIP', '')

    # First-boot admin account (see _bootstrap_admin)
    application.config['ADMIN_USERNAME'] = os.environ.get('ADMIN_USERNAME', 'admin').strip() or 'admin'
    application.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD') or (_DEV_ADMIN_PASSWORD if is_dev else None)

    # Initialize extensions
    db.init_app(application)
    csrf.init_app(application)
    mail.init_app(application)
    limiter.init_app(application)
    login_manager.init_app(application)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @application.context_processor
    def inject_gmail_status():
        from flask_login import current_user
        if current_user.is_authenticated:
            needs_reconnect = AppSetting.get('gmail_needs_reconnect')
            return {'gmail_needs_reconnect': bool(needs_reconnect)}
        return {'gmail_needs_reconnect': False}

    @application.after_request
    def set_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        if cookie_secure:
            # Only meaningful over HTTPS; a plain-HTTP dev server must not send it.
            response.headers.setdefault('Strict-Transport-Security',
                                        'max-age=31536000; includeSubDomains')
        return response

    # Register all blueprints
    register_blueprints(application)

    # Database initialization — runs on every startup
    with application.app_context():
        init_database(application)

    return application


def init_database(application: Flask | None = None):
    """Create tables if they don't exist and ensure a default admin user."""
    db.create_all()
    # Drop due_date column if it still exists (one-time migration)
    with db.engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE invoices DROP COLUMN due_date'))
            conn.commit()
        except Exception:
            pass  # column already dropped or doesn't exist
    # Migrate employees.name → first_name + last_name (one-time migration)
    with db.engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE employees ADD COLUMN first_name VARCHAR(60) NOT NULL DEFAULT ''"))
            conn.execute(text("ALTER TABLE employees ADD COLUMN last_name VARCHAR(60) NOT NULL DEFAULT ''"))
            conn.execute(text("""
                UPDATE employees SET
                    first_name = CASE WHEN instr(name, ' ') > 0
                                      THEN substr(name, 1, instr(name, ' ') - 1)
                                      ELSE name END,
                    last_name  = CASE WHEN instr(name, ' ') > 0
                                      THEN substr(name, instr(name, ' ') + 1)
                                      ELSE '' END
                WHERE name IS NOT NULL AND name != ''
            """))
            conn.commit()
        except Exception:
            pass  # columns already exist
    with db.engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE employees DROP COLUMN name'))
            conn.commit()
        except Exception:
            pass  # column already dropped or doesn't exist
    # Add route_id and route_position columns to clients (one-time migration)
    with db.engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE clients ADD COLUMN route_id INTEGER REFERENCES routes(id)'))
            conn.commit()
        except Exception:
            pass
    with db.engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE clients ADD COLUMN route_position INTEGER'))
            conn.commit()
        except Exception:
            pass
    # Add portal columns to clients (one-time migration)
    for col_sql in [
        "ALTER TABLE clients ADD COLUMN password_hash VARCHAR(256)",
        "ALTER TABLE clients ADD COLUMN portal_invite_token VARCHAR(256)",
        "ALTER TABLE clients ADD COLUMN portal_invite_sent_at TIMESTAMP",
    ]:
        with db.engine.connect() as conn:
            try:
                conn.execute(text(col_sql))
                conn.commit()
            except Exception:
                pass
    # Add portal columns to employees (one-time migration)
    for col_sql in [
        "ALTER TABLE employees ADD COLUMN email VARCHAR(120) DEFAULT ''",
        "ALTER TABLE employees ADD COLUMN password_hash VARCHAR(256)",
        "ALTER TABLE employees ADD COLUMN portal_invite_token VARCHAR(256)",
        "ALTER TABLE employees ADD COLUMN portal_invite_sent_at TIMESTAMP",
    ]:
        with db.engine.connect() as conn:
            try:
                conn.execute(text(col_sql))
                conn.commit()
            except Exception:
                pass
    # Add employee_id column to payroll (one-time migration)
    with db.engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE payroll ADD COLUMN employee_id INTEGER REFERENCES employees(id)'))
            conn.commit()
        except Exception:
            pass
    # Backfill payroll.employee_id by matching employee_name
    with db.engine.connect() as conn:
        try:
            conn.execute(text("""
                UPDATE payroll SET employee_id = (
                    SELECT e.id FROM employees e
                    WHERE payroll.employee_name = e.first_name || ' ' || e.last_name
                ) WHERE employee_id IS NULL
            """))
            conn.commit()
        except Exception:
            pass
    # Add start_time and end_time columns to time_entries (one-time migration)
    for col_sql in [
        "ALTER TABLE time_entries ADD COLUMN start_time TIME",
        "ALTER TABLE time_entries ADD COLUMN end_time TIME",
    ]:
        with db.engine.connect() as conn:
            try:
                conn.execute(text(col_sql))
                conn.commit()
            except Exception:
                pass
    # Migrate legacy invoice statuses (draft/sent → unpaid)
    db.session.execute(
        db.update(Invoice).where(Invoice.status.in_(['draft', 'sent'])).values(status='unpaid')
    )
    db.session.commit()
    _bootstrap_admin(application)


def _bootstrap_admin(application: Flask | None) -> None:
    """Create the first admin account on an empty users table.

    Production requires ADMIN_PASSWORD (set it once for the first boot, or run
    scripts/init_db.py). Development falls back to the documented dev password.
    """
    if User.query.first() is not None:
        return
    config = application.config if application is not None else {}
    username = config.get('ADMIN_USERNAME', 'admin')
    password = config.get('ADMIN_PASSWORD')
    if not password:
        log.error('No admin user exists and ADMIN_PASSWORD is not set. '
                  'Nobody can log in until you run scripts/init_db.py with '
                  'ADMIN_PASSWORD set (or set it in the service environment '
                  'and restart).')
        return
    admin = User(username=username, name='Admin')
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    if password == _DEV_ADMIN_PASSWORD:
        log.warning('Created development admin user %r with the default dev password.', username)
    else:
        log.info('Created admin user %r from ADMIN_PASSWORD.', username)


if __name__ == '__main__':
    # `python app.py` is the local dev server; gunicorn imports `app:app` and
    # therefore keeps the production default.
    os.environ.setdefault('APP_ENV', 'development')

# Module-level app instance for gunicorn (`app:app`) and the scripts that do
# `from app import app`.
app = create_app()


if __name__ == '__main__':
    app.run(debug=app.config['APP_ENV'] == 'development', host='127.0.0.1', port=5000)
