from flask import Flask


def register_blueprints(app: Flask) -> None:
    from routes.clients import bp as clients_bp
    from routes.core import bp as core_bp
    from routes.expenses import bp as expenses_bp
    from routes.invoices import bp as invoices_bp
    from routes.landing import bp as landing_bp
    from routes.payments import bp as payments_bp
    from routes.payroll import bp as payroll_bp
    from routes.portal import bp as portal_bp
    from routes.routes_bp import bp as mowing_routes_bp
    from routes.services_bp import bp as services_bp
    from routes.settings import bp as settings_bp
    from routes.staff import bp as staff_bp
    from routes.vendors import bp as vendors_bp

    app.register_blueprint(landing_bp)
    app.register_blueprint(core_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(services_bp)
    app.register_blueprint(invoices_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(vendors_bp)
    app.register_blueprint(payroll_bp)
    app.register_blueprint(mowing_routes_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(settings_bp)
