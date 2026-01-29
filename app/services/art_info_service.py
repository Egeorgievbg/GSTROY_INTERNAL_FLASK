import json
from datetime import datetime

import requests
from flask import current_app, g

from models import ArtInfoCache


REQUEST_SESSION = requests.Session()


class ArtInfoService:
    def __init__(self, session=None):
        self.session = session or getattr(g, "db", None)
        if self.session is None:
            raise RuntimeError("Database session is required for art info")

    @staticmethod
    def normalize_art_id(value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            return text
        try:
            return str(int(float(text)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_float(value):
        if value in (None, "", " "):
            return 0.0
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    def _fetch_art_info(self, art_id, sklad_code=None):
        url = current_app.config.get("ARTINFO_API_URL")
        if not url:
            raise RuntimeError("ARTINFO_API_URL is not configured")
        timeout = current_app.config.get("ARTINFO_API_TIMEOUT", 15)
        params = {"id_art": art_id}
        if sklad_code:
            params["sklad_code"] = sklad_code
        try:
            response = REQUEST_SESSION.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"ArtInfo API request failed: {exc}") from exc
        if response.status_code != 200:
            snippet = (response.text or "").strip()[:200]
            raise RuntimeError(f"ArtInfo API returned {response.status_code}: {snippet}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("ArtInfo API returned invalid JSON") from exc
        return payload

    def _extract_rows(self, payload):
        if isinstance(payload, dict):
            if "info" in payload:
                rows = payload.get("info") or []
            elif "data" in payload:
                rows = payload.get("data") or []
            else:
                rows = [payload]
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []

        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if "info" in row:
                nested = row.get("info") or []
                if isinstance(nested, list):
                    normalized.extend([item for item in nested if isinstance(item, dict)])
                    continue
                if isinstance(nested, dict):
                    normalized.append(nested)
                    continue
            if "data" in row:
                nested = row.get("data") or []
                if isinstance(nested, list):
                    normalized.extend([item for item in nested if isinstance(item, dict)])
                    continue
                if isinstance(nested, dict):
                    normalized.append(nested)
                    continue
            normalized.append(row)
        return normalized

    def _resolve_price(self, row):
        promo = self._to_float(row.get("promo_cena_me1_sdds"))
        price_field = (current_app.config.get("ARTINFO_PRICE_FIELD") or "cena1_me1").strip()
        price_candidates = [
            price_field,
            "cena1_me1",
            "cena2_me1",
            "cena3_me1",
            "cena4_me1",
        ]
        base_price = 0.0
        for field in price_candidates:
            base_price = self._to_float(row.get(field))
            if base_price:
                break
        current_price = promo if promo > 0 else base_price
        return current_price, base_price

    def get_art_info(self, art_id, sklad_code=None):
        art_id = self.normalize_art_id(art_id)
        if not art_id:
            raise RuntimeError("Missing id_art")
        cache_seconds = int(current_app.config.get("ARTINFO_CACHE_SECONDS", 300))
        now = datetime.utcnow()

        cache = (
            self.session.query(ArtInfoCache)
            .filter_by(art_id=art_id, sklad_code=sklad_code)
            .first()
        )
        if cache and cache.payload and (now - cache.fetched_at).total_seconds() < cache_seconds:
            return json.loads(cache.payload)

        payload = self._fetch_art_info(art_id, sklad_code=sklad_code)
        payload_json = json.dumps(payload, ensure_ascii=True)
        if cache:
            cache.payload = payload_json
            cache.fetched_at = now
        else:
            cache = ArtInfoCache(
                art_id=art_id,
                sklad_code=sklad_code,
                payload=payload_json,
                fetched_at=now,
            )
            self.session.add(cache)
        self.session.commit()
        return payload

    def build_view(self, payload):
        rows = self._extract_rows(payload)
        stocks = []
        total_physical = 0.0
        total_free = 0.0
        total_reserved = 0.0
        total_incoming = 0.0
        total_scrap = 0.0
        price_rows = []
        pricing = None

        for row in rows:
            free_qty = self._to_float(row.get("kol_free_m1"))
            total_qty = self._to_float(row.get("kol_total_me1")) or free_qty
            reserved_qty = max(total_qty - free_qty, 0.0)
            incoming = self._to_float(row.get("kol_por_me1")) + self._to_float(row.get("kol_neprieto_me1"))
            scrap = self._to_float(row.get("kol_nd_m1"))
            current_price, base_price = self._resolve_price(row)
            currency = row.get("currency") or "BGN"

            stocks.append(
                {
                    "name": row.get("sklad") or f"Sklad {row.get('sklad_code') or ''}".strip(),
                    "physical": total_qty,
                    "reserved": reserved_qty,
                    "free": free_qty,
                    "active": total_qty > 0 or free_qty > 0,
                    "price": current_price,
                    "currency": currency,
                }
            )
            total_physical += total_qty
            total_free += free_qty
            total_reserved += reserved_qty
            total_incoming += incoming
            total_scrap += scrap

            price_rows.append(
                {
                    "warehouse": row.get("sklad") or f"Sklad {row.get('sklad_code') or ''}".strip(),
                    "price": current_price,
                    "date": row.get("part") or "",
                    "currency": currency,
                }
            )

            if pricing is None and (current_price or base_price):
                pricing = {
                    "current": current_price,
                    "original": base_price or current_price,
                    "currency": currency,
                    "has_promo": current_price and base_price and current_price < base_price,
                }

        kpi = {
            "physical": total_physical,
            "reserved": total_reserved,
            "free": total_free,
            "incoming": total_incoming,
            "scrap": total_scrap,
        }
        return {
            "stocks": stocks,
            "kpi": kpi,
            "pricing": pricing,
            "price_rows": price_rows,
        }
