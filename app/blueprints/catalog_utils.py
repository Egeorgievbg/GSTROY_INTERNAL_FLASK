import re

from .catalog_sync import extract_category_levels

from gstroy_constants import PRODUCT_CSV_FIELDS


def normalize_header(name: str) -> str:
    return (
        (name or "")
        .strip()
        .lower()
        .replace("ў??", "")
        .replace("ў??", "")
        .replace("ў??", "")
        .replace('"', "")
        .replace("-", "_")
        .replace("/", "_")
        .replace("  ", " ")
        .replace(" ", "_")
    )


CSV_IMPORT_MAP = {normalize_header(header): attr for attr, header in PRODUCT_CSV_FIELDS}
CSV_IMPORT_MAP.update(
    {
        "ђу‘?ђш‘'ђуђ?_ђ?ђхђс‘?ђшђ?ђсђз": "short_description",
        "ђ?‘?ђ>ђ?ђ?_ђ?ђхђс‘?ђшђ?ђсђз": "long_description",
        "meta_title": "meta_title",
        "meta_description": "meta_description",
        "‘?ђ?ђсђ?ђуђш": "image_url",
        "image_url": "image_url",
        "ђ?ђш‘?ђуђш": "brand",
    }
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
