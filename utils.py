import base64
import re
import secrets
from datetime import datetime
from io import BytesIO
from pathlib import Path

from app.blueprints.catalog_sync import BrandRegistry, CategoryRegistry, extract_category_levels
from flask import current_app, g
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from constants import (
    BOOLEAN_FIELDS,
    FLOAT_FIELDS,
    PDF_FONT_NAME,
    PRODUCT_CSV_FIELDS,
    PPP_STATIC_DIR,
    UNIT_ALIASES,
)
from models import ProductList, StockOrder, TransferDocument, Warehouse


def normalize_header(name: str) -> str:
    return (
        (name or "")
        .strip()
        .lower()
        .replace("¢??", "")
        .replace("¢??", "")
        .replace("¢??", "")
        .replace('"', "")
        .replace("-", "_")
        .replace("/", "_")
        .replace("  ", " ")
        .replace(" ", "_")
    )


def ensure_catalog_fields(payload, brand_registry, category_registry):
    brand = brand_registry.ensure(payload.get("brand"))
    if brand:
        payload["brand_id"] = brand.id
        payload["brand"] = brand.name
    levels = extract_category_levels(payload)
    category = category_registry.ensure_for_levels(levels)
    if category:
        payload["category_id"] = category.id
        if not payload.get("category"):
            payload["category"] = category.full_address


def parse_bool(value):
    if value is None:
        return False
    value = str(value).strip().lower()
    return value in {"1", "true", "ђ?ђш", "yes", "y", "on", "ђ?", "ў?", "x"}


def parse_float(value):
    if value in (None, "", " "):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _generate_unique_code(session, model, column, prefix):
    for _ in range(10):
        suffix = secrets.token_hex(3).upper()
        candidate = f"{prefix}-{datetime.utcnow():%Y%m%d}-{suffix}"
        exists = session.query(model).filter(column == candidate).first()
        if not exists:
            return candidate
    raise RuntimeError(f"Unable to generate unique code for {prefix}")


def generate_list_code(session):
    return _generate_unique_code(session, ProductList, ProductList.code, "LST")


def generate_pallet_code(session):
    return _generate_unique_code(session, ProductList, ProductList.pallet_code, "PLT")


def generate_transfer_code(session):
    return _generate_unique_code(session, TransferDocument, TransferDocument.code, "TRF")


def ensure_ppp_dir():
    PPP_STATIC_DIR.mkdir(parents=True, exist_ok=True)


def generate_ppp_pdf(order: StockOrder, signature_rel_path: str | None = None):
    ensure_ppp_dir()
    pdf_filename = f"ppp_order_{order.id}.pdf"
    pdf_path = PPP_STATIC_DIR / pdf_filename
    pdf = canvas.Canvas(str(pdf_path), pagesize=A4)
    width, height = A4
    pdf.setFont(PDF_FONT_NAME, 16)
    pdf.drawString(30, height - 40, "ППП документ")
    pdf.setFont(PDF_FONT_NAME, 11)
    lines = [
        f"Идентификатор: {order.external_id or order.id}",
        f"Клиент: {order.client_name or '-'}",
        f"Телефон: {order.client_phone or '-'}",
        f"Адрес: {order.delivery_address or order.client_address or '-'}",
        f"Статус: {order.status or '-'}",
    ]
    y = height - 70
    for line in lines:
        pdf.drawString(30, y, line)
        y -= 22
    pdf.setFont(PDF_FONT_NAME, 10)
    for item in order.items:
        product_name = item.product.name if item.product else "Unknown"
        text = (
            f"- {product_name} | Количка: {item.quantity_ordered} {item.unit or ''} | "
            f"Подготвено: {item.quantity_prepared} | Доставено: {item.quantity_delivered}"
        )
        if y < 100:
            pdf.showPage()
            y = height - 60
            pdf.setFont(PDF_FONT_NAME, 10)
        pdf.drawString(36, y, text)
        y -= 14
    pdf.setFont(PDF_FONT_NAME, 11)
    signature_y = 100
    pdf.drawString(30, signature_y + 40, "Подпис:")
    signature_abs = None
    if signature_rel_path:
        signature_abs = PPP_STATIC_DIR / Path(signature_rel_path).name
        if not signature_abs.exists():
            signature_abs = None
    if signature_abs:
        pdf.drawImage(
            str(signature_abs),
            30,
            signature_y + 5,
            width=150,
            height=60,
            preserveAspectRatio=True,
            mask="auto",
        )
    else:
        pdf.line(30, signature_y + 5, 220, signature_y + 5)
    pdf.save()
    return f"ppp/{pdf_filename}"


def save_signature_image(order_id: int, data_url: str):
    if not data_url:
        raise ValueError("Missing signature data")
    header, _, encoded = data_url.partition(",")
    binary = base64.b64decode(encoded or data_url, validate=True)
    max_bytes = current_app.config["SIGNATURE_MAX_BYTES"]
    if len(binary) > max_bytes:
        raise ValueError("Signature too large")
    if not header.lower().startswith("data:image/png"):
        raise ValueError("Signature must be a PNG")
    ensure_ppp_dir()
    filename = f"signature_{order_id}_{secrets.token_hex(4)}.png"
    path = PPP_STATIC_DIR / filename
    with open(path, "wb") as handle:
        handle.write(binary)
    return f"ppp/{filename}"


def _unit_token(value: str | None) -> str | None:
    if not value:
        return None
    normalized = (
        value.replace("‚?", "2")
        .replace("¢??", "")
        .replace("¢??", "")
        .replace('"', "")
    )
    return re.sub(r"[\s\.\-/_]", "", normalized or "").lower()


def canonical_unit_name(value: str | None) -> str | None:
    token = _unit_token(value)
    if not token:
        return None
    return UNIT_ALIAS_LOOKUP.get(token)


def is_piece_unit(value: str | None) -> bool:
    return canonical_unit_name(value) == "pieces"


def is_package_unit(value: str | None) -> bool:
    return canonical_unit_name(value) == "packages"


def supports_package_to_piece(product) -> bool:
    return (
        is_package_unit(product.main_unit)
        and is_piece_unit(product.secondary_unit)
        and bool(product.unit_conversion_ratio)
    )


def piece_unit_label(product) -> str:
    if is_piece_unit(product.main_unit):
        return product.main_unit or "брой"
    if is_piece_unit(product.secondary_unit):
        return product.secondary_unit or "брой"
    return "брой"


def default_unit_mode(unit_label: str | None) -> str:
    canonical = canonical_unit_name(unit_label)
    if canonical == "pieces":
        return "pieces"
    if canonical == "packages":
        return "packages"
    return "manual"


def load_warehouses(session):
    return session.query(Warehouse).order_by(Warehouse.name).all()


def user_with_default_warehouse(user):
    if not user or not getattr(user, "default_warehouse_id", None):
        return None
    return user


def default_warehouse_for_user(user):
    if not user:
        return None
    default_id = getattr(user, "default_warehouse_id", None)
    if not default_id:
        return None
    session = getattr(g, "db", None)
    if session:
        warehouse = session.get(Warehouse, default_id)
        if warehouse:
            return warehouse
    return getattr(user, "default_warehouse", None)


def calculate_list_totals(product_list):
    total_quantity = 0.0
    total_weight = 0.0
    total_volume = 0.0
    total_pieces = 0.0
    total_packages = 0.0
    for item in product_list.items:
        qty = float(item.quantity or 0)
        total_quantity += qty
        product = item.product
        canonical_unit = canonical_unit_name(item.unit)
        if canonical_unit == "pieces":
            total_pieces += qty
        elif canonical_unit == "package":
            total_packages += qty
        if product:
            if product.weight_kg:
                total_weight += product.weight_kg * qty
            if product.width_cm and product.height_cm and product.depth_cm:
                volume_per_unit = (product.width_cm / 100) * (product.height_cm / 100) * (
                    product.depth_cm / 100
                )
                total_volume += volume_per_unit * qty
    return {
        "line_count": len(product_list.items),
        "total_quantity": total_quantity,
        "total_weight": total_weight,
        "total_volume": total_volume,
        "total_pieces": total_pieces,
        "total_packages": total_packages,
    }


def parse_scan_task_lines(raw_text: str):
    pattern = re.compile(r"[;\\s]+")
    entries = []
    for line in (raw_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = [p for p in re.split(pattern, stripped) if p]
        if not parts:
            continue
        barcode = parts[0]
        try:
            qty = float(parts[1]) if len(parts) > 1 else 1.0
        except (ValueError, TypeError):
            qty = 1.0
        entries.append((barcode, qty))
    return entries


UNIT_ALIAS_LOOKUP = {}
for canonical_name, aliases in UNIT_ALIASES.items():
    for alias in aliases:
        token = _unit_token(alias)
        if token:
            UNIT_ALIAS_LOOKUP[token] = canonical_name

CSV_IMPORT_MAP = {normalize_header(header): attr for attr, header in PRODUCT_CSV_FIELDS}
CSV_IMPORT_MAP.update(
    {
        "short_description": "short_description",
        "long_description": "long_description",
        "meta_title": "meta_title",
        "meta_description": "meta_description",
        "image_url": "image_url",
        "brand": "brand",
    }
)
