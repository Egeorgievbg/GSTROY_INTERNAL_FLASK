from __future__ import annotations

import os
import threading

from database import SessionLocal
from models import Product

from app.services.search_service import ProductSearchService


def _index_all_products(app):
    with app.app_context():
        service = ProductSearchService(app)
        if not service.is_enabled():
            return
        if not service.ping():
            app.logger.warning("Elasticsearch is not reachable; search will fallback to SQL.")
            return
        if not service.ensure_index():
            return

        required_fields = [
            "name_translit",
            "brand_translit",
            "category_translit",
            "primary_group_translit",
            "secondary_group_translit",
            "name_suggest",
            "brand_suggest",
            "category_suggest",
            "primary_group_suggest",
            "secondary_group_suggest",
            "name_translit_suggest",
            "brand_translit_suggest",
            "category_translit_suggest",
            "primary_group_translit_suggest",
            "secondary_group_translit_suggest",
        ]
        if not service.mapping_has_fields(required_fields):
            if not service.rebuild_index():
                return

        session = SessionLocal()
        try:
            product_count = session.query(Product.id).count()
            doc_count = service.count_documents()
            force_reindex = bool(app.config.get("ELASTICSEARCH_FORCE_REINDEX", False))

            if product_count == 0:
                return

            if not force_reindex and doc_count == product_count:
                return

            if force_reindex or doc_count != product_count:
                if not service.rebuild_index():
                    return

            batch_size = app.config.get("ELASTICSEARCH_BATCH_SIZE", 1000)
            last_id = 0
            while True:
                batch = (
                    session.query(Product)
                    .filter(Product.id > last_id)
                    .order_by(Product.id)
                    .limit(batch_size)
                    .all()
                )
                if not batch:
                    break
                service.bulk_index(batch)
                last_id = batch[-1].id
        finally:
            session.close()


def schedule_search_index(app):
    if not app.config.get("ELASTICSEARCH_AUTO_INDEX", True):
        return
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    thread = threading.Thread(target=_index_all_products, args=(app,), daemon=True)
    thread.start()
