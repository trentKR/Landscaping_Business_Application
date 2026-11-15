import logging
import re
import unicodedata

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from extensions import limiter
from gmail import send_email
from tz import eastern_now, eastern_today

log = logging.getLogger(__name__)

bp = Blueprint('landing', __name__)

# Clips only works the Fairlawn / Copley / Bath area, so quote requests from
# outside Ohio are accepted quietly and never emailed.
OHIO_ZIP_PREFIXES = ('43', '44', '45')

THANKS = "Thanks — we'll be in touch within a day."

SERVICE_OPTIONS = ('Weekly mowing', 'Mulching', 'Hedge trimming',
                   'Spring or fall cleanup', 'Something else')

# Generous enough for any real address, small enough to keep the owner's inbox
# and the app log readable. Field -> max characters.
FIELD_LIMITS = {'name': 100, 'street': 120, 'city': 60, 'state': 20, 'zip': 12,
                'phone': 30, 'email': 254, 'service': 80, 'notes': 2000}

# Deliberately loose: enough to catch a typo or a bot's junk, not enough to
# reject somebody's unusual but valid address.
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$')

LABELS = {'name': 'name', 'street': 'street address', 'city': 'city', 'state': 'state',
          'zip': 'ZIP code', 'phone': 'phone number', 'email': 'email address',
          'service': 'selection', 'notes': 'note'}

MIN_PHONE_DIGITS = 7

# The form carries a signed timestamp so we can measure how long it took to
# fill in. Nobody types an address in under three seconds; scripts always do.
QUOTE_SALT = 'quote-form'
MIN_FORM_SECONDS = 3
MAX_FORM_SECONDS = 6 * 60 * 60


def _scrub(raw, multiline=False):
    """Strip Unicode control/format characters and surrounding whitespace.

    Kills CR/LF (log-line forgery, email headers) along with the invisible
    zero-width and bidi characters spam uses to dodge filters. ``multiline``
    keeps real newlines, which only the notes box is allowed to have.
    """
    kept = [ch for ch in (raw or '')
            if (multiline and ch == '\n') or unicodedata.category(ch)[0] != 'C']
    return ''.join(kept).strip()


def _quote_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=QUOTE_SALT)


def make_quote_token():
    """Signed timestamp planted in the form so we can measure fill time."""
    return _quote_serializer().dumps('quote')


def _form_age_seconds(token):
    """Seconds since the form was rendered, or None if the token isn't ours.

    Raises ``SignatureExpired`` for a tab that sat open past MAX_FORM_SECONDS.
    """
    if not token:
        return None
    try:
        _, issued = _quote_serializer().loads(token, max_age=MAX_FORM_SECONDS,
                                              return_timestamp=True)
    except SignatureExpired:
        # A tab left open overnight. Real person, dead token — let them resend.
        raise
    except BadSignature:
        return None
    return (eastern_now() - issued).total_seconds()


def _is_ohio(state, zip_):
    """True when the state field reads Ohio, or the ZIP falls in Ohio's 43-45 range."""
    if state.strip().strip('.').lower() in ('oh', 'ohio'):
        return True
    digits = ''.join(c for c in zip_ if c.isdigit())
    return len(digits) >= 5 and digits[:2] in OHIO_ZIP_PREFIXES


REVIEWS = [
    {
        'text': 'John had been nothing but outstanding throughout the years of managing our lawn. Service is A+. Class act all the way.',
        'name': 'Brad',
        'location': 'Creekstone',
    },
    {
        'text': "I've used John since he started. Very professional and dependable. Also available for spring and fall cleanup.",
        'name': 'Nancy',
        'location': 'Fairlawn Heights',
    },
    {
        'text': 'We have used John\'s service for years. He does a great job. He does mowing & fall cleanup. Very prompt and caring. My yard looks beautiful. Thank you John.',
        'name': 'Earl',
        'location': 'Sand Run Offshoots',
    },
    {
        'text': "I've been using John for several years and he and his team always do great work for a reasonable price.",
        'name': 'Heather',
        'location': 'Fairlawn Heights',
    },
    {
        'text': 'We love Clips Lawn Service. They are a really nice local family business who can do a lot of different lawn care needs. They are awesome and we highly recommend them.',
        'name': 'Sue',
        'location': 'Sand Run Offshoots',
    },
    {
        'text': "John and his sons / crew have been handling my lawn and mulch for 8 years. I could not be more pleased with the quality of service. He is always checking to make sure the lawn looking great throughout the week and flexible to any special requests!",
        'name': 'Bob',
        'location': 'Heritage Woods',
    },
    {
        'text': "I've been working with John and his sons for almost five years now, and I genuinely appreciate their efficient system of mowing lawns. They can mow my two acres in about 30 minutes, which is incredible.",
        'name': 'Brian',
        'location': 'Copley-Jacoby Road',
    },
    {
        'text': 'Wonderful team! Quick, neat and dependable. Love them!',
        'name': 'Faith',
        'location': 'Sand Run Knolls',
    },
    {
        'text': 'Great company and very reliable. Have been utilizing them for the last 4 years. Definitely would recommend.',
        'name': 'Sherman',
        'location': 'Copley-Jacoby Road',
    },
]


@bp.route('/')
def home():
    return render_template('landing.html',
                           reviews=REVIEWS,
                           services=SERVICE_OPTIONS,
                           field_limits=FIELD_LIMITS,
                           quote_token=make_quote_token(),
                           current_year=eastern_today().year)


@bp.route('/request-quote', methods=['POST'])
@limiter.limit('5 per hour')
@limiter.limit('2 per minute')
def request_quote():
    """Landing-page quote request — emails the details to QUOTE_INBOX."""
    # Honeypot: hidden from people, irresistible to bots. Pretend it worked.
    if request.form.get('website'):
        log.info('Quote request rejected: honeypot filled')
        flash(THANKS, 'success')
        return redirect(url_for('landing.home', _anchor='quote'))

    # How long the form sat on screen. No token, a forged one, or an inhumanly
    # fast fill all mean a script, so pretend it worked and move on.
    try:
        age = _form_age_seconds(request.form.get('form_ts'))
    except SignatureExpired:
        flash('That form expired. Please send it again.', 'warning')
        return redirect(url_for('landing.home', _anchor='quote'))
    if age is None or age < MIN_FORM_SECONDS:
        log.info('Quote request rejected: form age %s', age)
        flash(THANKS, 'success')
        return redirect(url_for('landing.home', _anchor='quote'))

    fields = {name: _scrub(request.form.get(name, ''), multiline=(name == 'notes'))
              for name in FIELD_LIMITS}

    for field, limit in FIELD_LIMITS.items():
        if len(fields[field]) > limit:
            flash(f'That {LABELS[field]} is longer than we can accept — '
                  'please shorten it.', 'danger')
            return redirect(url_for('landing.home', _anchor='quote'))

    name    = fields['name']
    street  = fields['street']
    city    = fields['city']
    state   = fields['state']
    zip_    = fields['zip']
    address = f"{street}, {city}, {state} {zip_}".strip(', ')
    phone   = fields['phone']
    email   = fields['email']
    notes   = fields['notes']
    # The field is a <select>; anything else means a broken client, not a bad
    # customer, so file it under the catch-all rather than turning them away.
    service = fields['service'] if fields['service'] in SERVICE_OPTIONS else 'Something else'

    if not name or not (street and city and state and zip_):
        flash('Please include your name and full property address.', 'danger')
        return redirect(url_for('landing.home', _anchor='quote'))
    if not phone and not email:
        flash('Please include a phone number or an email so we can reach you.', 'danger')
        return redirect(url_for('landing.home', _anchor='quote'))
    if email and not EMAIL_RE.match(email):
        flash("That email address doesn't look right. Please check it and try again.", 'danger')
        return redirect(url_for('landing.home', _anchor='quote'))
    if phone and sum(c.isdigit() for c in phone) < MIN_PHONE_DIGITS:
        flash("That phone number doesn't look right. Please check it and try again.", 'danger')
        return redirect(url_for('landing.home', _anchor='quote'))

    if not _is_ohio(state, zip_):
        # Outside the service area: accept quietly rather than tell them we ignored it.
        flash(THANKS, 'success')
        return redirect(url_for('landing.home', _anchor='quote'))

    sent = False
    try:
        html_body = render_template('email_quote_request.html',
                                    name=name, address=address, phone=phone,
                                    email=email, service=service, notes=notes,
                                    submitted=eastern_today())
        sent = send_email(subject=f'New quote request — {name}',
                          recipients=[current_app.config['QUOTE_INBOX']],
                          html=html_body)
    except Exception as e:
        log.error('Quote request email failed for %s: %s', name, e)

    if sent:
        log.info('Quote request emailed for %s', name)
        flash(THANKS, 'success')
    else:
        # The lead is only in the log now, so don't let the visitor walk away
        # believing it landed. Give them the phone number instead.
        log.error('Quote request email failed for %s (%s / %s) — %s',
                  name, phone, email, address)
        flash('We could not send that just now. Please call (330) 620-3465 '
              'and we will get you scheduled.', 'danger')

    return redirect(url_for('landing.home', _anchor='quote'))
