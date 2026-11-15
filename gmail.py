"""Gmail API email sending with OAuth2, falling back to Flask-Mail SMTP."""

import base64
import logging
import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app
from flask_mail import Message as FlaskMailMessage

log = logging.getLogger(__name__)


def _get_fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get('FERNET_KEY')
    if not key:
        return None
    return Fernet(key.encode())


def _encrypt(plaintext: str) -> str:
    f = _get_fernet()
    if not f:
        raise RuntimeError('FERNET_KEY not set')
    return f.encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    f = _get_fernet()
    if not f:
        raise RuntimeError('FERNET_KEY not set')
    return f.decrypt(ciphertext.encode()).decode()


def store_tokens(refresh_token: str, email: str) -> None:
    from models import AppSetting, db
    from tz import eastern_now
    AppSetting.set('gmail_refresh_token', _encrypt(refresh_token))
    AppSetting.set('gmail_email', email)
    AppSetting.set('gmail_connected_at', eastern_now().isoformat())
    AppSetting.set('gmail_needs_reconnect', '')
    db.session.commit()


def clear_tokens() -> None:
    from models import AppSetting, db
    for key in ('gmail_refresh_token', 'gmail_email', 'gmail_connected_at',
                'gmail_needs_reconnect'):
        AppSetting.delete_key(key)
    db.session.commit()


def get_gmail_status() -> dict:
    from models import AppSetting
    email = AppSetting.get('gmail_email')
    connected_at = AppSetting.get('gmail_connected_at')
    needs_reconnect = AppSetting.get('gmail_needs_reconnect')
    return {
        'connected': bool(email and connected_at),
        'email': email,
        'connected_at': connected_at,
        'needs_reconnect': bool(needs_reconnect),
    }


def _get_gmail_service():
    """Build a Gmail API service using the stored refresh token."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    from models import AppSetting, db

    encrypted_token = AppSetting.get('gmail_refresh_token')
    if not encrypted_token:
        return None

    try:
        refresh_token = _decrypt(encrypted_token)
    except Exception:
        log.error('Failed to decrypt Gmail refresh token')
        return None

    client_id = os.environ.get('GOOGLE_CLIENT_ID', '')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET', '')

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=client_id,
        client_secret=client_secret,
        scopes=['https://www.googleapis.com/auth/gmail.send'],
    )

    try:
        creds.refresh(Request())
    except Exception as e:
        log.error('Gmail token refresh failed (grant may be revoked): %s', e)
        AppSetting.set('gmail_needs_reconnect', '1')
        db.session.commit()
        return None

    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


def _build_mime_message(subject: str, recipients: list[str], html: str,
                        sender: str,
                        attachments: list[tuple[str, str, bytes]] | None = None):
    """Build a MIME message. Attachments are (filename, content_type, data) tuples."""
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)
    msg.attach(MIMEText(html, 'html'))

    for filename, content_type, data in (attachments or []):
        maintype, subtype = content_type.split('/', 1) if '/' in content_type else ('application', 'octet-stream')
        att = MIMEApplication(data, _subtype=subtype)
        att.add_header('Content-Disposition', 'attachment', filename=filename)
        msg.attach(att)

    return msg


def _send_via_gmail_api(subject: str, recipients: list[str], html: str,
                        sender: str,
                        attachments: list[tuple[str, str, bytes]] | None = None) -> bool:
    """Send via Gmail API. Returns True on success, False if unavailable/failed."""
    service = _get_gmail_service()
    if not service:
        return False

    mime_msg = _build_mime_message(subject, recipients, html, sender, attachments)
    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()

    try:
        service.users().messages().send(
            userId='me', body={'raw': raw}
        ).execute()
        return True
    except Exception as e:
        log.error('Gmail API send failed: %s', e)
        return False


def _send_via_smtp(subject: str, recipients: list[str], html: str,
                   attachments: list[tuple[str, str, bytes]] | None = None) -> bool:
    """Fallback: send via Flask-Mail SMTP."""
    from extensions import mail
    try:
        msg = FlaskMailMessage(subject=subject, recipients=recipients)
        msg.html = html
        for filename, content_type, data in (attachments or []):
            msg.attach(filename=filename, content_type=content_type, data=data)
        mail.send(msg)
        return True
    except Exception as e:
        log.error('SMTP send failed: %s', e)
        return False


def send_email(subject: str, recipients: list[str], html: str,
               attachments: list[tuple[str, str, bytes]] | None = None) -> bool:
    """Send an email via Gmail API (preferred) or SMTP fallback.

    Args:
        subject: Email subject line.
        recipients: List of recipient email addresses.
        html: HTML body content.
        attachments: Optional list of (filename, content_type, data) tuples.

    Returns True on success, False on failure.
    """
    sender = current_app.config.get('MAIL_DEFAULT_SENDER', '')

    status = get_gmail_status()
    if status['connected'] and not status['needs_reconnect']:
        if _send_via_gmail_api(subject, recipients, html, sender, attachments):
            return True
        log.warning('Gmail API failed, falling back to SMTP')

    return _send_via_smtp(subject, recipients, html, attachments)


def send_email_with_app(app, subject: str, recipients: list[str], html: str,
                        attachments: list[tuple[str, str, bytes]] | None = None) -> bool:
    """Same as send_email but accepts an app object for background threads."""
    with app.app_context():
        return send_email(subject, recipients, html, attachments)
