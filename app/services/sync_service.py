from datetime import datetime
import traceback

import requests
from flask import current_app, g

from app.blueprints.catalog_sync import BrandRegistry, CategoryRegistry, extract_category_levels
from app.services.search_service import ProductSearchService
from models import MasterProduct, Product, SyncLog


class ProductSyncService:
    def __init__(self, session=None):
        self.session = session or getattr(g, "db", None)
        if self.session is None:
            raise RuntimeError("Database session is required for sync")

    @staticmethod
    def _normalize_text(value):
        if value is None:
            return None
        return str(value).strip()

    @staticmethod
    def _coerce_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _chunked(values, chunk_size):
        for idx in range(0, len(values), chunk_size):
            yield values[idx : idx + chunk_size]

    def get_data_from_api(self):
        url = current_app.config.get("NOMEN_API_URL")
        if not url:
            raise RuntimeError("NOMEN_API_URL is not configured")
        timeout = current_app.config.get("NOMEN_API_TIMEOUT", 30)
        single_id = self._normalize_text(current_app.config.get("NOMEN_API_SINGLE_ID"))
        params = {"ids_nomen": single_id} if single_id else None
        try:
            response = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to fetch data from API: {exc}") from exc
        if response.status_code != 200:
            snippet = (response.text or "").strip()[:200]
            raise RuntimeError(f"API returned {response.status_code}: {snippet}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("API returned invalid JSON") from exc
        if isinstance(payload, dict):
            payload = payload.get("data") or payload.get("items")
        if not isinstance(payload, list):
            raise RuntimeError("API payload is not a list")
        return payload

    def _build_master_mapping(self, item, now):
        internal_id = self._coerce_int(item.get("ids_nomen"))
        return {
            "internal_id": internal_id,
            "name": self._normalize_text(item.get("name")),
            "barcode": self._normalize_text(item.get("barkod")),
            "vendor_code": self._normalize_text(item.get("katnomer")),
            "measure_unit": self._normalize_text(item.get("med")),
            "manufacturer": self._normalize_text(item.get("proizwoditel")),
            "group_name": self._normalize_text(item.get("ids_grupa_name")),
            "last_updated": now,
        }

    def _master_needs_update(self, existing, mapping):
        fields = [
            "name",
            "barcode",
            "vendor_code",
            "measure_unit",
            "manufacturer",
            "group_name",
        ]
        for field in fields:
            if self._normalize_text(getattr(existing, field)) != mapping[field]:
                return True
        return False

    def _build_product_mapping(self, item, brand_registry, category_registry):
        internal_id = self._coerce_int(item.get("ids_nomen"))
        item_number = self._normalize_text(item.get("nomer"))
        if not item_number and internal_id is not None:
            item_number = str(internal_id)
        name = self._normalize_text(item.get("name"))
        if not item_number or not name:
            return None

        manufacturer = self._normalize_text(item.get("proizwoditel"))
        barcode = self._normalize_text(item.get("barkod"))
        catalog_number = self._normalize_text(item.get("katnomer"))
        main_unit = self._normalize_text(item.get("med")) or "pcs"
        primary_group = self._normalize_text(item.get("ids_nom_osn_grupa_name"))
        secondary_group = self._normalize_text(item.get("ids_grupa_name"))
        group = self._normalize_text(item.get("ids_nom_kat_name"))

        brand = brand_registry.ensure(manufacturer) if manufacturer else None
        category = None
        if primary_group or secondary_group or group:
            levels = extract_category_levels(
                {
                    "primary_group": primary_group,
                    "secondary_group": secondary_group,
                    "tertiary_group": None,
                    "group": group,
                    "category": None,
                    "subgroup": None,
                    "quaternary_group": None,
                }
            )
            category = category_registry.ensure_for_levels(levels)

        mapping = {
            "item_number": item_number,
            "name": name,
            "barcode": barcode,
            "catalog_number": catalog_number,
            "main_unit": main_unit,
            "manufacturer_name": manufacturer,
            "brand": manufacturer,
            "primary_group": primary_group,
            "secondary_group": secondary_group,
            "group": group,
            "versus_id": str(internal_id) if internal_id is not None else None,
            "is_active": True,
        }
        if brand:
            mapping["brand_id"] = brand.id
            mapping["brand"] = brand.name
        if category:
            mapping["category_id"] = category.id
            mapping["category"] = category.full_address
        return mapping

    def _product_needs_update(self, existing, mapping):
        text_fields = [
            "name",
            "barcode",
            "catalog_number",
            "main_unit",
            "manufacturer_name",
            "brand",
            "category",
            "primary_group",
            "secondary_group",
            "group",
            "versus_id",
        ]
        for field in text_fields:
            current = self._normalize_text(getattr(existing, field))
            incoming = self._normalize_text(mapping.get(field))
            if current != incoming:
                return True
        id_fields = ["brand_id", "category_id"]
        for field in id_fields:
            if getattr(existing, field) != mapping.get(field):
                return True
        if getattr(existing, "is_active", None) != mapping.get("is_active"):
            return True
        return False

    def _index_catalog_updates(self, session, inserts, updates, deactivations):
        service = ProductSearchService(current_app)
        if not service.is_enabled():
            return
        if not service.ensure_index():
            return
        item_numbers = {
            mapping.get("item_number")
            for mapping in inserts
            if mapping.get("item_number")
        }
        ids = {mapping.get("id") for mapping in updates if mapping.get("id")}
        ids.update({mapping.get("id") for mapping in deactivations if mapping.get("id")})

        products = []
        chunk_size = int(current_app.config.get("NOMEN_SYNC_QUERY_CHUNK_SIZE", 900))
        if item_numbers:
            for chunk in self._chunked(list(item_numbers), chunk_size):
                products.extend(
                    session.query(Product)
                    .filter(Product.item_number.in_(chunk))
                    .all()
                )
        if ids:
            for chunk in self._chunked(list(ids), chunk_size):
                products.extend(
                    session.query(Product)
                    .filter(Product.id.in_(chunk))
                    .all()
                )
        if not products:
            return
        unique_products = {product.id: product for product in products}
        service.bulk_index(unique_products.values())

    def run_sync(self, triggered_by="System", apply_to_catalog=True, deactivate_missing=True):
        session = self.session
        log = SyncLog(
            started_at=datetime.utcnow(),
            status="IN_PROGRESS",
            triggered_by=triggered_by,
        )
        session.add(log)
        session.commit()

        try:
            payload = self.get_data_from_api()
            now = datetime.utcnow()
            total_fetched = len(payload)

            existing_master_rows = (
                session.query(
                    MasterProduct.id,
                    MasterProduct.internal_id,
                    MasterProduct.name,
                    MasterProduct.barcode,
                    MasterProduct.vendor_code,
                    MasterProduct.measure_unit,
                    MasterProduct.manufacturer,
                    MasterProduct.group_name,
                )
                .all()
            )
            existing_master_map = {
                row.internal_id: row for row in existing_master_rows if row.internal_id is not None
            }
            existing_product_rows = []
            existing_product_map = {}
            brand_registry = None
            category_registry = None
            if apply_to_catalog:
                existing_product_rows = (
                    session.query(
                        Product.id,
                        Product.item_number,
                        Product.name,
                        Product.barcode,
                        Product.catalog_number,
                        Product.main_unit,
                        Product.manufacturer_name,
                        Product.brand,
                        Product.brand_id,
                        Product.category,
                        Product.category_id,
                        Product.primary_group,
                        Product.secondary_group,
                        Product.group,
                        Product.versus_id,
                        Product.is_active,
                    )
                    .all()
                )
                existing_product_map = {
                    row.item_number: row for row in existing_product_rows if row.item_number
                }
                brand_registry = BrandRegistry(session)
                category_registry = CategoryRegistry(session)

            master_inserts = []
            master_updates = []
            product_inserts = []
            product_updates = []
            product_deactivations = []
            seen_internal_ids = set()
            seen_item_numbers = set()

            for item in payload:
                master_mapping = self._build_master_mapping(item, now)
                internal_id = master_mapping["internal_id"]
                if internal_id is not None and internal_id not in seen_internal_ids:
                    seen_internal_ids.add(internal_id)
                    existing_master = existing_master_map.get(internal_id)
                    if existing_master is None:
                        master_inserts.append(master_mapping)
                    elif self._master_needs_update(existing_master, master_mapping):
                        master_mapping["id"] = existing_master.id
                        master_updates.append(master_mapping)

                if not apply_to_catalog:
                    continue

                product_mapping = self._build_product_mapping(
                    item, brand_registry, category_registry
                )
                if not product_mapping:
                    continue
                item_number = product_mapping["item_number"]
                if item_number in seen_item_numbers:
                    continue
                seen_item_numbers.add(item_number)

                existing_product = existing_product_map.get(item_number)
                if existing_product is None:
                    product_inserts.append(product_mapping)
                elif self._product_needs_update(existing_product, product_mapping):
                    product_mapping["id"] = existing_product.id
                    product_mapping.pop("item_number", None)
                    product_updates.append(product_mapping)

            if master_inserts:
                session.bulk_insert_mappings(MasterProduct, master_inserts)
            if master_updates:
                session.bulk_update_mappings(MasterProduct, master_updates)

            if apply_to_catalog:
                if product_inserts:
                    session.bulk_insert_mappings(Product, product_inserts)
                if product_updates:
                    session.bulk_update_mappings(Product, product_updates)
                if deactivate_missing:
                    for existing in existing_product_rows:
                        if not existing.item_number or existing.item_number in seen_item_numbers:
                            continue
                        if existing.is_active:
                            product_deactivations.append(
                                {
                                    "id": existing.id,
                                    "is_active": False,
                                }
                            )
                    if product_deactivations:
                        session.bulk_update_mappings(Product, product_deactivations)

            log = session.get(SyncLog, log.id)
            log.status = "SUCCESS"
            log.completed_at = datetime.utcnow()
            log.total_fetched = total_fetched
            if apply_to_catalog:
                log.created_count = len(product_inserts)
                log.updated_count = len(product_updates) + len(product_deactivations)
            else:
                log.created_count = len(master_inserts)
                log.updated_count = len(master_updates)
            session.commit()
            if apply_to_catalog and (product_inserts or product_updates or product_deactivations):
                try:
                    self._index_catalog_updates(
                        session,
                        product_inserts,
                        product_updates,
                        product_deactivations,
                    )
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
