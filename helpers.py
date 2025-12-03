import re
import unicodedata

from flask import abort, g, request, url_for
from models import Warehouse
from urllib.parse import urljoin, urlparse
 


def parse_bool(value):
    if value is None:
        return False
    value = str(value).strip().lower()
    return value in {"1", "true", "да", "yes", "y", "on", "t", "x"}


def parse_float(value):
    if value in (None, "", " "):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


def require_admin():
    user = getattr(g, "current_user", None)
    if not user or not getattr(user, "is_admin", False):
        abort(403)


def slugify(value: str) -> str:
    value = str(value or "")
    normalized = unicodedata.normalize("NFKD", value)
    cleaned = re.sub(r"[^\w\s-]", "", normalized, flags=re.UNICODE)
    trimmed = cleaned.strip().lower()
    return re.sub(r"[-\s]+", "-", trimmed)


def hierarchical_address(slug: str, parent_address: str | None) -> str:
    if parent_address:
        return f"{parent_address}/{slug}"
    return slug


def unique_slug(session, model, base_slug: str, exclude_id: int | None = None) -> str:
    slug_candidate = base_slug
    counter = 1
    slug_field = getattr(model, "slug")
    id_field = getattr(model, "id")
    while True:
        query = session.query(model).filter(slug_field == slug_candidate)
        if exclude_id is not None:
            query = query.filter(id_field != exclude_id)
        if not query.first():
            break
        counter += 1
        slug_candidate = f"{base_slug}-{counter}"
    return slug_candidate


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def safe_redirect_target(target: str | None):
    if not target:
        return url_for("index")
    host_url = request.host_url
    ref_url = urlparse(host_url)
    test_url = urlparse(urljoin(host_url, target))
    if test_url.scheme in {"http", "https"} and test_url.netloc == ref_url.netloc:
        return target
    return url_for("index")


def user_warehouse(user):
    if not user:
        return None
    session = getattr(g, "db", None)
    warehouse = None
    assigned_id = getattr(user, "assigned_warehouse_id", None)
    if session and assigned_id:
        warehouse = session.get(Warehouse, assigned_id)
        if warehouse:
            return warehouse
    default_id = getattr(user, "default_warehouse_id", None)
    if session and default_id:
        default_wh = session.get(Warehouse, default_id)
        if default_wh:
            return default_wh
    warehouse = getattr(user, "assigned_warehouse", None)
    if warehouse:
        return warehouse
    return getattr(user, "default_warehouse", None)


UNIT_ALIASES: dict[str, set[str]] = {
    "pieces": {
        "گ+‘?",
        "گ+‘?.",
        "گ+‘?گ?گü",
        "گ'گےگ?گT",
        "گ'‘?.",
        "broj",
        "broi",
        "broy",
        "pcs",
        "piece",
        "pieces",
    },
    "packages": {
        "گُگّگَ",
        "گُگّگَ.",
        "گُگّگَگç‘'",
        "گُگّگَگç‘'گٌ",
        "pak",
        "package",
        "pkg",
        "pack",
    },
    "sqm": {
        "گَگ?گ?",
        "گَگ?.گ?",
        "گَگ?. گ?.",
        "گَگ?.گ?گç‘'‘?‘?",
        "گَگ?گّگ?‘?گّ‘'",
        "square",
        "sqm",
        "گ?2",
        "m2",
        "m^2",
        "گ?^2",
    },
    "linear_meter": {
        "گ>گ?",
        "گ>.گ?",
        "گ>. گ?.",
        "گ>.گ?.",
        "گ>گٌگ?گçگçگ?",
        "گ>گٌگ?گçگçگ?گ?گç‘'‘?‘?",
        "lm",
    },
    "kilogram": {
        "گَگ?",
        "گَگ?.",
        "گَگٌگ>گ?گ?‘?گّگ?",
        "گَگٌگ>گ?گ?‘?گّگ?گّ",
        "kg",
        "kilo",
        "kilogram",
    },
    "box": {"گَ‘?‘'گٌ‘?", "گَ‘?‘'.", "گَ‘?‘'گٌگٌ", "box"},
    "set": {
        "گَگ?گ?گُگ>گçگَ‘'",
        "گَگ?گ?گُگ>.",
        "گَگ?گ?گُگ>گçگَ‘'گٌ",
        "set",
    },
    "ton": {"‘'گ?گ?", "‘'گ?گ?گّ", "‘'.", "t", "ton"},
}


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


UNIT_ALIAS_LOOKUP = {}
for canonical_name, aliases in UNIT_ALIASES.items():
    for alias in aliases:
        token = _unit_token(alias)
        if token:
            UNIT_ALIAS_LOOKUP[token] = canonical_name


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
        return product.main_unit or "گ+‘?."
    if is_piece_unit(product.secondary_unit):
        return product.secondary_unit or "گ+‘?."
    return "گ+‘?."


def default_unit_mode(unit_label: str | None) -> str:
    canonical = canonical_unit_name(unit_label)
    if canonical == "pieces":
        return "pieces"
    if canonical == "packages":
        return "packages"
    return "manual"
