from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from models import Client, Route, db

bp = Blueprint('routes_bp', __name__)


@bp.route('/mowing-routes')
@login_required
def route_list():
    routes = Route.all_sorted()
    unassigned = Client.query.filter_by(active=True, route_id=None).order_by(
        Client.last_name, Client.first_name
    ).all()
    return render_template('mowing_routes.html', routes=routes, unassigned=unassigned)


@bp.route('/mowing-routes/new', methods=['GET', 'POST'])
@login_required
def route_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Route name is required.', 'danger')
            return redirect(url_for('routes_bp.route_new'))

        route = Route(name=name)
        db.session.add(route)
        db.session.flush()

        selected_ids = request.form.getlist('client_ids')
        for pos, cid_str in enumerate(selected_ids, start=1):
            try:
                cid = int(cid_str)
            except (ValueError, TypeError):
                continue
            client = db.session.get(Client, cid)
            if client and client.active and client.route_id is None:
                client.route_id = route.id
                client.route_position = pos

        db.session.commit()
        flash(f'Route "{route.name}" created.', 'success')
        return redirect(url_for('routes_bp.route_list'))

    available = Client.query.filter_by(active=True, route_id=None).order_by(
        Client.last_name, Client.first_name
    ).all()
    return render_template('mowing_route_form.html', route=None, assigned=[], available=available)


@bp.route('/mowing-routes/<int:route_id>/edit', methods=['GET', 'POST'])
@login_required
def route_edit(route_id):
    route = db.session.get(Route, route_id) or abort(404)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Route name is required.', 'danger')
            return redirect(url_for('routes_bp.route_edit', route_id=route_id))

        route.name = name

        # Remove clients that were unchecked
        remove_ids = request.form.getlist('remove_ids')
        for cid_str in remove_ids:
            try:
                cid = int(cid_str)
            except (ValueError, TypeError):
                continue
            client = db.session.get(Client, cid)
            if client and client.route_id == route.id:
                client.route_id = None
                client.route_position = None

        # Add newly selected clients
        add_ids = request.form.getlist('client_ids')
        for cid_str in add_ids:
            try:
                cid = int(cid_str)
            except (ValueError, TypeError):
                continue
            client = db.session.get(Client, cid)
            if client and client.active and client.route_id is None:
                client.route_id = route.id

        # Recalculate positions for all clients in this route
        # Use the ordered_ids hidden field if provided (drag-and-drop reorder)
        ordered_ids_str = request.form.get('ordered_ids', '').strip()
        if ordered_ids_str:
            ordered_ids = []
            for v in ordered_ids_str.split(','):
                try:
                    ordered_ids.append(int(v.strip()))
                except (ValueError, TypeError):
                    pass
        else:
            ordered_ids = None

        remaining = route.clients.filter_by(active=True).order_by(
            Client.route_position, Client.last_name, Client.first_name
        ).all()

        if ordered_ids:
            # Reorder based on the provided order
            id_to_client = {c.id: c for c in remaining}
            pos = 1
            for cid in ordered_ids:
                if cid in id_to_client:
                    id_to_client[cid].route_position = pos
                    pos += 1
            # Any remaining clients not in ordered_ids get appended
            for c in remaining:
                if c.id not in set(ordered_ids):
                    c.route_position = pos
                    pos += 1
        else:
            for pos, c in enumerate(remaining, start=1):
                c.route_position = pos

        db.session.commit()
        flash(f'Route "{route.name}" updated.', 'success')
        return redirect(url_for('routes_bp.route_list'))

    assigned = route.clients.filter_by(active=True).order_by(
        Client.route_position, Client.last_name, Client.first_name
    ).all()
    available = Client.query.filter_by(active=True, route_id=None).order_by(
        Client.last_name, Client.first_name
    ).all()
    return render_template('mowing_route_form.html', route=route,
                           assigned=assigned, available=available)


@bp.route('/mowing-routes/<int:route_id>/delete', methods=['POST'])
@login_required
def route_delete(route_id):
    route = db.session.get(Route, route_id) or abort(404)
    name = route.name
    # Unassign all clients
    for client in route.clients.all():
        client.route_id = None
        client.route_position = None
    db.session.delete(route)
    db.session.commit()
    flash(f'Route "{name}" deleted. Clients have been unassigned.', 'warning')
    return redirect(url_for('routes_bp.route_list'))


@bp.route('/api/mowing-routes/move-client', methods=['POST'])
@login_required
def route_move_client():
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    target_route_id = data.get('target_route_id')  # int or None
    source_ordered_ids = data.get('source_ordered_ids', [])
    target_ordered_ids = data.get('target_ordered_ids', [])

    if not client_id:
        return jsonify(success=False, error='client_id required'), 400

    client = db.session.get(Client, client_id)
    if not client or not client.active:
        return jsonify(success=False, error='Client not found'), 404

    client.route_id = target_route_id  # None = unassigned
    client.route_position = None

    try:
        for pos, cid in enumerate(source_ordered_ids, start=1):
            c = db.session.get(Client, cid)
            if c:
                c.route_position = pos
        for pos, cid in enumerate(target_ordered_ids, start=1):
            c = db.session.get(Client, cid)
            if c:
                c.route_position = pos
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e)), 500

    return jsonify(success=True)
