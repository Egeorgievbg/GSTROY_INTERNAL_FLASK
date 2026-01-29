from datetime import datetime
import csv
import re
import traceback
from io import StringIO

import requests
from flask import current_app, g

from app.services.search_service import ProductSearchService
from models import Product, SyncLog


class ProductFeedSyncService:
    def __init__(self, session=None):
        self.session = session or getattr(g, "db", None)
        if self.session is None:
            raise RuntimeError("Database session is required for sync")

    @staticmethod
    def _normalize_text(value):
        if value is None:
            return None
        text = str(value).strip()
        return text if text else None

    @staticmethod
    def _normalize_row(row):
        normalized = {}
        for key, value in (row or {}).items():
            if key is None:
                continue
            normalized[str(key).strip().lower()] = value
        return normalized

    @staticmethod
    def _coerce_float(value):
        if value is None:
            return None
        raw = str(value)
        match = re.search(r"[-+]?\d+(?:[.,]\d+)?", raw)
        if not match:
            return None
        try:
            return float(match.group(0).replace(",", "."))
        except ValueError:
            return None

    @staticmethod
    def _is_placeholder_image(url):
        if not url:
            return False
        lower = url.lower()
        return "no_image" in lower or "no-image" in lower

    @staticmethod
    def _chunked(values, chunk_size):
        for idx in range(0, len(values), chunk_size):
            yield values[idx : idx + chunk_size]

    def _fetch_products_by_field(self, field_name, ids):
        if not ids:
            return {}
        field = getattr(Product, field_name)
        results = {}
        chunk_size = int(current_app.config.get("FB_FEED_QUERY_CHUNK_SIZE", 900))
        for chunk in self._chunked(list(ids), chunk_size):
            for product in self.session.query(Product).filter(field.in_(chunk)).all():
                key = getattr(product, field_name)
                if key:
                    results[key] = product
        return results

    def _read_feed_rows(self):
        url = current_app.config.get("FB_FEED_URL")
        if not url:
            raise RuntimeError("FB_FEED_URL is not configured")
        timeout = current_app.config.get("FB_FEED_TIMEOUT", 30)
        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to fetch FB feed: {exc}") from exc
        if response.status_code != 200:
            snippet = (response.text or "").strip()[:200]
            raise RuntimeError(f"FB feed returned {response.status_code}: {snippet}")

        raw = response.content or b""
        data = None
        for encoding in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                data = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if data is None:
            data = raw.decode("utf-8", errors="ignore")

        sample = data[:4096]
        delimiter = ","
        try:
            sniffed = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
            delimiter = sniffed.delimiter
        except csv.Error:
            delimiter_counts = {
                ",": sample.count(","),
                ";": sample.count(";"),
                "\t": sample.count("\t"),
            }
            if any(delimiter_counts.values()):
                delimiter = max(delimiter_counts, key=delimiter_counts.get)

        reader = csv.DictReader(StringIO(data), delimiter=delimiter)
        if not reader.fieldnames:
            raise RuntimeError("FB feed CSV is missing headers")
        return [self._normalize_row(row) for row in reader]

    @staticmethod
    def _value_differs(current_value, incoming_value):
        if incoming_value is None:
            return False
        if isinstance(incoming_value, bool):
            return bool(current_value) != incoming_value
        if isinstance(incoming_value, (int, float)):
            try:
                current_float = float(current_value)
            except (TypeError, ValueError):
                return True
            return abs(current_float - float(incoming_value)) > 1e-6
        return (str(current_value).strip() if current_value is not None else None) != str(
            incoming_value
        ).strip()

    def _index_updated_products(self, session, updated_ids):
        if not updated_ids:
            return
        service = ProductSearchService(current_app)
        if not service.is_enabled() or not service.ensure_index():
            return
        chunk_size = int(current_app.config.get("FB_FEED_QUERY_CHUNK_SIZE", 900))
        for chunk in self._chunked(list(updated_ids), chunk_size):
            products = session.query(Product).filter(Product.id.in_(chunk)).all()
            if products:
                service.bulk_index(products)

    def run_sync(self, triggered_by="System"):
        session = self.session
        log = SyncLog(
            started_at=datetime.utcnow(),
            status="IN_PROGRESS",
            triggered_by=triggered_by,
        )
        session.add(log)
        session.commit()

        try:
            rows = self._read_feed_rows()
            total_fetched = len(rows)
            ids = {
                self._normalize_text(row.get("id"))
                for row in rows
                if self._normalize_text(row.get("id"))
            }
            if not ids:
                raise RuntimeError("FB feed did not include any product IDs")

            products_by_item = self._fetch_products_by_field("item_number", ids)
            products_by_versus = self._fetch_products_by_field("versus_id", ids)

            updates = []
            updated_ids = set()
            skip_placeholder = current_app.config.get("FB_FEED_SKIP_PLACEHOLDER_IMAGES", True)
            deactivate_missing = current_app.config.get("FB_FEED_DEACTIVATE_MISSING", True)

            for row in rows:
                raw_id = self._normalize_text(row.get("id"))
                if not raw_id:
                    continue
                product = products_by_item.get(raw_id) or products_by_versus.get(raw_id)
                if not product:
                    continue

                changes = {}
                title = self._normalize_text(row.get("title"))
                if title and not product.name:
                    if self._value_differs(product.name, title):
                        changes["name"] = title

                description = self._normalize_text(row.get("description"))
                if description:
                    if self._value_differs(product.long_description, description):
                        changes["long_description"] = description
                    if not product.short_description:
                        summary = description.splitlines()[0].strip() if description else ""
                        summary = summary[:240] if summary else None
                        if summary and self._value_differs(product.short_description, summary):
                            changes["short_description"] = summary

                image_link = self._normalize_text(row.get("image_link"))
                if image_link and not (skip_placeholder and self._is_placeholder_image(image_link)):
                    if self._value_differs(product.image_url, image_link):
                        changes["image_url"] = image_link

                brand = self._normalize_text(row.get("brand"))
                if brand and not product.brand:
                    if self._value_differs(product.brand, brand):
                        changes["brand"] = brand

                fb_category = self._normalize_text(row.get("fb_product_category"))
                if fb_category and not product.fb_category:
                    if self._value_differs(product.fb_category, fb_category):
                        changes["fb_category"] = fb_category

                google_category = self._normalize_text(row.get("google_product_category"))
                if google_category and not product.google_category:
                    if self._value_differs(product.google_category, google_category):
                        changes["google_category"] = google_category

                price = self._coerce_float(row.get("price"))
                sale_price = self._coerce_float(row.get("sale_price"))
                if price is not None:
                    if self._value_differs(product.price_unit_1, price):
                        changes["price_unit_1"] = price
                    if self._value_differs(product.visible_price_unit_1, price):
                        changes["visible_price_unit_1"] = price
                if sale_price is None and price is not None:
                    sale_price = price
                if sale_price is not None:
                    if self._value_differs(product.promo_price_unit_1, sale_price):
                        changes["promo_price_unit_1"] = sale_price

                if (
                    price is not None
                    and sale_price is not None
                    and sale_price < price
                    and not product.is_special_offer
                ):
                    changes["is_special_offer"] = True

                custom_label_2 = self._normalize_text(row.get("custom_label_2"))
                if custom_label_2 and "брошур" in custom_label_2.lower():
                    if not product.in_brochure:
                        changes["in_brochure"] = True

                if changes:
                    changes["id"] = product.id
                    updates.append(changes)
                    updated_ids.add(product.id)

            if deactivate_missing:
                active_products = (
                    session.query(Product)
                    .filter(Product.is_active.is_(True))
                    .filter(
                        (Product.item_number.isnot(None)) | (Product.versus_id.isnot(None))
                    )
                    .all()
                )
                for product in active_products:
                    item_key = self._normalize_text(product.item_number)
                    versus_key = self._normalize_text(product.versus_id)
                    if (item_key and item_key in ids) or (versus_key and versus_key in ids):
                        continue
                    updates.append({"id": product.id, "is_active": False})
                    updated_ids.add(product.id)

            if updates:
                session.bulk_update_mappings(Product, updates)

            log = session.get(SyncLog, log.id)
            log.status = "SUCCESS"
            log.completed_at = datetime.utcnow()
            log.total_fetched = total_fetched
            log.created_count = 0
            log.updated_count = len(updates)
            session.commit()

            if updates:
                try:
                    self._index_updated_products(session, updated_ids)
                except Exception as exc:
                    current_app.logger.warning("Elasticsearch update failed: %s", exc)

            return log
        except Exception:
            session.rollback()
            log = session.get(SyncLog, log.id)
            if log:
                log.status = "FAILED"
                log.completed_at = datetime.utcnow()
                log.error_message = traceback.format_exc()
                session.commit()
            return log
