"""Settings blueprint — Gmail OAuth2 connection management."""

import base64
import os

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from extensions import limiter
from gmail import clear_tokens, get_gmail_status, store_tokens
from models import db
from utils import MIN_ADMIN_PASSWORD_LENGTH

bp = Blueprint('settings', __name__)

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'openid',
    'email',
]


# ---------------------------------------------------------------------------
# Account (admin password)
# ---------------------------------------------------------------------------

@bp.route('/settings/account', methods=['GET', 'POST'])
@login_required
@limiter.limit('5 per minute', methods=['POST'])
def account_settings():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')

        if not current_user.check_password(current):
            flash('Current password is incorrect.', 'danger')
        elif len(new) < MIN_ADMIN_PASSWORD_LENGTH:
            flash(f'New password must be at least {MIN_ADMIN_PASSWORD_LENGTH} characters.', 'danger')
        elif new != confirm:
            flash('New passwords do not match.', 'danger')
        elif current_user.check_password(new):
            flash('New password must be different from the current one.', 'danger')
        else:
            current_user.set_password(new)
            db.session.commit()
            flash('Password updated.', 'success')
        return redirect(url_for('settings.account_settings'))

    return render_template('settings_account.html',
                           min_length=MIN_ADMIN_PASSWORD_LENGTH)


def _oauth_configured() -> bool:
    return bool(os.environ.get('GOOGLE_CLIENT_ID') and
                os.environ.get('GOOGLE_CLIENT_SECRET') and
                os.environ.get('FERNET_KEY'))


@bp.route('/settings/gmail')
@login_required
def gmail_settings():
    status = get_gmail_status()
    return render_template('settings_gmail.html',
                           status=status,
                           oauth_configured=_oauth_configured())


@bp.route('/settings/gmail/connect')
@login_required
def gmail_connect():
    if not _oauth_configured():
        flash('Gmail OAuth is not configured. Set GOOGLE_CLIENT_ID, '
              'GOOGLE_CLIENT_SECRET, and FERNET_KEY environment variables.', 'danger')
        return redirect(url_for('settings.gmail_settings'))

    import secrets
    from urllib.parse import urlencode

    redirect_uri = url_for('settings.gmail_callback', _external=True, _scheme='https')
    state = secrets.token_urlsafe(32)

    params = {
        'client_id': os.environ['GOOGLE_CLIENT_ID'],
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'access_type': 'offline',
        'prompt': 'select_account consent',
        'state': state,
    }

    authorization_url = 'https://accounts.google.com/o/oauth2/auth?' + urlencode(params)

    session['gmail_oauth_state'] = state
    return redirect(authorization_url)


@bp.route('/settings/gmail/callback')
@login_required
def gmail_callback():
    if not _oauth_configured():
        flash('Gmail OAuth is not configured.', 'danger')
        return redirect(url_for('settings.gmail_settings'))

    from urllib.parse import parse_qs, urlparse

    import requests as http_requests

    # Extract authorization code and state from the callback URL
    authorization_response = request.url.replace('http://', 'https://', 1)
    parsed = urlparse(authorization_response)
    qs = parse_qs(parsed.query)

    # Check for error from Google (e.g. access_denied, admin_policy_enforced)
    error = qs.get('error', [None])[0]
    if error:
        error_desc = qs.get('error_description', [''])[0]
        current_app.logger.warning('Gmail OAuth error from Google: %s — %s', error, error_desc)
        flash(f'Google denied authorization: {error}. {error_desc}', 'danger')
        return redirect(url_for('settings.gmail_settings'))

    # Validate state parameter (CSRF protection)
    returned_state = qs.get('state', [None])[0]
    expected_state = session.pop('gmail_oauth_state', None)
    if not returned_state or returned_state != expected_state:
        current_app.logger.warning('Gmail OAuth state mismatch: expected=%s got=%s',
                                   expected_state, returned_state)
        flash('OAuth state mismatch — please try connecting again.', 'danger')
        return redirect(url_for('settings.gmail_settings'))

    code = qs.get('code', [None])[0]

    if not code:
        flash('No authorization code received.', 'danger')
        return redirect(url_for('settings.gmail_settings'))

    # Exchange code for tokens directly (bypasses PKCE requirement in oauthlib)
    token_response = http_requests.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': os.environ['GOOGLE_CLIENT_ID'],
        'client_secret': os.environ['GOOGLE_CLIENT_SECRET'],
        'redirect_uri': url_for('settings.gmail_callback', _external=True, _scheme='https'),
        'grant_type': 'authorization_code',
    })

    if token_response.status_code != 200:
        current_app.logger.error('Gmail token exchange failed: %s', token_response.text)
        flash('Failed to complete Gmail authorization. Please try again.', 'danger')
        return redirect(url_for('settings.gmail_settings'))

    tokens = token_response.json()
    refresh_token = tokens.get('refresh_token')
    access_token = tokens.get('access_token')

    if not refresh_token:
        flash('No refresh token received. Please disconnect and reconnect.', 'warning')
        return redirect(url_for('settings.gmail_settings'))

    # Get the authorized email address.
    # Primary: decode from id_token JWT (already in token response when openid+email scopes granted).
    # Fallback: call the userinfo endpoint with the access token.
    email = None

    id_token_raw = tokens.get('id_token')
    if id_token_raw:
        try:
            import json as _json
            payload_segment = id_token_raw.split('.')[1]
            # Fix base64 padding
            payload_segment += '=' * (4 - len(payload_segment) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(payload_segment))
            email = payload.get('email')
        except Exception as e:
            current_app.logger.warning('Failed to decode id_token for email: %s', e)

    if not email and access_token:
        headers = {'Authorization': f'Bearer {access_token}'}
        userinfo_resp = http_requests.get(
            'https://www.googleapis.com/oauth2/v3/userinfo', headers=headers)
        if userinfo_resp.ok:
            email = userinfo_resp.json().get('email')
        else:
            current_app.logger.warning('Userinfo request failed (%s): %s',
                                       userinfo_resp.status_code, userinfo_resp.text)

    if not email:
        email = 'unknown'
        current_app.logger.warning('Could not determine Gmail address from OAuth response')

    store_tokens(refresh_token, email)

    flash(f'Gmail connected as {email}.', 'success')
    return redirect(url_for('settings.gmail_settings'))


@bp.route('/settings/gmail/disconnect', methods=['POST'])
@login_required
def gmail_disconnect():
    clear_tokens()
    flash('Gmail disconnected. Emails will fall back to SMTP.', 'info')
    return redirect(url_for('settings.gmail_settings'))
