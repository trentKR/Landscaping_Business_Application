"""Invoice PDF rendering through WeasyPrint.

Skipped automatically when WeasyPrint's native libraries (Pango/GObject) are
not installed — typical on a Windows dev box. CI installs them, so the pinned
WeasyPrint version is exercised there on every push.
"""

import pytest
from flask import render_template

from models import Invoice, InvoiceLineItem
from routes.invoices import _make_static_fetcher
from services import get_outstanding_invoices
from tz import eastern_today

try:
    from weasyprint import HTML
    HAVE_WEASYPRINT = True
except OSError:  # native libs missing
    HAVE_WEASYPRINT = False

pytestmark = pytest.mark.skipif(not HAVE_WEASYPRINT,
                                reason='WeasyPrint native libraries not available')


def test_static_fetcher_serves_logo_from_disk(app):
    """Pins the url_fetcher contract, which WeasyPrint changed in 70.

    The fetcher must resolve /static/ URLs off the local filesystem: invoice
    emails render in a background thread where nothing is listening on HTTP.
    """
    from weasyprint.urls import URLFetcherResponse

    fetcher = _make_static_fetcher(app.static_folder)
    resp = fetcher('http://127.0.0.1:5000/static/logo_green.png')

    assert isinstance(resp, URLFetcherResponse)
    assert resp.content_type == 'image/png'
    assert resp.read()[:8] == b'\x89PNG\r\n\x1a\n'
    resp.close()


def test_invoice_pdf_renders(app, client_obj, make_service, make_invoice, db):
    svc = make_service(client_obj, amount=45.0)
    inv = make_invoice(client_obj, 45.0)
    db.session.add(InvoiceLineItem(invoice_id=inv.id, service_id=svc.id,
                                   description='Mowing', amount=45.0))
    db.session.commit()

    with app.test_request_context(base_url='http://127.0.0.1:5000/'):
        html = render_template('invoice_print.html', invoice=inv,
                               outstanding=get_outstanding_invoices(inv),
                               today=eastern_today())
    pdf = HTML(string=html, base_url='http://127.0.0.1:5000/',
               url_fetcher=_make_static_fetcher(app.static_folder)).write_pdf()

    assert pdf[:5] == b'%PDF-'
    assert len(pdf) > 5_000
    assert db.session.get(Invoice, inv.id).invoice_number in html
