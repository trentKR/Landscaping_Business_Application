import mimetypes
import os
import threading
from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from gmail import send_email
from models import Client, Invoice, InvoiceLineItem, InvoiceStatus, Route, Service, db
from services import apply_date_filters, auto_apply_credits, get_outstanding_invoices, refresh_client_invoice_statuses
from tz import eastern_now, eastern_today
from utils import make_setup_token

bp = Blueprint('invoices', __name__)


@bp.route('/invoices')
@login_required
def invoice_list():
    status_filter = request.args.getlist('status')
    search = request.args.get('search', '').strip()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Invoice.query.join(Client).options(
        joinedload(Invoice.client),
        joinedload(Invoice.payment_applications)
    )
    if status_filter:
        query = query.filter(Invoice.status.in_(status_filter))
    if search:
        query = query.filter(
            Client.first_name.ilike(f'%{search}%') |
            Client.last_name.ilike(f'%{search}%')
        )
    query, date_from, date_to = apply_date_filters(query, Invoice.invoice_date, date_from, date_to)

    invoices = query.order_by(Invoice.invoice_date.desc()).limit(200).all()
    total_outstanding = sum(
        inv.balance_due for inv in invoices
        if inv.status not in (InvoiceStatus.PAID, InvoiceStatus.VOID)
    )

    return render_template('invoices.html', invoices=invoices,
                           status_filter=status_filter,
                           search=search,
                           date_from=date_from, date_to=date_to,
                           total_outstanding=total_outstanding)


@bp.route('/invoices/new', methods=['GET', 'POST'])
@login_required
def invoice_create():
    if request.method == 'POST':
        try:
            client_id = int(request.form['client_id'])
            invoice_date_val = date.fromisoformat(request.form['invoice_date'])
        except (ValueError, KeyError):
            flash('Please fill in all required fields with valid values.', 'danger')
            return redirect(url_for('invoices.invoice_create'))
        notes = request.form.get('notes', '').strip()

        # Retry loop handles the rare case where two users create an invoice
        # at the same moment and get the same sequence number.
        for _attempt in range(3):
            try:
                with db.session.begin_nested():
                    invoice = Invoice(
                        invoice_number=Invoice.generate_invoice_number(),
                        client_id=client_id,
                        invoice_date=invoice_date_val,
                        notes=notes,
                        status=InvoiceStatus.UNPAID,
                    )
                    db.session.add(invoice)
                    db.session.flush()
                break
            except IntegrityError:
                invoice = None
        else:
            flash('Could not generate invoice number. Please try again.', 'danger')
            return redirect(url_for('invoices.invoice_create'))

        # Add selected services as line items
        service_ids = request.form.getlist('service_ids')
        for sid in service_ids:
            service = db.session.get(Service, int(sid))
            if service and service.client_id == client_id:
                li = InvoiceLineItem(
                    invoice_id=invoice.id,
                    service_id=service.id,
                    description=f'{service.description if service.service_type == "other" and service.description else service.service_type_display} - {service.service_date.strftime("%m/%d/%Y")}',
                    amount=service.amount
                )
                db.session.add(li)

        db.session.flush()
        invoice.recalculate_subtotal()
        db.session.flush()
        try:
            auto_apply_credits(invoice)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash('An error occurred creating the invoice. Please try again.', 'danger')
            return redirect(url_for('invoices.invoice_create'))
        flash(f'Invoice {invoice.invoice_number} created.', 'success')

        return redirect(url_for('invoices.invoice_detail', invoice_id=invoice.id))

    preselect_client = request.args.get('client_id', type=int)
    all_clients = Client.query.filter_by(active=True).order_by(Client.last_name, Client.first_name).all()
    today_val = eastern_today()
    return render_template('invoice_create.html',
                           clients=all_clients,
                           preselect_client=preselect_client,
                           today=today_val)


@bp.route('/invoices/quick-create', methods=['GET', 'POST'])
@login_required
def invoice_quick_create():
    if request.method == 'POST':
        selected_ids = request.form.getlist('client_ids')
        if not selected_ids:
            flash('No clients selected.', 'warning')
            return redirect(url_for('invoices.invoice_quick_create'))

        today_val = eastern_today()
        invoices_created = 0
        created_invoice_ids = []

        try:
            for cid_str in selected_ids:
                try:
                    cid = int(cid_str)
                except ValueError:
                    continue
                client = db.session.get(Client, cid)
                if not client or not client.active:
                    continue

                uninvoiced = client.uninvoiced_services()
                if not uninvoiced:
                    flash(f'{client.full_name} has no uninvoiced services — skipped.', 'warning')
                    continue

                invoice = None
                for _attempt in range(3):
                    try:
                        with db.session.begin_nested():
                            invoice = Invoice(
                                invoice_number=Invoice.generate_invoice_number(),
                                client_id=client.id,
                                invoice_date=today_val,
                                status=InvoiceStatus.UNPAID,
                            )
                            db.session.add(invoice)
                            db.session.flush()
                        break
                    except IntegrityError:
                        invoice = None
                else:
                    flash(f'Could not generate invoice number for {client.full_name} — skipped.', 'danger')
                    continue

                for service in uninvoiced:
                    li = InvoiceLineItem(
                        invoice_id=invoice.id,
                        service_id=service.id,
                        description=f'{service.description if service.service_type == "other" and service.description else service.service_type_display} - {service.service_date.strftime("%m/%d/%Y")}',
                        amount=service.amount,
                    )
                    db.session.add(li)

                db.session.flush()
                invoice.recalculate_subtotal()
                db.session.flush()
                auto_apply_credits(invoice)
                created_invoice_ids.append(invoice.id)
                invoices_created += 1

            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Quick-create failed: %s', exc)
            flash(f'Something went wrong creating invoices: {type(exc).__name__}. '
                  'Please try again or contact support.', 'danger')
            return redirect(url_for('invoices.invoice_quick_create'))

        # Send emails in background thread — don't block the response
        email_invoice_ids = []
        emails_skipped = 0
        for inv_id in created_invoice_ids:
            inv = db.session.get(Invoice, inv_id)
            if inv and inv.client.email:
                email_invoice_ids.append(inv_id)
            else:
                emails_skipped += 1

        if email_invoice_ids:
            thread = threading.Thread(
                target=_send_emails_background,
                args=(current_app._get_current_object(), email_invoice_ids),
            )
            thread.daemon = True
            thread.start()

        parts = [f'{invoices_created} invoice{"s" if invoices_created != 1 else ""} created']
        if email_invoice_ids:
            parts.append(f'{len(email_invoice_ids)} email{"s" if len(email_invoice_ids) != 1 else ""} sending in background')
        if emails_skipped:
            parts.append(f'{emails_skipped} client{"s" if emails_skipped != 1 else ""} missing email')
        flash(', '.join(parts) + '.', 'success')
        return redirect(url_for('invoices.invoice_list'))

    all_clients = Client.query.filter_by(active=True).order_by(Client.last_name, Client.first_name).all()
    clients_with_services = [(c, c.uninvoiced_services()) for c in all_clients]
    clients_with_services = [(c, svcs) for c, svcs in clients_with_services if svcs]
    return render_template('invoice_quick_create.html',
                           clients_with_services=clients_with_services,
                           routes=Route.all_sorted())


@bp.route('/invoices/<int:invoice_id>')
@login_required
def invoice_detail(invoice_id):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    return render_template('invoice_detail.html', invoice=invoice,
                           outstanding=get_outstanding_invoices(invoice),
                           today=eastern_today())


@bp.route('/invoices/<int:invoice_id>/void', methods=['POST'])
@login_required
def invoice_void(invoice_id):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    if invoice.status == InvoiceStatus.VOID:
        flash('Invoice is already void.', 'warning')
        return redirect(url_for('invoices.invoice_detail', invoice_id=invoice.id))
    # Delete payment applications to free credits
    for pa in list(invoice.payment_applications):
        db.session.delete(pa)
    # Delete line items to free up the services for future invoicing
    for li in list(invoice.line_items):
        db.session.delete(li)
    invoice.status = InvoiceStatus.VOID
    invoice.voided_at = eastern_now()
    db.session.flush()
    refresh_client_invoice_statuses(invoice.client)
    # Re-apply freed credits to remaining outstanding invoices (FIFO)
    outstanding = invoice.client.invoices.filter(
        Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL])
    ).order_by(Invoice.invoice_date.asc(), Invoice.id.asc()).all()
    for inv in outstanding:
        auto_apply_credits(inv)
    db.session.commit()
    flash(f'Invoice {invoice.invoice_number} voided. Payment credits and services have been freed.', 'warning')
    return redirect(url_for('invoices.invoice_detail', invoice_id=invoice.id))


@bp.route('/invoices/<int:invoice_id>/delete', methods=['POST'])
@login_required
def invoice_delete(invoice_id):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    if invoice.status != InvoiceStatus.VOID:
        flash('Only void invoices can be deleted.', 'danger')
        return redirect(url_for('invoices.invoice_detail', invoice_id=invoice.id))
    invoice_number = invoice.invoice_number
    db.session.delete(invoice)
    db.session.commit()
    flash(f'Invoice {invoice_number} deleted.', 'success')
    return redirect(url_for('invoices.invoice_list'))


@bp.route('/invoices/<int:invoice_id>/print')
@login_required
def invoice_print(invoice_id):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)
    return render_template('invoice_print.html', invoice=invoice,
                           outstanding=get_outstanding_invoices(invoice),
                           today=eastern_today())


def _send_emails_background(app, invoice_ids):
    """Send invoice emails in a background thread with app context."""
    with app.app_context():
        for invoice_id in invoice_ids:
            invoice = db.session.get(Invoice, invoice_id)
            if not invoice or not invoice.client.email:
                continue
            try:
                _send_invoice_email_bg(invoice, app)
            except Exception as e:
                app.logger.error('Background email failed for invoice %s: %s',
                                 invoice.invoice_number, e)
        db.session.commit()


def _make_static_fetcher(static_folder):
    """WeasyPrint url_fetcher that serves /static/ files from disk.

    Invoice PDFs reference the logo as an absolute URL, which WeasyPrint would
    otherwise fetch over HTTP — impossible from a background thread with no
    server listening. Anything outside /static/ falls through to WeasyPrint's
    own fetching.

    WeasyPrint 70 replaced the module-level ``default_url_fetcher`` function
    with a ``URLFetcher`` class whose ``fetch()`` returns a
    ``URLFetcherResponse``; subclassing is now the supported way to override it.
    """
    from weasyprint.urls import URLFetcher, URLFetcherResponse

    class StaticFileFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            idx = url.find('/static/')
            if idx >= 0:
                filename = url[idx + len('/static/'):]
                filepath = os.path.join(static_folder, filename)
                if os.path.isfile(filepath):
                    mime = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
                    with open(filepath, 'rb') as f:
                        return URLFetcherResponse(
                            url, body=f.read(), headers={'Content-Type': mime})
            return super().fetch(url, headers)

    return StaticFileFetcher()


def _send_invoice_email_bg(invoice, app):
    """Background-safe email send — no request context needed."""
    from weasyprint import HTML

    base_url = app.config.get('BASE_URL', 'http://127.0.0.1:5000/')

    with app.test_request_context(base_url=base_url):
        outstanding = get_outstanding_invoices(invoice)
        html_string = render_template('invoice_print.html', invoice=invoice,
                                      outstanding=outstanding,
                                      today=eastern_today())
        fetcher = _make_static_fetcher(app.static_folder)
        pdf_bytes = HTML(string=html_string, base_url=base_url,
                         url_fetcher=fetcher).write_pdf()
        pdf_filename = f'invoice_{invoice.invoice_number}_{invoice.client.last_name}.pdf'
        subject = f'Invoice {invoice.invoice_number} \u2014 Clips by Rorabaugh, LLC'

        client = invoice.client
        setup_url = None
        portal_dashboard_url = None
        if client.has_portal_access:
            portal_dashboard_url = url_for('portal.portal_dashboard',
                                           _external=True)
        else:
            token = make_setup_token(app.config['SECRET_KEY'], 'client_id', client)
            setup_url = url_for('portal.portal_setup_password',
                                token=token, _external=True)

        marketing_url = app.config.get('MARKETING_URL', '')
        marketing_display = marketing_url.replace('https://', '').replace('http://', '').rstrip('/')

        html_body = render_template('email_invoice.html', invoice=invoice,
                                    account_balance=invoice.client.account_balance(),
                                    outstanding=outstanding,
                                    today=eastern_today(),
                                    setup_url=setup_url,
                                    portal_dashboard_url=portal_dashboard_url,
                                    marketing_url=marketing_url,
                                    marketing_display=marketing_display)
        send_email(subject=subject, recipients=[client.email], html=html_body,
                   attachments=[(pdf_filename, 'application/pdf', pdf_bytes)])
        invoice.sent_at = eastern_now()


def _send_invoice_email(invoice: Invoice) -> bool:
    """Generate PDF and email it to the client. Returns True on success."""
    try:
        from weasyprint import HTML
        outstanding = get_outstanding_invoices(invoice)
        html_string = render_template('invoice_print.html', invoice=invoice,
                                      outstanding=outstanding,
                                      today=eastern_today())
        fetcher = _make_static_fetcher(current_app.static_folder)
        pdf_bytes = HTML(string=html_string, base_url=request.url_root,
                         url_fetcher=fetcher).write_pdf()
        pdf_filename = f'invoice_{invoice.invoice_number}_{invoice.client.last_name}.pdf'
        subject = f'Invoice {invoice.invoice_number} \u2014 Clips by Rorabaugh, LLC'

        # Build portal context for email template
        client = invoice.client
        setup_url = None
        portal_dashboard_url = None
        if client.has_portal_access:
            portal_dashboard_url = url_for('portal.portal_dashboard',
                                           _external=True)
        else:
            token = make_setup_token(current_app.config['SECRET_KEY'], 'client_id', client)
            setup_url = url_for('portal.portal_setup_password',
                                token=token, _external=True)

        marketing_url = current_app.config.get('MARKETING_URL', '')
        marketing_display = marketing_url.replace('https://', '').replace('http://', '').rstrip('/')

        html_body = render_template('email_invoice.html', invoice=invoice,
                                    account_balance=invoice.client.account_balance(),
                                    outstanding=outstanding,
                                    today=eastern_today(),
                                    setup_url=setup_url,
                                    portal_dashboard_url=portal_dashboard_url,
                                    marketing_url=marketing_url,
                                    marketing_display=marketing_display)
        success = send_email(subject=subject, recipients=[client.email], html=html_body,
                             attachments=[(pdf_filename, 'application/pdf', pdf_bytes)])
        if success:
            invoice.sent_at = eastern_now()
        return success
    except Exception as e:
        current_app.logger.error('Email send failed for invoice %s to %s: %s',
                                 invoice.invoice_number, invoice.client.email, e)
        return False


@bp.route('/invoices/<int:invoice_id>/email', methods=['POST'])
@login_required
def invoice_email(invoice_id):
    invoice = db.session.get(Invoice, invoice_id) or abort(404)

    if not invoice.client.email:
        flash('This client has no email address on file.', 'warning')
        return redirect(url_for('invoices.invoice_detail', invoice_id=invoice.id))

    if _send_invoice_email(invoice):
        db.session.commit()
        flash(f'Invoice {invoice.invoice_number} emailed to {invoice.client.email}.', 'success')
    else:
        flash('PDF generated but email could not be sent. Check mail configuration.', 'danger')
    return redirect(url_for('invoices.invoice_detail', invoice_id=invoice.id))


@bp.route('/invoices/bulk-email', methods=['POST'])
@login_required
def invoice_bulk_email():
    raw_ids = request.form.getlist('invoice_ids[]')
    ids = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (ValueError, TypeError):
            pass
    if not ids:
        flash('No invoices selected.', 'warning')
        return redirect(url_for('invoices.invoice_list'))

    emails_sent = 0
    emails_failed = 0
    emails_skipped = 0
    for invoice in Invoice.query.filter(Invoice.id.in_(ids)).all():
        if not invoice.client.email:
            emails_skipped += 1
            continue
        if _send_invoice_email(invoice):
            emails_sent += 1
        else:
            emails_failed += 1

    if emails_sent:
        db.session.commit()

    parts = []
    if emails_sent:
        parts.append(f'{emails_sent} emailed')
    if emails_failed:
        parts.append(f'{emails_failed} failed')
    if emails_skipped:
        parts.append(f'{emails_skipped} missing email')
    flash(', '.join(parts) + '.', 'success' if not emails_failed else 'warning')
    return redirect(url_for('invoices.invoice_list'))


@bp.route('/invoices/bulk-void', methods=['POST'])
@login_required
def invoice_bulk_void():
    raw_ids = request.form.getlist('invoice_ids[]')
    ids = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (ValueError, TypeError):
            pass
    if not ids:
        flash('No invoices selected.', 'warning')
        return redirect(url_for('invoices.invoice_list'))

    voided = 0
    skipped = 0
    affected_clients = set()
    for invoice in Invoice.query.filter(Invoice.id.in_(ids)).all():
        if invoice.status == InvoiceStatus.VOID:
            skipped += 1
            continue
        for pa in list(invoice.payment_applications):
            db.session.delete(pa)
        for li in list(invoice.line_items):
            db.session.delete(li)
        invoice.status = InvoiceStatus.VOID
        invoice.voided_at = eastern_now()
        affected_clients.add(invoice.client_id)
        voided += 1

    db.session.flush()
    for client in Client.query.filter(Client.id.in_(affected_clients)).all():
        refresh_client_invoice_statuses(client)
        outstanding = client.invoices.filter(
            Invoice.status.in_([InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL])
        ).order_by(Invoice.invoice_date.asc(), Invoice.id.asc()).all()
        for inv in outstanding:
            auto_apply_credits(inv)
    db.session.commit()

    parts = [f'{voided} voided']
    if skipped:
        parts.append(f'{skipped} skipped (already void)')
    flash(', '.join(parts) + '.', 'warning')
    return redirect(url_for('invoices.invoice_list'))


@bp.route('/invoices/bulk-delete', methods=['POST'])
@login_required
def invoice_bulk_delete():
    raw_ids = request.form.getlist('invoice_ids[]')
    ids = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (ValueError, TypeError):
            pass
    if not ids:
        flash('No invoices selected.', 'warning')
        return redirect(url_for('invoices.invoice_list'))

    deleted = 0
    skipped = 0
    for invoice in Invoice.query.filter(Invoice.id.in_(ids)).all():
        if invoice.status != InvoiceStatus.VOID:
            skipped += 1
            continue
        db.session.delete(invoice)
        deleted += 1

    db.session.commit()

    parts = [f'{deleted} deleted']
    if skipped:
        parts.append(f'{skipped} skipped (not void)')
    flash(', '.join(parts) + '.', 'success' if not skipped else 'warning')
    return redirect(url_for('invoices.invoice_list'))
