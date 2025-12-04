import os
import urllib.request
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from sqlalchemy.orm import joinedload

from models import (
    StockOrder,
    StockOrderItem,
    StockOrderAssignment,
)

FONT_CANDIDATES = [
    Path(__file__).resolve().parent / "fonts" / "DejaVuSans.ttf",
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]
PDF_FONT_NAME = "Helvetica"
for idx, candidate in enumerate(FONT_CANDIDATES):
    if candidate.exists():
        font_name = f"GSTROYFont{idx}"
        pdfmetrics.registerFont(TTFont(font_name, str(candidate)))
        PDF_FONT_NAME = font_name
        break
else:
    try:
        pdfmetrics.registerFont(TTFont("GSTROYFont", "DejaVuSans.ttf"))
        PDF_FONT_NAME = "GSTROYFont"
    except Exception:  # pragma: no cover
        PDF_FONT_NAME = "Helvetica"

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
PPP_STATIC_DIR = STATIC_DIR / "ppp"
IMAGES_DIR = STATIC_DIR / "images"
PLACEHOLDER_IMAGE_URL = "https://internal.gstroy.bg/static/assets/images/StroiMarket_no_image.png"
PLACEHOLDER_IMAGE_PATH = IMAGES_DIR / "no_image.png"
DEFAULT_PRODUCT_IMAGE = "images/no_image.png"
if not PLACEHOLDER_IMAGE_PATH.exists():
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(PLACEHOLDER_IMAGE_URL, PLACEHOLDER_IMAGE_PATH)
    except Exception:
        PLACEHOLDER_IMAGE_PATH.write_bytes(b"")

ALLOWED_CSV_MIME_TYPES = {"text/csv", "application/vnd.ms-excel", "application/csv"}
PRODUCT_CSV_FIELDS = [
    ("item_number", "Номенклатурен номер"),
    ("name", "Име"),
    ("brand", "Марка"),
    ("manufacturer_name", "Производител"),
    ("primary_group", "Основна група"),
    ("secondary_group", "Втора група"),
    ("tertiary_group", "Трета група"),
    ("quaternary_group", "Четвърта група"),
    ("category", "Категория"),
    ("group", "Група"),
    ("subgroup", "Подгрупа"),
    ("fb_category", "fb_category"),
    ("google_category", "google_category"),
    ("fb_ads_tag", "fb_ads"),
    ("versus_id", "Versus ID"),
    ("catalog_number", "Каталожен номер"),
    ("main_unit", "Мерна единица 1"),
    ("secondary_unit", "Мерна единица 2"),
    ("unit_conversion_ratio", "Коефициент единици"),
    ("price_unit_1", "Цена единица 1"),
    ("price_unit_2", "Цена единица 2"),
    ("promo_price_unit_1", "Промо цена единица 1"),
    ("promo_price_unit_2", "Промо цена единица 2"),
    ("visible_price_unit_1", "Видима цена единица 1"),
    ("visible_price_unit_2", "Видима цена единица 2"),
    ("show_add_to_cart_button", "Покажи бутон „Купи“"),
    ("show_request_button", "Покажи бутон „Поръчай“"),
    ("allow_two_unit_sales", "Позволи две мерни единици"),
    ("in_brochure", "В брошура"),
    ("is_most_viewed", "Най-гледани"),
    ("is_active", "Активен"),
    ("is_oversized", "Голям"),
    ("is_special_offer", "Специална оферта"),
    ("show_in_special_carousel", "Покажи в карусел"),
    ("landing_page_accent", "Оцветяване на лендинг"),
    ("check_availability_in_versus", "Проверка на наличности (Versus)"),
    ("variation_parent_sku", "Родител SKU"),
    ("variation_color_code", "Цвят - код"),
    ("variation_color_name", "Цвят - име"),
    ("option2_name", "Опция 2 - име"),
    ("option2_value", "Опция 2 - стойност"),
    ("option2_keyword", "Опция 2 - ключова дума"),
    ("weight_unit_1", "Тегло (кг)"),
    ("weight_kg", "Тегло (кг)"),
    ("width_cm", "Ширина (см)"),
    ("height_cm", "Височина (см)"),
    ("depth_cm", "Дълбочина (см)"),
    ("storage_location", "Складово място"),
    ("barcode", "EAN"),
    ("image_url", "URL изображение"),
    ("short_description", "Кратко описание"),
    ("long_description", "Дълго описание"),
    ("meta_title", "Meta Title"),
    ("meta_description", "Meta Description"),
]
BOOLEAN_FIELDS = {
    "show_add_to_cart_button",
    "show_request_button",
    "allow_two_unit_sales",
    "in_brochure",
    "is_most_viewed",
    "is_active",
    "is_oversized",
    "is_special_offer",
    "show_in_special_carousel",
    "landing_page_accent",
    "check_availability_in_versus",
}
FLOAT_FIELDS = {
    "unit_conversion_ratio",
    "price_unit_1",
    "price_unit_2",
    "promo_price_unit_1",
    "promo_price_unit_2",
    "visible_price_unit_1",
    "visible_price_unit_2",
    "weight_unit_1",
    "weight_kg",
    "width_cm",
    "height_cm",
    "depth_cm",
}

STOCK_ORDER_STATUSES = [
    "new",
    "assigned",
    "in_progress",
    "ready_for_handover",
    "partially_delivered",
    "delivered",
]
STOCK_ORDER_TYPES = ["A", "B", "C"]
STOCK_ORDER_AUTOMATION_STATUSES = ["assigned", "in_progress"]

# Поправени преводи тук
STOCK_ORDER_STATUS_LABELS = {
    "new": "Нова",
    "assigned": "Възложена",
    "in_progress": "В процес",
    "ready_for_handover": "Готова за предаване",
    "partially_delivered": "Частично доставена",
    "delivered": "Доставена",
}

# Поправени преводи тук
STOCK_ORDER_TYPE_LABELS = {
    "A": "Вид A",
    "B": "Вид B",
    "C": "Вид C"
}

STOCK_ORDER_EAGER_OPTIONS = [
    joinedload(StockOrder.items).joinedload(StockOrderItem.product),
    joinedload(StockOrder.items).joinedload(StockOrderItem.service_point),
    joinedload(StockOrder.assignments).joinedload(StockOrderAssignment.user),
    joinedload(StockOrder.assignments).joinedload(StockOrderAssignment.service_point),
    joinedload(StockOrder.ppp_documents),
    joinedload(StockOrder.warehouse),
]
STATUS_BADGE_CLASSES = {
    "new": "status-new",
    "assigned": "status-assigned",
    "in_progress": "status-progress",
    "ready_for_handover": "status-ready",
    "partially_delivered": "status-partial",
    "delivered": "status-delivered",
}

UNIT_ALIASES: dict[str, set[str]] = {
    "pieces": {
        "брой",
        "брой.",
        "бройове",
        "pcs",
        "piece",
        "pieces",
        "бр.",
        "broj",
        "broi",
        "broy",
    },
    "packages": {
        "опаковка",
        "pack",
        "pkg",
        "package",
        "package.",
        "пакет",
    },
    "sqm": {
        "sqm",
        "m2",
        "m^2",
        "m²",
        "кв.м.",
        "кв.м",
        "square",
    },
    "linear_meter": {"lm", "linear_meter", "l.m.", "м/м"},
    "kilogram": {"kg", "kilo", "kilogram", "кг"},
    "box": {"box"},
    "set": {"set"},
    "ton": {"t", "ton"},
}
