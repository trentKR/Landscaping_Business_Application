from datetime import date

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.orm import joinedload

from models import Client, Route, Service, db
from services import apply_date_filters
from tz import eastern_today
from utils import parse_amount

bp = Blueprint('services_bp', __name__)


@bp.route('/services')
@login_required
def services():
    search = request.args.get('search', '').strip()
    service_type = request.args.get('type', 'all')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Service.query.join(Client).options(joinedload(Service.client))

    if search:
        query = query.filter(
            Client.first_name.ilike(f'%{search}%') |
            Client.last_name.ilike(f'%{search}%')
        )
    if service_type != 'all':
        query = query.filter(Service.service_type == service_type)
    query, date_from, date_to = apply_date_filters(query, Service.service_date, date_from, date_to)

    services_list = query.order_by(Service.service_date.desc()).limit(200).all()

    total = sum(s.amount for s in services_list)
    return render_template('services.html', services=services_list,
                           search=search, service_type=service_type,
                           date_from=date_from, date_to=date_to,
                           total=total,
                           service_types=Service.SERVICE_TYPES)


@bp.route('/services/<int:service_id>/edit', methods=['GET', 'POST'])
@login_required
def service_edit(service_id):
    service = db.session.get(Service, service_id) or abort(404)
    if request.method == 'POST':
        VALID_SERVICE_TYPES = {st[0] for st in Service.SERVICE_TYPES}
        service_type = request.form.get('service_type', '')
        if service_type not in VALID_SERVICE_TYPES:
            flash('Invalid service type.', 'danger')
            return redirect(url_for('services_bp.service_edit', service_id=service_id))
        try:
            service_date_val = date.fromisoformat(request.form['service_date'])
            amount = parse_amount(request.form['amount'])
        except (ValueError, KeyError):
            flash('Please fill in all required fields with valid values.', 'danger')
            return redirect(url_for('services_bp.service_edit', service_id=service_id))
        if amount <= 0:
            flash('Amount must be more than $0.', 'danger')
            return redirect(url_for('services_bp.service_edit', service_id=service_id))
        service.service_date = service_date_val
        service.service_type = service_type
        service.description = request.form.get('description', '').strip()
        service.amount = amount
        db.session.commit()
        flash('Service updated.', 'success')
        return redirect(url_for('services_bp.services'))

    clients = Client.query.filter_by(active=True).order_by(Client.last_name, Client.first_name).all()
    return render_template('service_form.html', service=service,
                           clients=clients,
                           preselect_client=service.client_id,
                           service_types=Service.SERVICE_TYPES,
                           today=eastern_today())


MAX_QUICK_ADD_ROWS = 200


@bp.route('/services/quick-add', methods=['GET', 'POST'])
@login_required
def service_quick_add():
    all_clients = Client.active_sorted()
    weekly_clients = [c for c in all_clients if c.is_weekly]

    if request.method == 'POST':
        mode = request.form.get('mode', 'checklist')

        if mode == 'grid':
            # --- Grid mode: spreadsheet-style bulk entry ---
            VALID_SERVICE_TYPES = {st[0] for st in Service.SERVICE_TYPES}
            valid_client_ids = {c.id for c in all_clients}
            staged = []
            errors = []
            seen = set()

            for position, raw_idx in enumerate(
                    request.form.getlist('row_index')[:MAX_QUICK_ADD_ROWS], start=1):
                if raw_idx in seen:
                    continue
                seen.add(raw_idx)

                date_str = request.form.get(f'service_date_{raw_idx}', '').strip()
                client_str = request.form.get(f'client_id_{raw_idx}', '').strip()
                type_str = request.form.get(f'service_type_{raw_idx}', '').strip()
                amount_str = request.form.get(f'amount_{raw_idx}', '').strip()
                description = request.form.get(f'description_{raw_idx}', '').strip()[:250]

                # service_date and service_type always carry a value (a prefilled
                # input and a select with no empty option), so neither marks a row
                # as intentionally filled. Mirrors MEANINGFUL in the grid's JS.
                if not (client_str or amount_str or description):
                    continue

                try:
                    service_date_val = date.fromisoformat(date_str)
                except ValueError:
                    errors.append(f'Row {position}: enter a valid date.')
                    continue

                try:
                    client_id = int(client_str)
                except ValueError:
                    client_id = 0
                if client_id not in valid_client_ids:
                    errors.append(f'Row {position}: choose a client.')
                    continue

                if type_str not in VALID_SERVICE_TYPES:
                    errors.append(f'Row {position}: choose a service type.')
                    continue

                try:
                    amount = parse_amount(amount_str)
                except ValueError:
                    errors.append(f'Row {position}: enter a valid amount.')
                    continue
                if amount <= 0:
                    errors.append(f'Row {position}: amount must be more than $0.')
                    continue

                staged.append(Service(
                    client_id=client_id,
                    service_date=service_date_val,
                    service_type=type_str,
                    description=description,
                    amount=amount,
                ))

            if errors:
                for msg in errors[:5]:
                    flash(msg, 'danger')
                if len(errors) > 5:
                    flash(f'…and {len(errors) - 5} more row(s) with problems.', 'danger')
                return redirect(url_for('services_bp.service_quick_add'))

            if not staged:
                flash('No services entered.', 'warning')
                return redirect(url_for('services_bp.service_quick_add'))

            for s in staged:
                db.session.add(s)
            db.session.commit()
            total = sum(s.amount for s in staged)
            flash(f'{len(staged)} service(s) recorded (${total:,.2f}).', 'success')
            return redirect(url_for('services_bp.services'))

        else:
            # --- Checklist mode: mowing run ---
            try:
                service_date_val = date.fromisoformat(request.form['service_date'])
            except (ValueError, KeyError):
                flash('Please enter a valid service date.', 'danger')
                return redirect(url_for('services_bp.service_quick_add'))
            count = 0
            for client in weekly_clients:
                checkbox = request.form.get(f'client_{client.id}')
                if checkbox:
                    rate_override = request.form.get(f'rate_{client.id}', '')
                    try:
                        amount = parse_amount(rate_override) if rate_override else client.default_mowing_rate
                        if amount <= 0:
                            raise ValueError
                    except ValueError:
                        amount = client.default_mowing_rate
                    service = Service(
                        client_id=client.id,
                        service_date=service_date_val,
                        service_type='mowing',
                        description='Weekly mowing',
                        amount=amount,
                    )
                    db.session.add(service)
                    count += 1
            db.session.commit()
            flash(f'{count} mowing service(s) recorded.', 'success')
            return redirect(url_for('services_bp.services'))

    # Build route-grouped data for checklist tab
    routes = Route.all_sorted()
    route_groups = []
    for route in routes:
        route_clients = [c for c in weekly_clients if c.route_id == route.id]
        if route_clients:
            route_groups.append((route, route_clients))
    unassigned = [c for c in weekly_clients if c.route_id is None]

    # Build client rates map for JS autofill
    client_rates = {c.id: c.default_mowing_rate for c in all_clients}

    return render_template('service_quick_add.html',
                           clients=all_clients,
                           weekly_clients=weekly_clients,
                           route_groups=route_groups,
                           unassigned=unassigned,
                           service_types=Service.SERVICE_TYPES,
                           client_rates=client_rates,
                           today=eastern_today(),
                           blank_rows=1,
                           max_rows=MAX_QUICK_ADD_ROWS)


@bp.route('/services/<int:service_id>/delete', methods=['POST'])
@login_required
def service_delete(service_id):
    service = db.session.get(Service, service_id) or abort(404)
    # Block deletion if service is linked to an invoice or an expense
    if service.line_item is not None:
        flash(f'Cannot delete this service — it is linked to invoice {service.line_item.invoice.invoice_number}.', 'danger')
        return redirect(url_for('services_bp.services'))
    if service.expenses:
        flash(f'Cannot delete this service — {len(service.expenses)} expense(s) are linked to it. '
              'Unlink those expenses first.', 'danger')
        return redirect(url_for('services_bp.services'))
    db.session.delete(service)
    db.session.commit()
    flash('Service deleted.', 'warning')
    return redirect(url_for('services_bp.services'))


@bp.route('/services/bulk-delete', methods=['POST'])
@login_required
def service_bulk_delete():
    raw_ids = request.form.getlist('service_ids')
    ids = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (ValueError, TypeError):
            pass
    if not ids:
        flash('No services selected.', 'warning')
        return redirect(url_for('services_bp.services'))

    deleted = 0
    for sid in ids:
        service = db.session.get(Service, sid)
        if service is None:
            continue
        if service.line_item is not None:
            flash(
                f'Skipped: service on {service.service_date} for '
                f'{service.client.full_name} is linked to invoice '
                f'{service.line_item.invoice.invoice_number}.',
                'warning'
            )
            continue
        if service.expenses:
            flash(
                f'Skipped: service on {service.service_date} for '
                f'{service.client.full_name} has {len(service.expenses)} linked expense(s).',
                'warning'
            )
            continue
        db.session.delete(service)
        deleted += 1

    db.session.commit()
    if deleted:
        flash(f'{deleted} service(s) deleted.', 'warning')
    return redirect(url_for('services_bp.services'))


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

@bp.route('/api/client/<int:client_id>/uninvoiced-services')
@login_required
def api_uninvoiced_services(client_id):
    client = db.session.get(Client, client_id)
    if not client:
        return jsonify([])
    uninvoiced = client.uninvoiced_services()
    return jsonify([{
        'id': s.id,
        'date': s.service_date.isoformat(),
        'date_display': s.service_date.strftime('%m/%d/%Y'),
        'type': s.service_type_display,
        'description': s.description,
        'amount': s.amount,
    } for s in uninvoiced])
