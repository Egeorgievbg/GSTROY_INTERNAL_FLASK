from __future__ import annotations

import csv
import json
import re
import traceback
from datetime import datetime, timedelta
from io import StringIO

import requests
from flask import current_app, g

from models import (
    PricemindCompetitorPrice,
    PricemindSnapshot,
    PricemindSyncLog,
    Product,
)


class PricemindSyncService:
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
    def _coerce_float(value):
        if value in (None, ""):
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
    def _coerce_int(value):
        if value in (None, ""):
            return None
        try:
            return int(float(str(value).replace(",", ".")))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_datetime(value):
        if not value:
            return None
        raw = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _chunked(values, chunk_size):
        for idx in range(0, len(values), chunk_size):
            yield values[idx : idx + chunk_size]

    def _read_feed_rows(self):
        url = current_app.config.get("PRICEMIND_FEED_URL")
        if not url:
            raise RuntimeError("PRICEMIND_FEED_URL is not configured")
        timeout = current_app.config.get("PRICEMIND_FEED_TIMEOUT", 30)
        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to fetch Pricemind feed: {exc}") from exc
        if response.status_code != 200:
            snippet = (response.text or "").strip()[:200]
            raise RuntimeError(f"Pricemind feed returned {response.status_code}: {snippet}")

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
            raise RuntimeError("Pricemind CSV is missing headers")
        return reader

    @staticmethod
    def _normalize_row(row):
        normalized = {}
        for key, value in (row or {}).items():
            if key is None:
                continue
            normalized[str(key).strip().lower()] = value
        return normalized

    @staticmethod
    def _discover_competitors(fieldnames):
        competitors = {}
        for name in fieldnames:
            if not name:
                continue
            if name.endswith(" Offer"):
                competitor = name[: -len(" Offer")].strip()
                competitors[competitor] = {
                    "offer": name,
                    "regular": f"Regular {competitor}",
                    "special": f"Special {competitor}",
                    "stock": f"{competitor} Stock",
                    "retrieved": f"{competitor} Retrieved At",
                }
        return competitors

    def _cleanup_history(self):
        retention_days = int(current_app.config.get("PRICEMIND_SYNC_HISTORY_DAYS", 7))
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        old_logs = (
            self.session.query(PricemindSyncLog)
            .filter(PricemindSyncLog.started_at < cutoff)
            .all()
        )
        if not old_logs:
            return
        log_ids = [log.id for log in old_logs]
        snapshot_ids = [
            row[0]
            for row in self.session.query(PricemindSnapshot.id)
            .filter(PricemindSnapshot.sync_log_id.in_(log_ids))
            .all()
        ]
        chunk_size = int(current_app.config.get("PRICEMIND_SYNC_BATCH_SIZE", 1000))
        for chunk in self._chunked(snapshot_ids, chunk_size):
            (
                self.session.query(PricemindCompetitorPrice)
                .filter(PricemindCompetitorPrice.snapshot_id.in_(chunk))
                .delete(synchronize_session=False)
            )
        for chunk in self._chunked(snapshot_ids, chunk_size):
            (
                self.session.query(PricemindSnapshot)
                .filter(PricemindSnapshot.id.in_(chunk))
                .delete(synchronize_session=False)
            )
        for chunk in self._chunked(log_ids, chunk_size):
            (
                self.session.query(PricemindSyncLog)
                .filter(PricemindSyncLog.id.in_(chunk))
                .delete(synchronize_session=False)
            )
        self.session.commit()

    def run_sync(self, triggered_by="System"):
        session = self.session
        log = PricemindSyncLog(
            started_at=datetime.utcnow(),
            status="IN_PROGRESS",
            triggered_by=triggered_by,
        )
        session.add(log)
        session.commit()

        try:
            reader = self._read_feed_rows()
            fieldnames = reader.fieldnames or []
            competitors = self._discover_competitors(fieldnames)
            product_rows = session.query(
                Product.id,
                Product.item_number,
                Product.catalog_number,
            ).all()
            item_map = {
                self._normalize_text(row.item_number): row.id
                for row in product_rows
                if self._normalize_text(row.item_number)
            }
            catalog_map = {
                self._normalize_text(row.catalog_number): row.id
                for row in product_rows
                if self._normalize_text(row.catalog_number)
            }

            total_rows = 0
            matched = 0
            unmatched = 0
            updated = 0
            batch_size = int(current_app.config.get("PRICEMIND_SYNC_BATCH_SIZE", 1000))
            snapshot_batch = []

            def flush_batch():
                nonlocal updated
                if not snapshot_batch:
                    return
                session.bulk_insert_mappings(
                    PricemindSnapshot,
                    snapshot_batch,
                    return_defaults=True,
                )
                competitor_rows = []
                for snapshot in snapshot_batch:
                    competitors_payload = snapshot.pop("_competitors", [])
                    snapshot_id = snapshot.get("id")
                    if not snapshot_id:
                        continue
                    for comp in competitors_payload:
                        competitor_rows.append(
                            {
                                "snapshot_id": snapshot_id,
                                "competitor": comp.get("competitor"),
                                "offer_price": comp.get("offer_price"),
                                "regular_price": comp.get("regular_price"),
                                "special_price": comp.get("special_price"),
                                "stock": comp.get("stock"),
                                "retrieved_at": comp.get("retrieved_at"),
                            }
                        )
                if competitor_rows:
                    session.bulk_insert_mappings(
                        PricemindCompetitorPrice,
                        competitor_rows,
                    )
                updated += len(snapshot_batch)
                session.commit()
                snapshot_batch.clear()

            for row in reader:
                total_rows += 1
                normalized_row = self._normalize_row(row)

                sku = self._normalize_text(normalized_row.get("sku"))
                if not sku:
                    continue
                catalog_number = self._normalize_text(normalized_row.get("catalog number"))
                product_id = item_map.get(sku) or (catalog_map.get(catalog_number) if catalog_number else None)
                if product_id:
                    matched += 1
                else:
                    unmatched += 1

                snapshot = {
                    "sync_log_id": log.id,
                    "product_id": product_id,
                    "sku": sku,
                    "catalog_number": catalog_number,
                    "title": self._normalize_text(normalized_row.get("title")),
                    "brand": self._normalize_text(normalized_row.get("brand")),
                    "categories": self._normalize_text(normalized_row.get("categories")),
                    "labels": self._normalize_text(normalized_row.get("labels")),
                    "image_url": self._normalize_text(normalized_row.get("image")),
                    "my_price": self._coerce_float(normalized_row.get("my price")),
                    "my_regular_price": self._coerce_float(normalized_row.get("my regular price")),
                    "my_special_price": self._coerce_float(normalized_row.get("my special price")),
                    "my_price_stock": self._coerce_int(normalized_row.get("my price stock")),
                    "my_price_retrieved_at": self._coerce_datetime(
                        normalized_row.get("my price retrieved at")
                    ),
                    "price_difference": self._normalize_text(normalized_row.get("price difference")),
                    "lowest_price": self._coerce_float(normalized_row.get("lowest price")),
                    "lowest_price_competitor": self._normalize_text(
                        normalized_row.get("lowest price competitor")
                    ),
                    "raw_payload": json.dumps(row, ensure_ascii=False),
                    "created_at": datetime.utcnow(),
                }

                competitor_payload = []
                for competitor, cols in competitors.items():
                    offer = self._coerce_float(row.get(cols["offer"]))
                    regular = self._coerce_float(row.get(cols["regular"]))
                    special = self._coerce_float(row.get(cols["special"]))
                    stock = self._coerce_int(row.get(cols["stock"]))
                    retrieved_at = self._coerce_datetime(row.get(cols["retrieved"]))
                    if offer is None and regular is None and special is None and stock is None and retrieved_at is None:
                        continue
                    competitor_payload.append(
                        {
                            "competitor": competitor,
                            "offer_price": offer,
                            "regular_price": regular,
                            "special_price": special,
                            "stock": stock,
                            "retrieved_at": retrieved_at,
                        }
                    )
                snapshot["_competitors"] = competitor_payload
                snapshot_batch.append(snapshot)

                if len(snapshot_batch) >= batch_size:
                    flush_batch()

            flush_batch()

            log = session.get(PricemindSyncLog, log.id)
            log.status = "SUCCESS"
            log.completed_at = datetime.utcnow()
            log.total_rows = total_rows
            log.matched_count = matched
            log.unmatched_count = unmatched
            log.updated_count = updated
            session.commit()

            self._cleanup_history()

            return log
        except Exception:
            session.rollback()
            log = session.get(PricemindSyncLog, log.id)
            if log:
                log.status = "FAILED"
                log.completed_at = datetime.utcnow()
                log.error_message = traceback.format_exc()
                session.commit()
            return log
