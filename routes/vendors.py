from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from models import Vendor, db

bp = Blueprint('vendors', __name__)


@bp.route('/vendors')
@login_required
def vendor_list():
    search = request.args.get('search', '').strip()
    query = Vendor.query
    if search:
        query = query.filter(
            Vendor.name.ilike(f'%{search}%') |
            Vendor.contact_name.ilike(f'%{search}%')
        )
    vendors = query.filter_by(active=True).order_by(Vendor.name).all()
    return render_template('vendors.html', vendors=vendors, search=search)


@bp.route('/vendors/new', methods=['GET', 'POST'])
@login_required
def vendor_new():
    if request.method == 'POST':
        vendor = Vendor(
            name=request.form['name'].strip(),
            contact_name=request.form.get('contact_name', '').strip(),
            phone=request.form.get('phone', '').strip(),
            email=request.form.get('email', '').strip(),
            address=request.form.get('address', '').strip(),
            notes=request.form.get('notes', '').strip(),
        )
        db.session.add(vendor)
        db.session.commit()
        flash(f'Vendor "{vendor.name}" created.', 'success')
        return redirect(url_for('vendors.vendor_detail', vendor_id=vendor.id))
    return render_template('vendor_form.html', vendor=None)


@bp.route('/vendors/<int:vendor_id>')
@login_required
def vendor_detail(vendor_id):
    vendor = db.session.get(Vendor, vendor_id) or abort(404)
    expenses = vendor.expenses.limit(50).all()
    total_spent = vendor.total_spent()
    return render_template('vendor_detail.html', vendor=vendor,
                           expenses=expenses, total_spent=total_spent)


@bp.route('/vendors/<int:vendor_id>/edit', methods=['GET', 'POST'])
@login_required
def vendor_edit(vendor_id):
    vendor = db.session.get(Vendor, vendor_id) or abort(404)
    if request.method == 'POST':
        vendor.name = request.form['name'].strip()
        vendor.contact_name = request.form.get('contact_name', '').strip()
        vendor.phone = request.form.get('phone', '').strip()
        vendor.email = request.form.get('email', '').strip()
        vendor.address = request.form.get('address', '').strip()
        vendor.notes = request.form.get('notes', '').strip()
        vendor.active = 'active' in request.form
        db.session.commit()
        flash(f'Vendor "{vendor.name}" updated.', 'success')
        return redirect(url_for('vendors.vendor_detail', vendor_id=vendor.id))
    return render_template('vendor_form.html', vendor=vendor)


@bp.route('/vendors/<int:vendor_id>/delete', methods=['POST'])
@login_required
def vendor_delete(vendor_id):
    vendor = db.session.get(Vendor, vendor_id) or abort(404)
    vendor.active = False
    db.session.commit()
    flash(f'Vendor "{vendor.name}" deactivated.', 'warning')
    return redirect(url_for('vendors.vendor_list'))
