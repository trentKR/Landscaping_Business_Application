import logging
from functools import wraps

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from extensions import csrf, limiter
from gmail import send_email
from models import Client, Invoice, InvoiceStatus, Payment, db
from services import apply_payment_fifo, get_outstanding_invoices
from tz import eastern_today
from utils import is_safe_redirect_target, make_setup_token, setup_token_matches

log = logging.getLogger(__name__)

bp = Blueprint('portal', __name__, url_prefix='/portal')


# ---------------------------------------------------------------------------
# Portal auth decorator (independent from Flask-Login admin auth)
# ---------------------------------------------------------------------------

def portal_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        client_id = session.get('portal_client_id')
        if not client_id:
            return redirect(url_for('portal.portal_login', next=request.url))
        client = db.session.get(Client, client_id)
        if not client or not client.active:
            session.pop('portal_client_id', None)
            return redirect(url_for('portal.portal_login', next=request.url))
        g.portal_client = client
        return f(*args, **kwargs)
    return decorated


def _get_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def portal_login():
    if session.get('portal_client_id'):
        return redirect(url_for('portal.portal_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        client = Client.query.filter(
            db.func.lower(Client.email) == email,
            Client.active.is_(True)
        ).first()
        if client and client.check_portal_password(password):
            session['portal_client_id'] = client.id
            next_url = request.args.get('next') or request.form.get('next')
            if is_safe_redirect_target(next_url):
                return redirect(next_url)
            return redirect(url_for('portal.portal_dashboard'))
        flash('Invalid email or password.', 'danger')
    return render_template('portal_login.html')


@bp.route('/logout', methods=['POST'])
@portal_login_required
def portal_logout():
    session.pop('portal_client_id', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('portal.portal_login'))


# ---------------------------------------------------------------------------
# Password setup (from email invite)
# ---------------------------------------------------------------------------

@bp.route('/setup-password', methods=['GET', 'POST'])
def portal_setup_password():
    token = request.args.get('token', '')
    if not token:
        flash('Invalid or missing link.', 'danger')
        return redirect(url_for('portal.portal_login'))

    s = _get_serializer()
    try:
        data = s.loads(token, max_age=86400 * 7)  # 7 days
    except SignatureExpired:
        flash('This link has expired. You can request a new one below.', 'warning')
        return redirect(url_for('portal.portal_login'))
    except BadSignature as e:
        log.error('Portal setup BadSignature: %s', e)
        flash('Invalid link. You can request a new one below.', 'danger')
        return redirect(url_for('portal.portal_login'))

    client_id = data.get('client_id')
    client = db.session.get(Client, client_id) if client_id else None
    if not client:
        log.error('Portal setup: no client with id=%s', client_id)
        flash('No account found for this link.', 'danger')
        return redirect(url_for('portal.portal_login'))
    if not client.active:
        log.error('Portal setup: client id=%s is inactive', client_id)
        flash('This account has been deactivated.', 'danger')
        return redirect(url_for('portal.portal_login'))
    # The nonce ties the link to the password state it was issued for, so a
    # forwarded invoice email cannot be used to reset an already-set password.
    if not setup_token_matches(data, client):
        log.warning('Portal setup: stale token for client id=%s', client_id)
        flash('This link is no longer valid. You can request a new one below.', 'warning')
        return redirect(url_for('portal.portal_login'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('portal_setup_password.html', token=token, client=client)
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('portal_setup_password.html', token=token, client=client)

        client.set_portal_password(password)
        client.portal_invite_token = None
        client.portal_invite_sent_at = None
        db.session.commit()

        session['portal_client_id'] = client.id
        flash('Password set successfully! Welcome to your portal.', 'success')
        return redirect(url_for('portal.portal_dashboard'))

    return render_template('portal_setup_password.html', token=token, client=client)


# ---------------------------------------------------------------------------
# Self-service "Email me a login link"
# ---------------------------------------------------------------------------

@bp.route('/request-link', methods=['POST'])
@limiter.limit("3 per minute")
def portal_request_link():
    email = request.form.get('email', '').strip().lower()
    # Always show the same message to prevent email enumeration
    flash('If an account exists for that email, we\'ll send a link.', 'info')

    if email:
        client = Client.query.filter(
            db.func.lower(Client.email) == email,
            Client.active.is_(True)
        ).first()
        if not client:
            log.info('Portal link requested for %s — no active client found', email)
        else:
            token = make_setup_token(current_app.config['SECRET_KEY'], 'client_id', client)
            setup_url = url_for('portal.portal_setup_password', token=token, _external=True)
            try:
                html_body = render_template('email_portal_link.html',
                                            client=client, setup_url=setup_url)
                if send_email(subject='Your Clips Portal Access Link',
                              recipients=[client.email], html=html_body):
                    log.info('Portal link emailed to %s', client.email)
                else:
                    log.error('Portal request-link email failed for %s', client.email)
            except Exception as e:
                log.error('Portal request-link email failed for %s: %s', client.email, e)

    return redirect(url_for('portal.portal_login'))


# ---------------------------------------------------------------------------
# Portal dashboard
# ---------------------------------------------------------------------------

@bp.route('/')
@portal_login_required
def portal_dashboard():
    client = g.portal_client
    outstanding = client.invoices.filter(
        Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL])
    ).order_by(Invoice.invoice_date.desc()).all()
    paid_invoices = client.invoices.filter(
        Invoice.status == InvoiceStatus.PAID
    ).order_by(Invoice.invoice_date.desc()).all()
    recent_payments = client.payments.limit(10).all()
    balance = client.account_balance()
    stripe_enabled = bool(current_app.config.get('STRIPE_SECRET_KEY'))
    return render_template('portal_dashboard.html',
                           client=client,
                           outstanding_invoices=outstanding,
                           paid_invoices=paid_invoices,
                           recent_payments=recent_payments,
                           balance=balance,
                           stripe_enabled=stripe_enabled)


# ---------------------------------------------------------------------------
# Invoice view
# ---------------------------------------------------------------------------

@bp.route('/invoices/<int:invoice_id>')
@portal_login_required
def portal_invoice(invoice_id):
    invoice = db.session.get(Invoice, invoice_id)
    if not invoice or invoice.client_id != g.portal_client.id:
        abort(404)
    stripe_enabled = bool(current_app.config.get('STRIPE_SECRET_KEY'))
    return render_template('portal_invoice.html',
                           invoice=invoice,
                           outstanding=get_outstanding_invoices(invoice),
                           client=g.portal_client,
                           stripe_enabled=stripe_enabled,
                           today=eastern_today())


# ---------------------------------------------------------------------------
# Stripe payment
# ---------------------------------------------------------------------------

@bp.route('/pay/<int:invoice_id>', methods=['POST'])
@portal_login_required
def portal_pay(invoice_id):
    invoice = db.session.get(Invoice, invoice_id)
    if not invoice or invoice.client_id != g.portal_client.id:
        abort(404)
    if invoice.balance_due <= 0:
        flash('This invoice is already paid.', 'info')
        return redirect(url_for('portal.portal_invoice', invoice_id=invoice_id))

    stripe_key = current_app.config.get('STRIPE_SECRET_KEY')
    if not stripe_key:
        flash('Online payments are not configured.', 'warning')
        return redirect(url_for('portal.portal_invoice', invoice_id=invoice_id))

    try:
        import stripe
        stripe.api_key = stripe_key

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'Invoice {invoice.invoice_number}',
                        'description': f'Clips Landscaping - {g.portal_client.full_name}',
                    },
                    'unit_amount': int(round(invoice.balance_due * 100)),
                },
                'quantity': 1,
            }],
            mode='payment',
            metadata={
                'invoice_id': str(invoice.id),
                'client_id': str(g.portal_client.id),
                'invoice_number': invoice.invoice_number,
            },
            success_url=url_for('portal.portal_pay_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('portal.portal_pay_cancel', _external=True),
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        log.error('Stripe checkout error: %s', e)
        flash('Unable to start payment. Please try again later.', 'danger')
        return redirect(url_for('portal.portal_invoice', invoice_id=invoice_id))


@bp.route('/pay/success')
@portal_login_required
def portal_pay_success():
    return render_template('portal_pay_success.html', client=g.portal_client)


@bp.route('/pay/cancel')
@portal_login_required
def portal_pay_cancel():
    return render_template('portal_pay_cancel.html', client=g.portal_client)


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------

@bp.route('/webhook/stripe', methods=['POST'])
@csrf.exempt
def stripe_webhook():
    webhook_secret = current_app.config.get('STRIPE_WEBHOOK_SECRET')
    if not webhook_secret:
        abort(400)

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    try:
        import stripe
        stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        log.warning('Stripe webhook verification failed: %s', e)
        abort(400)

    if event['type'] == 'checkout.session.completed':
        sess = event['data']['object']
        session_id = sess['id']
        metadata = sess.get('metadata', {})
        invoice_id = metadata.get('invoice_id')
        client_id = metadata.get('client_id')

        if not invoice_id or not client_id:
            log.warning('Stripe webhook missing metadata: %s', metadata)
            return ('', 200)

        # Idempotency: check if payment already recorded
        reference = f'stripe:{session_id}'
        existing = Payment.query.filter_by(reference=reference).first()
        if existing:
            return ('', 200)

        invoice = db.session.get(Invoice, int(invoice_id))
        if not invoice:
            log.warning('Stripe webhook: invoice %s not found', invoice_id)
            return ('', 200)

        amount_paid = sess.get('amount_total', 0) / 100.0

        payment = Payment(
            client_id=int(client_id),
            amount=amount_paid,
            payment_method='credit_card',
            reference=reference,
            notes=f'Online payment via Stripe for {invoice.invoice_number}',
        )
        db.session.add(payment)
        db.session.flush()

        try:
            apply_payment_fifo(payment)
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception('Stripe webhook: failed to apply payment')
            abort(500)

    return ('', 200)
