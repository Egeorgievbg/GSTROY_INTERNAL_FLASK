import os
from os import path

from flask import Flask, g
from flask_login import current_user

from database import SessionLocal, init_db
from extensions import csrf, login_manager
from printer_service import printer_bp
from app.blueprints.admin import admin_bp
from app.blueprints.auth import auth_bp
from app.blueprints.logistics import logistics_bp
from app.blueprints.main import main_bp
from app.blueprints.orders import orders_bp
from app.blueprints.products import products_bp
from app.blueprints.scanning import scanning_bp


def create_app():
    base_dir = path.abspath(path.dirname(path.dirname(__file__)))
    static_root = path.join(base_dir, "static")
    templates_root = path.join(base_dir, "templates")
    app = Flask(
        __name__,
        static_folder=static_root,
        static_url_path="/static",
        template_folder=templates_root,
    )
    app.secret_key = os.environ.get("GSTROY_SECRET_KEY", "change-me")
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    app.register_blueprint(admin_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(logistics_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(scanning_bp)
    app.register_blueprint(printer_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    init_db()

    @app.before_request
    def bind_db_session():
        g.db = SessionLocal()

    @app.before_request
    def attach_current_user():
        g.current_user = current_user

    @app.teardown_appcontext
    def remove_db_session(exception=None):
        SessionLocal.remove()

    return app
