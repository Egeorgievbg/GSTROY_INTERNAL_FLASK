import base64
import json
import mimetypes
from datetime import datetime
from pathlib import Path

import requests
from requests.exceptions import ReadTimeout, RequestException
import io
try:
    from PIL import Image
except Exception:
    Image = None

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - handled at runtime if missing
    fitz = None
try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - handled at runtime if missing
    PdfReader = None

from flask import current_app
from sqlalchemy import func

from models import MasterProduct, Product


def _normalize_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _coerce_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _coerce_date(value):
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


class InvoiceOcrService:
    def __init__(self, api_key=None, model=None, timeout=None, max_pages=None):
        self.api_key = api_key or current_app.config.get("OPENAI_API_KEY")
        self.model = model or current_app.config.get("INVOICE_OCR_MODEL", "gpt-4o")
        # Default timeout increased to handle large uploads and model processing
        self.timeout = timeout or current_app.config.get("INVOICE_OCR_TIMEOUT", 300)
        # ВАЖНО: Евромастер пращат по 8+ страници. Вдигаме лимита на 15.
        self.max_pages = max_pages or current_app.config.get("INVOICE_OCR_MAX_PAGES", 15)
        # How many pages to send per request when invoice is multi-page
        self.chunk_pages = int(current_app.config.get("INVOICE_OCR_CHUNK_PAGES", 1))

    def extract_invoice_data(self, file_path: str | Path, progress_callback=None):
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        image_payloads, text_payload, source_path = self._build_image_payloads(file_path)

        if not image_payloads and not text_payload:
            raise RuntimeError("No usable content extracted from the invoice")

        # Schema remains the same...
        schema = {
            "invoice_header": {
                "invoice_number": "string",
                "issue_date": "YYYY-MM-DD",
                "currency": "string",
            },
            "vendor": {
                "name": "string",
                "vat_id": "string",
            },
            "line_items": [
                {
                    "article_no": "string (Critical: Vendor SKU)",
                    "description": "string",
                    "quantity": "number",
                    "unit": "string",
                    "unit_price": "number",
                    "total_row": "number",
                }
            ],
            "totals": {
                "total_due": "number",
            },
        }

        # --- системни инструкции ---
        system_prompt = """
        You are an expert Data Extraction AI for Bulgarian invoices.
        Extract data strictly into JSON.

        ### HEADER & VENDOR DETECTION RULES:
        1. **Distinguish Vendor vs Receiver**:
           - The **VENDOR (Доставчик)** is usually located on the **RIGHT** side or labeled explicitly "Доставчик".
           - The **RECEIVER (Получател)** is usually on the **LEFT**.
           - **VESTAL SPECIFIC**: Look for "Вестал - 2002" on the RIGHT. Use its VAT ID (BG175220680). Do NOT use the Receiver's VAT/EIK.

        ### CRITICAL TABLE EXTRACTION RULES:
        1. **COLUMN ALIGNMENT IS KING**: 
           - You must strictly align values with their vertical column headers: "Мярка", "Количество", "Ед.цена", "Стойност".
           - **IGNORE numbers inside the Description text!**
           - **Example Trap**: Description "Разклонител бял 6 с 1,5м кабел". 
             - "6" is NOT quantity. "1.5" is NOT price. These are product specs.
             - Look further to the RIGHT for the actual columns (e.g., Qty: 12, Price: 13.31).

        2. **TABLE BOUNDARIES**: 
           - You must STOP extracting line items when you reach the total section lines.
           - Stop keywords: "ВСИЧКО", "Данъчна основа", "Начислен ДДС", "Сума за плащане", "СЛОВОМ".
           - The numbers appearing after these keywords (like 319.44, 63.89) are TOTALS, not products.

        ### VENDOR-SPECIFIC RULES:

        1. **VESTAL-2002 (ВЕСТАЛ)** - STRICT HANDLING:
           - **Row Number Separation**: The text lines start with: "[Row Index] [Article Code] [Description]".
           - **The Glue Problem**: If the OCR sees a merged number at the start (e.g., "1461212" on row 1, "2461223" on row 2), you MUST split it.
             - Rule: The FIRST digit is the row index. The REST is the Article Code.
             - Input: "1461212..." -> Article No: "461212" (Remove leading '1').
             - Input: "2461223..." -> Article No: "461223" (Remove leading '2').
           - **Description vs Values**: As per general rules, DO NOT extract numbers from the description text (like "6" or "1.5m") as quantity/price. Look at the specific numeric columns.

        2. **EUROMASTER (ЕВРОМАСТЕР)**:
           - Column "Артикул №" is 'article_no'.
           - Must preserve leading zeros (e.g. "020136"). 

        3. **KAM-04 & DENICOM**:
           - Use 'Vendor Code' (Код Дост.), ignore 'Client Code' (Код Клиент).
           - Preserve spaces in codes (e.g. "2 608 630").

        ### GENERAL RULES:
        - Dates: YYYY-MM-DD.
        - Quantities: Always extract the base unit count (pcs/бр/брой).
        - If multiple pages, process ALL rows from ALL pages correctly.
        """

        user_content = [
            {
                "type": "text",
                "text": f"Extract data to this JSON schema: {json.dumps(schema)}",
            }
        ]

        if image_payloads:
            user_content += image_payloads
        else:
            user_content.append({"type": "text", "text": f"Invoice text:\n{text_payload}"})

        # Helper to perform a single request with retries
        def _single_request(payload):
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            attempts = int(current_app.config.get("INVOICE_OCR_REQUEST_ATTEMPTS", 3))
            base_backoff = float(current_app.config.get("INVOICE_OCR_BACKOFF", 1.5))
            # use a session for connection reuse
            session = requests.Session()
            last_exc = None
            for attempt in range(1, attempts + 1):
                try:
                    # explicit tuple (connect_timeout, read_timeout)
                    timeout_tuple = (int(current_app.config.get("INVOICE_OCR_CONNECT_TIMEOUT", 10)), int(self.timeout))
                    resp = session.post(
                        "https://api.openai.com/v1/chat/completions",
                        json=payload,
                        headers=headers,
                        timeout=timeout_tuple,
                    )
                    if resp.status_code != 200:
                        snippet = (resp.text or "")[:400]
                        raise RuntimeError(f"OCR request failed ({resp.status_code}): {snippet}")
                    return resp.json()
                except ReadTimeout as exc:
                    last_exc = exc
                    if attempt < attempts:
                        sleep_s = base_backoff ** attempt
                        try:
                            import time

                            time.sleep(sleep_s)
                        except Exception:
                            pass
                        # on next attempt increase read timeout slightly
                        self.timeout = min(int(self.timeout * 1.5), int(current_app.config.get("INVOICE_OCR_MAX_TIMEOUT", 900)))
                        continue
                except RequestException as exc:
                    last_exc = exc
                    if attempt < attempts:
                        sleep_s = base_backoff ** attempt
                        try:
                            import time

                            time.sleep(sleep_s)
                        except Exception:
                            pass
                        continue
                except Exception as exc:
                    last_exc = exc
                    # non-retriable error, raise immediately
                    raise
            # exhausted attempts
            raise last_exc

        # If we have image payloads, process them sequentially (one request per page)
        if image_payloads:
            all_items = []
            header = None
            vendor = None
            totals = {}
            usage_acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            total_images = len(image_payloads)
            for idx, img_payload in enumerate(image_payloads):
                # per-page user content: schema + single page
                page_user_content = [
                    {"type": "text", "text": f"Extract data to this JSON schema: {json.dumps(schema)}"},
                    img_payload,
                ]
                page_payload = {
                    "model": self.model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(page_user_content, ensure_ascii=False)},
                    ],
                }
                try:
                    data = _single_request(page_payload)
                except Exception as exc:
                    # on ReadTimeout or large payload issues, try to re-render this page with lower resolution
                    if source_path and isinstance(exc, (ReadTimeout, RequestException)):
                        current_app.logger.warning(
                            "OCR page %s timed out on first attempt: %s", idx + 1, getattr(exc, "args", exc)
                        )
                        data = None
                        # try progressively smaller zooms
                        for zoom_try in (0.8, 0.6, 0.5):
                            try:
                                current_app.logger.info("Re-rendering page %s at zoom %s", idx + 1, zoom_try)
                                downsampled = self._pdf_to_images_for_pages(source_path, [idx], zoom=zoom_try)
                                small_user_content = [
                                    {"type": "text", "text": f"Extract data to this JSON schema: {json.dumps(schema)}"}
                                ] + downsampled
                                small_payload = {
                                    "model": self.model,
                                    "temperature": 0,
                                    "response_format": {"type": "json_object"},
                                    "messages": [
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": json.dumps(small_user_content, ensure_ascii=False)},
                                    ],
                                }
                                data = _single_request(small_payload)
                                break
                            except Exception:
                                current_app.logger.exception("Retry with zoom %s failed for page %s", zoom_try, idx + 1)
                                data = None
                                continue
                        # If downsample retries failed, try JPEG compression fallback if Pillow available
                        if not data and Image is not None:
                            try:
                                current_app.logger.info("Trying JPEG compression fallback for page %s", idx + 1)
                                small_imgs = self._pdf_to_images_for_pages(source_path, [idx], zoom=0.5)
                                if small_imgs:
                                    compressed_payloads = []
                                    for p in small_imgs:
                                        data_url = p.get("image_url", {}).get("url")
                                        if data_url and data_url.startswith("data:"):
                                            try:
                                                header, b64 = data_url.split(",", 1)
                                                raw = base64.b64decode(b64)
                                                img = Image.open(io.BytesIO(raw)).convert("RGB")
                                                out = io.BytesIO()
                                                img.save(out, format="JPEG", quality=60, optimize=True)
                                                jpg_bytes = out.getvalue()
                                                compressed_payloads.append(self._image_payload(jpg_bytes, "image/jpeg"))
                                            except Exception:
                                                current_app.logger.exception("JPEG compression failed for page %s", idx + 1)
                                                continue
                                    if compressed_payloads:
                                        small_user_content = [
                                            {"type": "text", "text": f"Extract data to this JSON schema: {json.dumps(schema)}"}
                                        ] + compressed_payloads
                                        small_payload = {
                                            "model": self.model,
                                            "temperature": 0,
                                            "response_format": {"type": "json_object"},
                                            "messages": [
                                                {"role": "system", "content": system_prompt},
                                                {"role": "user", "content": json.dumps(small_user_content, ensure_ascii=False)},
                                            ],
                                        }
                                        data = _single_request(small_payload)
                            except Exception:
                                current_app.logger.exception("JPEG fallback failed for page %s", idx + 1)

                        if not data:
                            current_app.logger.error("All retries failed for page %s", idx + 1)
                            # notify callback about error for this page
                            try:
                                if progress_callback:
                                    progress_callback(idx + 1, {"status": "error", "error": str(exc)})
                            except Exception:
                                current_app.logger.exception("progress_callback raised")
                            raise exc
                    else:
                        raise

                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                if not content:
                    raise RuntimeError("OCR response did not include content for page")
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"OCR returned invalid JSON for page: {exc}") from exc

                # collect header/vendor/totals if present
                if not header and parsed.get("invoice_header"):
                    header = parsed.get("invoice_header")
                if not vendor and parsed.get("vendor"):
                    vendor = parsed.get("vendor")
                if parsed.get("line_items"):
                    # annotate items with source page if helpful
                    for it in parsed.get("line_items"):
                        it.setdefault("_source_page", idx + 1)
                    all_items.extend(parsed.get("line_items"))
                # accumulate totals if present
                if parsed.get("totals"):
                    totals.update({k: parsed.get("totals").get(k) for k in parsed.get("totals")})
                # accumulate usage if available
                u = data.get("usage") or {}
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    try:
                        usage_acc[k] = usage_acc.get(k, 0) + int(u.get(k, 0))
                    except Exception:
                        pass
                # call progress callback with successful page result
                try:
                    if progress_callback:
                        progress_callback(idx + 1, {"status": "ok", "result": parsed, "usage": u})
                except Exception:
                    current_app.logger.exception("progress_callback raised")

            merged = {
                "invoice_header": header or {},
                "vendor": vendor or {},
                "line_items": all_items,
                "totals": totals or {},
            }
            usage_acc = usage_acc or None
            return merged, usage_acc

        # Single request path (small invoices or text)
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
            ],
        }

        data = _single_request(payload)
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not content:
            raise RuntimeError("OCR response did not include content")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OCR returned invalid JSON: {exc}") from exc
        usage = data.get("usage")
        return parsed, usage

    def _build_image_payloads(self, file_path: str | Path):
        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            if fitz is not None:
                # For multi-page PDFs, prefer lower-res JPEGs to reduce payload size
                try:
                    doc = fitz.open(path)
                    page_count = min(len(doc), int(self.max_pages or 0) or len(doc))
                except Exception:
                    doc = None
                    page_count = 0
                jpeg_threshold = int(current_app.config.get("INVOICE_OCR_JPEG_THRESHOLD_PAGES", 4))
                if page_count and page_count > jpeg_threshold:
                    # use smaller zoom and JPEG output for large PDFs
                    zoom = float(current_app.config.get("INVOICE_OCR_LARGE_PDF_ZOOM", 0.7))
                    return self._pdf_to_images(path, zoom=zoom, prefer_jpeg=True), None, path
                return self._pdf_to_images(path), None, path
            text = self._pdf_to_text(path)
            if text:
                return [], text, None
            raise RuntimeError("PDF processing requires PyMuPDF")
        mime = mimetypes.types_map.get(suffix, "image/jpeg")
        return [self._image_payload(path.read_bytes(), mime)], None, None

    def _pdf_to_images(self, path: Path, zoom: float = 1.0, prefer_jpeg: bool = False, jpeg_quality: int = 75):
        if fitz is None:
            raise RuntimeError("PyMuPDF is required")
        images = []
        doc = fitz.open(path)
        page_count = min(len(doc), int(self.max_pages or 0) or len(doc))
        matrix = fitz.Matrix(zoom, zoom)
        for idx in range(page_count):
            page = doc.load_page(idx)
            pix = page.get_pixmap(matrix=matrix)
            try:
                if prefer_jpeg:
                    # try direct JPEG bytes from PyMuPDF
                    jpg = pix.tobytes("jpg")
                    images.append(self._image_payload(jpg, "image/jpeg"))
                else:
                    images.append(self._image_payload(pix.tobytes("png"), "image/png"))
            except Exception:
                # fallback to PNG
                images.append(self._image_payload(pix.tobytes("png"), "image/png"))
        return images

    def _pdf_to_images_for_pages(self, path: Path, page_indices: list[int], zoom: float = 1.0, prefer_jpeg: bool = False):
        """Render a specific set of page indices (0-based) with given zoom and return image payloads."""
        if fitz is None:
            raise RuntimeError("PyMuPDF is required")
        images = []
        doc = fitz.open(path)
        matrix = fitz.Matrix(zoom, zoom)
        for idx in page_indices:
            if idx < 0 or idx >= len(doc):
                continue
            page = doc.load_page(idx)
            pix = page.get_pixmap(matrix=matrix)
            try:
                if prefer_jpeg:
                    jpg = pix.tobytes("jpg")
                    images.append(self._image_payload(jpg, "image/jpeg"))
                else:
                    images.append(self._image_payload(pix.tobytes("png"), "image/png"))
            except Exception:
                images.append(self._image_payload(pix.tobytes("png"), "image/png"))
        return images

    def _pdf_to_text(self, path: Path):
        if PdfReader is None:
            return None
        try:
            reader = PdfReader(str(path))
        except Exception:
            return None
        chunks = []
        for page in reader.pages[: self.max_pages]:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(chunks).strip()
        return text if text else None

    @staticmethod
    def _image_payload(raw_bytes: bytes, mime: str):
        encoded = base64.b64encode(raw_bytes).decode("utf-8")
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64,{encoded}",
                "detail": "high",
            },
        }


def build_match_lookup(session, vendor_codes):
    codes = []
    for code in vendor_codes:
        normalized = _normalize_text(code)
        if normalized:
            codes.append(normalized)
    lower_codes = {code.lower() for code in codes}
    if not lower_codes:
        return {
            "catalog": {},
            "item": {},
            "barcode": {},
            "master": {},
            "versus": {},
        }

    catalog_products = (
        session.query(Product)
        .filter(Product.catalog_number.isnot(None))
        .filter(func.lower(Product.catalog_number).in_(lower_codes))
        .all()
    )
    item_products = (
        session.query(Product)
        .filter(Product.item_number.isnot(None))
        .filter(func.lower(Product.item_number).in_(lower_codes))
        .all()
    )
    barcode_products = (
        session.query(Product)
        .filter(Product.barcode.isnot(None))
        .filter(func.lower(Product.barcode).in_(lower_codes))
        .all()
    )
    master_rows = (
        session.query(MasterProduct)
        .filter(MasterProduct.vendor_code.isnot(None))
        .filter(func.lower(MasterProduct.vendor_code).in_(lower_codes))
        .all()
    )
    internal_ids = [str(row.internal_id) for row in master_rows if row.internal_id is not None]
    versus_products = (
        session.query(Product)
        .filter(Product.versus_id.in_(internal_ids))
        .all()
        if internal_ids
        else []
    )

    return {
        "catalog": {p.catalog_number.lower(): p for p in catalog_products if p.catalog_number},
        "item": {p.item_number.lower(): p for p in item_products if p.item_number},
        "barcode": {p.barcode.lower(): p for p in barcode_products if p.barcode},
        "master": {m.vendor_code.lower(): m for m in master_rows if m.vendor_code},
        "versus": {p.versus_id: p for p in versus_products if p.versus_id},
    }


def match_vendor_code(vendor_code, lookup):
    normalized = _normalize_text(vendor_code)
    if not normalized:
        return None, None
    key = normalized.lower()
    product = lookup["catalog"].get(key)
    if product:
        return product, "catalog_number"
    product = lookup["item"].get(key)
    if product:
        return product, "item_number"
    product = lookup["barcode"].get(key)
    if product:
        return product, "barcode"
    master = lookup["master"].get(key)
    if master and master.internal_id is not None:
        product = lookup["versus"].get(str(master.internal_id))
        if product:
            return product, "master_vendor_code"
    return None, None


def normalize_invoice_payload(payload):
    header = payload.get("invoice_header") or {}
    vendor = payload.get("vendor") or {}
    receiver = payload.get("receiver") or {}
    totals = payload.get("totals") or {}
    line_items = payload.get("line_items") or payload.get("items") or []

    return {
        "header": {
            "invoice_number": _normalize_text(header.get("invoice_number")),
            "issue_date": _coerce_date(header.get("issue_date")),
            "currency": _normalize_text(header.get("currency")),
        },
        "vendor": {
            "name": _normalize_text(vendor.get("name")),
            "vat_id": _normalize_text(vendor.get("vat_id")),
            "iban": _normalize_text(vendor.get("iban")),
        },
        "receiver": {
            "name": _normalize_text(receiver.get("name")),
            "vat_id": _normalize_text(receiver.get("vat_id")),
        },
        "totals": {
            "net_amount": _coerce_float(totals.get("net_amount")),
            "vat_amount": _coerce_float(totals.get("vat_amount")),
            "total_due": _coerce_float(totals.get("total_due")),
        },
        "line_items": [
            {
                "article_no": _normalize_text(item.get("article_no")),
                "description": _normalize_text(item.get("description")),
                "quantity": _coerce_float(item.get("quantity")),
                "unit": _normalize_text(item.get("unit")),
                "unit_price": _coerce_float(item.get("unit_price")),
                "total_row": _coerce_float(item.get("total_row")),
            }
            for item in (line_items or [])
        ],
    }
