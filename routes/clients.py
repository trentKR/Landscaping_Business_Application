from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from gmail import send_email
from models import Client, Route, db
from tz import eastern_now
from utils import make_setup_token, parse_amount

bp = Blueprint('clients', __name__)


@bp.route('/clients')
@login_required
def clients():
    search = request.args.get('search', '').strip()
    filter_type = request.args.get('filter', 'all')

    query = Client.query
    if search:
        query = query.filter(
            Client.first_name.ilike(f'%{search}%') |
            Client.last_name.ilike(f'%{search}%') |
            Client.address_street.ilike(f'%{search}%') |
            Client.city.ilike(f'%{search}%') |
            Client.zip_code.ilike(f'%{search}%') |
            Client.phone.ilike(f'%{search}%')
        )
    if filter_type == 'inactive':
        query = query.filter_by(active=False)
    else:
        query = query.filter_by(active=True)
        if filter_type == 'weekly':
            query = query.filter_by(is_weekly=True)
        elif filter_type == 'seasonal':
            query = query.filter_by(is_weekly=False)
        elif filter_type == 'portal':
            query = query.filter(Client.password_hash.isnot(None))
        elif filter_type == 'no_portal':
            query = query.filter(Client.password_hash.is_(None))

    clients_list = query.order_by(Client.last_name, Client.first_name).all()
    return render_template('clients.html', clients=clients_list,
                           search=search, filter_type=filter_type)


@bp.route('/clients/new', methods=['GET', 'POST'])
@login_required
def client_new():
    if request.method == 'POST':
        try:
            default_mowing_rate = parse_amount(request.form.get('default_mowing_rate') or 0)
            if default_mowing_rate < 0:
                raise ValueError
        except ValueError:
            flash('Mowing rate must be a valid number.', 'danger')
            return redirect(url_for('clients.client_new'))
        route_id_val = request.form.get('route_id', '')
        route_id_int = None
        if route_id_val:
            try:
                route_id_int = int(route_id_val)
            except ValueError:
                pass
        client = Client(
            first_name=request.form['first_name'].strip(),
            last_name=request.form['last_name'].strip(),
            address_street=request.form['address_street'].strip(),
            city=request.form['city'].strip(),
            state=request.form['state'].strip(),
            zip_code=request.form['zip_code'].strip(),
            phone=request.form.get('phone', '').strip(),
            email=request.form.get('email', '').strip(),
            default_mowing_rate=default_mowing_rate,
            is_weekly='is_weekly' in request.form,
            notes=request.form.get('notes', '').strip(),
            route_id=route_id_int,
        )
        db.session.add(client)
        db.session.commit()
        flash(f'Client "{client.full_name}" created.', 'success')
        return redirect(url_for('clients.client_detail', client_id=client.id))
    return render_template('client_form.html', client=None, routes=Route.all_sorted())


@bp.route('/clients/<int:client_id>')
@login_required
def client_detail(client_id):
    client = db.session.get(Client, client_id) or abort(404)
    services = client.services.limit(50).all()
    total_revenue = client.total_revenue()
    account_balance = client.account_balance()
    unapplied_credit = client.unapplied_credit()
    recent_invoices = client.invoices.limit(10).all()
    recent_payments = client.payments.limit(10).all()
    return render_template('client_detail.html', client=client,
                           services=services, total_revenue=total_revenue,
                           account_balance=account_balance,
                           unapplied_credit=unapplied_credit,
                           recent_invoices=recent_invoices,
                           recent_payments=recent_payments)


@bp.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def client_edit(client_id):
    client = db.session.get(Client, client_id) or abort(404)
    if request.method == 'POST':
        try:
            default_mowing_rate = parse_amount(request.form.get('default_mowing_rate') or 0)
            if default_mowing_rate < 0:
                raise ValueError
        except ValueError:
            flash('Mowing rate must be a valid number.', 'danger')
            return redirect(url_for('clients.client_edit', client_id=client_id))
        client.first_name = request.form['first_name'].strip()
        client.last_name = request.form['last_name'].strip()
        client.address_street = request.form['address_street'].strip()
        client.city = request.form['city'].strip()
        client.state = request.form['state'].strip()
        client.zip_code = request.form['zip_code'].strip()
        client.phone = request.form.get('phone', '').strip()
        client.email = request.form.get('email', '').strip()
        client.default_mowing_rate = default_mowing_rate
        client.is_weekly = 'is_weekly' in request.form
        client.active = 'active' in request.form
        client.notes = request.form.get('notes', '').strip()
        route_id_val = request.form.get('route_id', '')
        if route_id_val:
            try:
                client.route_id = int(route_id_val)
            except ValueError:
                client.route_id = None
        else:
            client.route_id = None
        db.session.commit()
        flash(f'Client "{client.full_name}" updated.', 'success')
        return redirect(url_for('clients.client_detail', client_id=client.id))
    return render_template('client_form.html', client=client, routes=Route.all_sorted())


@bp.route('/clients/<int:client_id>/delete', methods=['POST'])
@login_required
def client_delete(client_id):
    client = db.session.get(Client, client_id) or abort(404)
    client.active = False
    db.session.commit()
    flash(f'Client "{client.full_name}" deactivated.', 'warning')
    return redirect(url_for('clients.clients'))


@bp.route('/clients/<int:client_id>/hard-delete', methods=['POST'])
@login_required
def client_hard_delete(client_id):
    client = db.session.get(Client, client_id) or abort(404)
    # Financial history must never be cascade-deleted. On Postgres the FK
    # constraints would reject this with a 500; on SQLite (FKs unenforced)
    # it would silently orphan invoices and payments.
    linked = {
        'service': client.services.count(),
        'invoice': client.invoices.count(),
        'payment': client.payments.count(),
        'expense': client.expenses.count(),
        'expense allocation': client.expense_allocations.count(),
    }
    blockers = [f'{n} {label}{"s" if n != 1 else ""}' for label, n in linked.items() if n]
    if blockers:
        flash(f'Cannot permanently delete "{client.full_name}" — linked to '
              f'{", ".join(blockers)}. Deactivate the client instead.', 'danger')
        return redirect(url_for('clients.client_detail', client_id=client.id))
    name = client.full_name
    db.session.delete(client)
    db.session.commit()
    flash(f'Client "{name}" permanently deleted.', 'danger')
    return redirect(url_for('clients.clients'))


# ---------------------------------------------------------------------------
# Portal invite
# ---------------------------------------------------------------------------

@bp.route('/clients/<int:client_id>/portal-invite', methods=['POST'])
@login_required
def client_portal_invite(client_id):
    client = db.session.get(Client, client_id) or abort(404)
    if not client.email:
        flash('Client has no email address on file.', 'warning')
        return redirect(url_for('clients.client_detail', client_id=client.id))

    token = make_setup_token(current_app.config['SECRET_KEY'], 'client_id', client)
    client.portal_invite_token = token
    client.portal_invite_sent_at = eastern_now()
    db.session.commit()

    setup_url = url_for('portal.portal_setup_password', token=token, _external=True)
    try:
        html_body = (
            f'<p>Hi {client.first_name},</p>'
            f'<p>You\'ve been invited to the Clips customer portal where you can '
            f'view your invoices and previous payments online.</p>'
            f'<p><a href="{setup_url}" style="display:inline-block;padding:12px 24px;'
            f'background:#16a34a;color:#fff;text-decoration:none;border-radius:8px;'
            f'font-weight:600;">Set Up Your Password</a></p>'
            f'<p>This link expires in 7 days.</p>'
            f'<p>Thanks,<br>Clips</p>'
        )
        if send_email(subject='Your Clips Customer Portal Invite',
                      recipients=[client.email], html=html_body):
            flash(f'Portal invite sent to {client.email}.', 'success')
        else:
            flash('Invite token created but email failed to send.', 'warning')
    except Exception as e:
        flash(f'Invite token created but email failed to send: {e}', 'warning')

    return redirect(url_for('clients.client_detail', client_id=client.id))


# ---------------------------------------------------------------------------
# Client API endpoints
# ---------------------------------------------------------------------------

@bp.route('/api/client/<int:client_id>/rate')
@login_required
def api_client_rate(client_id):
    client = db.session.get(Client, client_id)
    if client:
        return jsonify({'rate': client.default_mowing_rate})
    return jsonify({'rate': 0})


@bp.route('/api/client/<int:client_id>/balance')
@login_required
def api_client_balance(client_id):
    client = db.session.get(Client, client_id)
    if not client:
        return jsonify({'balance': 0, 'unapplied_credit': 0})
    return jsonify({
        'balance': client.account_balance(),
        'unapplied_credit': client.unapplied_credit(),
    })
