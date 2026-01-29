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
from app.blueprints.deliveries import deliveries_bp
from app.blueprints.academy import academy_bp
from app.blueprints.pdf_printers import pdf_printers_bp
from app.services.search_indexer import schedule_search_index
from app.services.pricemind_sync_scheduler import schedule_pricemind_sync


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
    app.register_blueprint(academy_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(logistics_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(scanning_bp)
    app.register_blueprint(deliveries_bp)
    app.register_blueprint(printer_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(pdf_printers_bp)
    try:
        upload_max = int(os.environ.get("UPLOAD_MAX_BYTES", "10485760"))
    except ValueError:
        upload_max = 10 * 1024 * 1024
    app.config.setdefault("UPLOAD_MAX_BYTES", upload_max)
    try:
        invoice_max = int(os.environ.get("INVOICE_UPLOAD_MAX_BYTES", "15728640"))
    except ValueError:
        invoice_max = 15 * 1024 * 1024
    app.config.setdefault("INVOICE_UPLOAD_MAX_BYTES", invoice_max)
    app.config.setdefault("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    app.config.setdefault(
        "INVOICE_OCR_MODEL",
        os.environ.get("INVOICE_OCR_MODEL", "gpt-4o-mini"),
    )
    try:
        ocr_timeout = int(os.environ.get("INVOICE_OCR_TIMEOUT", "60"))
    except ValueError:
        ocr_timeout = 60
    app.config.setdefault("INVOICE_OCR_TIMEOUT", ocr_timeout)
    try:
        ocr_pages = int(os.environ.get("INVOICE_OCR_MAX_PAGES", "5"))
    except ValueError:
        ocr_pages = 5
    app.config.setdefault("INVOICE_OCR_MAX_PAGES", ocr_pages)
    # When a PDF has more than this many pages, render JPEGs at lower zoom to reduce payload
    try:
        ocr_jpeg_threshold = int(os.environ.get("INVOICE_OCR_JPEG_THRESHOLD_PAGES", "4"))
    except ValueError:
        ocr_jpeg_threshold = 4
    app.config.setdefault("INVOICE_OCR_JPEG_THRESHOLD_PAGES", ocr_jpeg_threshold)
    try:
        ocr_large_zoom = float(os.environ.get("INVOICE_OCR_LARGE_PDF_ZOOM", "0.7"))
    except ValueError:
        ocr_large_zoom = 0.7
    app.config.setdefault("INVOICE_OCR_LARGE_PDF_ZOOM", ocr_large_zoom)
    app.config.setdefault("SIGNATURE_MAX_BYTES", 200_000)
    app.config.setdefault(
        "NOMEN_API_URL",
        os.environ.get(
            "NOMEN_API_URL",
            "http://109.104.213.2:8080/GsREST/resources/get_webnomeninfo",
        ),
    )
    app.config.setdefault("NOMEN_API_SINGLE_ID", os.environ.get("NOMEN_API_SINGLE_ID", ""))
    try:
        api_timeout = int(os.environ.get("NOMEN_API_TIMEOUT", "30"))
    except ValueError:
        api_timeout = 30
    app.config.setdefault("NOMEN_API_TIMEOUT", api_timeout)
    app.config.setdefault("NOMEN_SYNC_APPLY_TO_CATALOG", True)
    app.config.setdefault("NOMEN_SYNC_DEACTIVATE_MISSING", True)
    app.config.setdefault("NOMEN_SYNC_QUERY_CHUNK_SIZE", 900)
    app.config.setdefault(
        "FB_FEED_URL",
        os.environ.get(
            "FB_FEED_URL",
            "https://gsstroimarket.bg/catalog/Facebook_Catalog_Products.csv",
        ),
    )
    try:
        fb_timeout = int(os.environ.get("FB_FEED_TIMEOUT", "30"))
    except ValueError:
        fb_timeout = 30
    app.config.setdefault("FB_FEED_TIMEOUT", fb_timeout)
    app.config.setdefault("FB_FEED_SYNC_ENABLED", True)
    app.config.setdefault("FB_FEED_SKIP_PLACEHOLDER_IMAGES", True)
    app.config.setdefault("FB_FEED_DEACTIVATE_MISSING", True)
    app.config.setdefault("FB_FEED_QUERY_CHUNK_SIZE", 900)
    app.config.setdefault(
        "PRICEMIND_FEED_URL",
        os.environ.get(
            "PRICEMIND_FEED_URL",
            "https://cdn.pricemind.io/feeds/296/internal_ingegration_test.csv",
        ),
    )
    try:
        pricemind_timeout = int(os.environ.get("PRICEMIND_FEED_TIMEOUT", "30"))
    except ValueError:
        pricemind_timeout = 30
    app.config.setdefault("PRICEMIND_FEED_TIMEOUT", pricemind_timeout)
    app.config.setdefault("PRICEMIND_SYNC_ENABLED", True)
    app.config.setdefault("PRICEMIND_SYNC_INTERVAL_HOURS", 6)
    app.config.setdefault("PRICEMIND_SYNC_HISTORY_DAYS", 7)
    app.config.setdefault("PRICEMIND_SYNC_BATCH_SIZE", 1000)
    app.config.setdefault(
        "ARTINFO_API_URL",
        os.environ.get(
            "ARTINFO_API_URL",
            "http://109.104.213.2:8080/GsREST/resources/get_artinfopg",
        ),
    )
    try:
        art_timeout = int(os.environ.get("ARTINFO_API_TIMEOUT", "15"))
    except ValueError:
        art_timeout = 15
    app.config.setdefault("ARTINFO_API_TIMEOUT", art_timeout)
    try:
        art_cache_seconds = int(os.environ.get("ARTINFO_CACHE_SECONDS", "300"))
    except ValueError:
        art_cache_seconds = 300
    app.config.setdefault("ARTINFO_CACHE_SECONDS", art_cache_seconds)
    app.config.setdefault(
        "ARTINFO_PRICE_FIELD",
        os.environ.get("ARTINFO_PRICE_FIELD", "cena1_me1"),
    )
    es_enabled_raw = os.environ.get("ELASTICSEARCH_ENABLED")
    if es_enabled_raw is None:
        es_enabled = True
    else:
        es_enabled = es_enabled_raw.lower() in ("1", "true", "yes", "on")
    app.config.setdefault(
        "ELASTICSEARCH_ENABLED",
        es_enabled,
    )
    app.config.setdefault(
        "ELASTICSEARCH_URL",
        os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200"),
    )
    app.config.setdefault(
        "ELASTICSEARCH_INDEX",
        os.environ.get("ELASTICSEARCH_INDEX", "gstroy-products"),
    )
    try:
        es_timeout = int(os.environ.get("ELASTICSEARCH_TIMEOUT", "5"))
    except ValueError:
        es_timeout = 5
    app.config.setdefault("ELASTICSEARCH_TIMEOUT", es_timeout)
    app.config.setdefault(
        "ELASTICSEARCH_VERIFY_CERTS",
        os.environ.get("ELASTICSEARCH_VERIFY_CERTS", "").lower()
        in ("1", "true", "yes", "on"),
    )
    app.config.setdefault("ELASTICSEARCH_USERNAME", os.environ.get("ELASTICSEARCH_USERNAME", ""))
    app.config.setdefault("ELASTICSEARCH_PASSWORD", os.environ.get("ELASTICSEARCH_PASSWORD", ""))
    try:
        es_batch = int(os.environ.get("ELASTICSEARCH_BATCH_SIZE", "1000"))
    except ValueError:
        es_batch = 1000
    app.config.setdefault("ELASTICSEARCH_BATCH_SIZE", es_batch)
    es_auto_raw = os.environ.get("ELASTICSEARCH_AUTO_INDEX")
    if es_auto_raw is None:
        es_auto = True
    else:
        es_auto = es_auto_raw.lower() in ("1", "true", "yes", "on")
    app.config.setdefault("ELASTICSEARCH_AUTO_INDEX", es_auto)
    es_force_raw = os.environ.get("ELASTICSEARCH_FORCE_REINDEX")
    if es_force_raw is None:
        es_force = False
    else:
        es_force = es_force_raw.lower() in ("1", "true", "yes", "on")
    app.config.setdefault("ELASTICSEARCH_FORCE_REINDEX", es_force)
    init_db()
    schedule_search_index(app)
    schedule_pricemind_sync(app)

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
