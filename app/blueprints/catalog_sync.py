from __future__ import annotations

import re

from sqlalchemy import exc, func, or_

from helpers import hierarchical_address, normalize_name, slugify, unique_slug
from models import Brand, Category, Product


def _cleanup_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text in {"—", "-"}:
        return None
    return text


def _delimiter_split(value: str | None) -> list[str]:
    cleaned = _cleanup_text(value)
    if not cleaned:
        return []
    parts = [part.strip() for part in re.split(r"[,/]", cleaned)]
    return [part for part in parts if part]


def extract_category_levels(payload: dict) -> list[str]:
    """
    Normalize category/group fields and produce a deterministic path.
    """
    levels: list[str] = []

    primary = _cleanup_text(payload.get("primary_group"))
    if primary:
        levels.extend(_delimiter_split(primary))
    else:
        levels.extend(_delimiter_split(payload.get("category")))

    secondary = _cleanup_text(payload.get("secondary_group") or payload.get("group"))
    levels.extend(_delimiter_split(secondary))

    tertiary = _cleanup_text(payload.get("tertiary_group") or payload.get("subgroup"))
    levels.extend(_delimiter_split(tertiary))

    quaternary = _cleanup_text(payload.get("quaternary_group"))
    levels.extend(_delimiter_split(quaternary))

    if not levels:
        levels.append("Други")
    return levels


class BrandRegistry:
    def __init__(self, session):
        self.session = session
        self.cache: dict[str, Brand] = {}
        self._populate()

    def _populate(self):
        for brand in self.session.query(Brand).all():
            norm = normalize_name(brand.name)
            if norm:
                self.cache.setdefault(norm, brand)

    def ensure(self, raw_name: str | None) -> Brand | None:
        cleaned = _cleanup_text(raw_name)
        if not cleaned:
            return None
        norm = normalize_name(cleaned)
        if not norm:
            return None
        cached = self.cache.get(norm)
        if cached:
            return cached

        slug_base = slugify(cleaned) or "brand"
        slug_value = unique_slug(self.session, Brand, slug_base)
        brand = Brand(name=cleaned, slug=slug_value)
        self.session.add(brand)
        try:
            self.session.flush()
        except exc.IntegrityError:
            self.session.rollback()
            brand = (
                self.session.query(Brand)
                .filter(func.lower(Brand.name) == norm)
                .first()
            )
            if not brand:
                raise
        self.cache[norm] = brand
        return brand


class CategoryRegistry:
    def __init__(self, session):
        self.session = session
        self.cache: dict[tuple[int | None, str], Category] = {}
        self._populate()

    def _cache_key(self, parent_id: int | None, name: str) -> tuple[int | None, str]:
        return parent_id, normalize_name(name)

    def _populate(self):
        for category in self.session.query(Category).all():
            norm = normalize_name(category.name)
            if not norm:
                continue
            key = self._cache_key(category.parent_id, category.name)
            self.cache.setdefault(key, category)

    def ensure_for_levels(self, levels: list[str]) -> Category | None:
        parent: Category | None = None
        for raw_level in levels:
            key = self._cache_key(parent.id if parent else None, raw_level)
            category = self.cache.get(key)
            if not category:
                slug_base = slugify(raw_level) or "category"
                slug_value = unique_slug(self.session, Category, slug_base)
                parent_address = parent.address if parent else None
                category = Category(
                    name=raw_level,
                    slug=slug_value,
                    parent=parent,
                    level=(parent.level if parent else 0) + 1,
                    address=hierarchical_address(slug_value, parent_address),
                )
                self.session.add(category)
                self.session.flush()
                self.cache[key] = category
            parent = category
        return parent


def ensure_catalog_entries_for_products(session, products=None):
    """
    Ensure there are Brand and Category records for the provided products.
    If no list is supplied, only products missing brand_id or category_id are processed.
    """
    registry_brand = BrandRegistry(session)
    registry_category = CategoryRegistry(session)
    if products is None:
        products = (
            session.query(Product)
            .filter(or_(Product.brand_id.is_(None), Product.category_id.is_(None)))
            .all()
        )
    updated = False
    for product in products:
        product_updated = False
        if product.brand and not product.brand_id:
            brand = registry_brand.ensure(product.brand)
            if brand:
                product.brand_id = brand.id
                product.brand = brand.name
                product_updated = True
        if not product.category_id:
            payload = {
                "primary_group": product.primary_group,
                "category": product.category,
                "secondary_group": product.secondary_group,
                "group": product.group,
                "tertiary_group": product.tertiary_group,
                "subgroup": product.subgroup,
                "quaternary_group": product.quaternary_group,
            }
            category = registry_category.ensure_for_levels(extract_category_levels(payload))
            if category:
                product.category_id = category.id
                if not product.category:
                    product.category = category.full_address
                product_updated = True
        if product_updated:
            updated = True
    if updated:
        session.commit()
