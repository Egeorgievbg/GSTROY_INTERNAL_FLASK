from __future__ import annotations

import os
import sys

os.environ.setdefault("ELASTICSEARCH_AUTO_INDEX", "0")

from app import create_app
from database import SessionLocal
from models import Product
from app.services.search_service import ProductSearchService


def main() -> int:
    app = create_app()
    with app.app_context():
        service = ProductSearchService(app)
        if not service.is_enabled():
            print("Elasticsearch is disabled or missing. Set ELASTICSEARCH_ENABLED=1.")
            return 1

        rebuild = "--rebuild" in sys.argv or app.config.get("ELASTICSEARCH_FORCE_REINDEX", False)
        if rebuild:
            if not service.rebuild_index():
                print("Failed to rebuild index.")
                return 1
        elif not service.ensure_index():
            print("Failed to create or verify index.")
            return 1

        session = SessionLocal()
        batch_size = app.config.get("ELASTICSEARCH_BATCH_SIZE", 1000)
        try:
            total = session.query(Product).count()
            if total == 0:
                print("No products found to index.")
                return 0

            print(f"Indexing {total} products in batches of {batch_size}...")
            processed = 0
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
                processed += service.bulk_index(batch)
                last_id = batch[-1].id
                print(f"Indexed {min(last_id, total)} / {total}")
            print(f"Done. Indexed {processed} products.")
            return 0
        finally:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
