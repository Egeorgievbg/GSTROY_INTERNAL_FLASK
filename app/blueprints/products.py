import csv
import os
from io import BytesIO, StringIO

from app.blueprints.catalog_sync import BrandRegistry, CategoryRegistry
from app.services.art_info_service import ArtInfoService
from app.services.search_service import ProductSearchService
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from constants import (
    ALLOWED_CSV_MIME_TYPES,
    BOOLEAN_FIELDS,
    DEFAULT_PRODUCT_IMAGE,
    FLOAT_FIELDS,
    PRODUCT_CSV_FIELDS,
)
from helpers import canonical_unit_name, require_admin, user_warehouse, safe_redirect_target
from models import (
    Category,
    PricemindCompetitorPrice,
    PricemindSnapshot,
    PricemindSyncLog,
    Product,
)
from printer_utils import (
    active_printers_for_warehouse,
    resolve_printer_for_warehouse,
    send_printer_request,
)
from utils import (
    CSV_IMPORT_MAP,
    ensure_catalog_fields,
    normalize_header,
    parse_bool,
    parse_float,
)


products_bp = Blueprint("products", __name__)


def build_category_tree(categories):
    tree = {}
    nodes = {}
    for category in categories:
        name = (category.name or "").strip()
        if not name:
            continue
        nodes[category.id] = {"name": name, "children": {}, "parent_id": category.parent_id}
    for category in categories:
        node = nodes.get(category.id)
        if not node:
            continue
        parent_id = node["parent_id"]
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"][node["name"]] = node
        else:
            tree[node["name"]] = node
    for node in nodes.values():
        node.pop("parent_id", None)
    return tree


def build_nav_category_tree(categories, allowed_ids=None):
    nodes = {}
    roots = []
    allowed = set(allowed_ids) if allowed_ids is not None else None
    for category in categories:
        if allowed is not None and category.id not in allowed:
            continue
        name = (category.name or "").strip()
        slug = (category.slug or "").strip()
        if not name or not slug:
            continue
        nodes[category.id] = {
            "id": category.id,
            "name": name,
            "slug": slug,
            "children": [],
        }
    for category in categories:
        node = nodes.get(category.id)
        if not node:
            continue
        parent_id = category.parent_id
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


def expand_category_ids_with_parents(categories, active_ids):
    if not active_ids:
        return set()
    parent_map = {category.id: category.parent_id for category in categories}
    expanded = set(active_ids)
    for category_id in list(active_ids):
        current_id = category_id
        while True:
            parent_id = parent_map.get(current_id)
            if not parent_id or parent_id in expanded:
                break
            expanded.add(parent_id)
            current_id = parent_id
    return expanded


def collect_category_ids(root_id, children_map):
    ids = []
    stack = [root_id]
    while stack:
        current_id = stack.pop()
        ids.append(current_id)
        for child in children_map.get(current_id, []):
            stack.append(child.id)
    return ids


def parse_float_arg(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None



def user_can_view_competitor_prices(user):
    return bool(
        user
        and (
            getattr(user, "is_admin", False)
            or getattr(user, "can_view_competitor_prices", False)
        )
    )


def sample_competitor_prices(base_price):
    try:
        base_price_value = float(base_price or 0)
    except (TypeError, ValueError):
        base_price_value = 0.0
    competitors = [
        ("praktiker.bg", 12.9, 299.9, "https://praktiker.bg", "26.08.2025"),
        ("praktis.bg", -8.3, 285.5, "https://praktis.bg", "22.08.2025"),
        ("onlinemashini.bg", 4.55, 294.2, "https://onlinemashini.bg", "24.08.2025"),
        ("mr.bricolage.bg", -4.0, 290.0, "https://mr-bricolage.bg", "20.08.2025"),
        ("etools.bg", 1.2, 296.3, "https://etools.bg", "25.08.2025"),
        ("temax.bg", -3.75, 290.9, "https://temax.bg", "19.08.2025"),
        ("mashini.bg", 9.1, 302.6, "https://mashini.bg", "15.08.2025"),
    ]
    return [
        {
            "name": name,
            "price": (base_price_value + delta) if base_price_value else fallback,
            "url": url,
            "last_checked": last_checked,
            "currency": "BGN",
        }
        for name, delta, fallback, url, last_checked in competitors
    ]


@products_bp.route("/products")
def products():
    session = g.db
    item_number = (request.args.get("item_number") or "").strip()
    name_query = (request.args.get("name") or "").strip()
    brand_filter = (request.args.get("brand") or "").strip()
    main_group_filter = (request.args.get("main_group") or "").strip()
    view_mode = request.args.get("view", "cards")
    if view_mode not in ("cards", "table"):
        view_mode = "cards"

    page = request.args.get("page", 1, type=int)
    per_page = 30
    search_service = ProductSearchService(current_app)
    use_es = search_service.is_enabled() and any(
        [name_query, item_number, brand_filter, main_group_filter]
    )

    current_batch = []
    total_items = 0
    has_more = False

    if use_es:
        search_term = " ".join([value for value in [item_number, name_query] if value]).strip()
        search_result = search_service.search(
            query=search_term,
            item_number=item_number,
            brand=brand_filter,
            main_group=main_group_filter,
            page=page,
            per_page=per_page,
        )
        if search_result:
            ids, total_items = search_result
            if ids:
                products_by_id = {
                    product.id: product
                    for product in session.query(Product).filter(Product.id.in_(ids)).all()
                }
                current_batch = [products_by_id[pid] for pid in ids if pid in products_by_id]
            has_more = page * per_page < total_items
        else:
            use_es = False

    if not use_es:
        query = session.query(Product).filter(Product.is_active.is_(True))
        if item_number:
            query = query.filter(func.lower(Product.item_number).contains(item_number.lower()))
        if name_query:
            query = query.filter(func.lower(Product.name).contains(name_query.lower()))
        if brand_filter:
            query = query.filter(Product.brand == brand_filter)
        if main_group_filter:
            group_expr = func.coalesce(Product.primary_group, Product.category)
            query = query.filter(group_expr == main_group_filter)
        total_items = query.order_by(None).count()
        current_batch = (
            query.order_by(Product.name)
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        has_more = page * per_page < total_items
    base_args = request.args.to_dict()
    base_args.pop("page", None)
    base_args.pop("view", None)
    cards_url = url_for("products.products", **{**base_args, "view": "cards"})
    table_url = url_for("products.products", **{**base_args, "view": "table"})

    is_partial = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get(
        "partial"
    )
    if is_partial:
        return render_template(
            "products_partial.html",
            products=current_batch,
            view_mode=view_mode,
        )

    brands = (
        session.query(Product.brand)
        .filter(Product.is_active.is_(True))
        .filter(Product.brand.isnot(None))
        .filter(Product.brand != "")
        .distinct()
        .order_by(Product.brand)
        .all()
    )
    brands = [row[0] for row in brands]

    group_expr = func.coalesce(Product.primary_group, Product.category)
    main_groups = (
        session.query(group_expr)
        .filter(Product.is_active.is_(True))
        .filter(group_expr.isnot(None))
        .filter(group_expr != "")
        .distinct()
        .order_by(group_expr)
        .all()
    )
    main_groups = [row[0] for row in main_groups]

    categories = session.query(Category).order_by(Category.level, Category.name).all()
    active_category_ids = {
        row[0]
        for row in session.query(Product.category_id)
        .filter(Product.is_active.is_(True))
        .filter(Product.category_id.isnot(None))
        .distinct()
        .all()
    }
    visible_category_ids = expand_category_ids_with_parents(
        categories, active_category_ids
    )
    category_tree = build_nav_category_tree(categories, allowed_ids=visible_category_ids)

    return render_template(
        "products.html",
        products=current_batch,
        total_items=total_items,
        has_more=has_more,
        next_page=page + 1,
        brands=brands,
        main_groups=main_groups,
        view_mode=view_mode,
        cards_url=cards_url,
        table_url=table_url,
        category_tree=category_tree,
        name_query=name_query,
        selected_brand=brand_filter,
        main_group_filter=main_group_filter,
    )


@products_bp.route("/category/<slug>")
@products_bp.route("/categories/<slug>")
def category_page(slug):
    session = g.db
    category = (
        session.query(Category)
        .filter(func.lower(Category.slug) == (slug or "").strip().lower())
        .first()
    )
    if not category:
        abort(404)

    categories = session.query(Category).order_by(Category.level, Category.name).all()
    active_category_ids = {
        row[0]
        for row in session.query(Product.category_id)
        .filter(Product.is_active.is_(True))
        .filter(Product.category_id.isnot(None))
        .distinct()
        .all()
    }
    visible_category_ids = expand_category_ids_with_parents(
        categories, active_category_ids
    )
    category_by_id = {cat.id: cat for cat in categories}
    children_map = {}
    for cat in categories:
        children_map.setdefault(cat.parent_id, []).append(cat)

    category_ids = collect_category_ids(category.id, children_map)
    nav_categories = build_nav_category_tree(categories, allowed_ids=visible_category_ids)
    subcategories = sorted(
        [
            cat
            for cat in children_map.get(category.id, [])
            if cat.id in visible_category_ids
        ],
        key=lambda cat: cat.name or "",
    )

    breadcrumb = []
    node = category
    while node:
        breadcrumb.append(node)
        node = category_by_id.get(node.parent_id)
    breadcrumb = list(reversed(breadcrumb))

    search_query = (
        request.args.get("search")
        or request.args.get("q")
        or request.args.get("name")
        or ""
    )
    search_query = search_query.strip()
    brand_filter = (request.args.get("brand") or "").strip()
    min_price = parse_float_arg(request.args.get("min_price"))
    max_price = parse_float_arg(request.args.get("max_price"))
    eur_to_bgn = 1.95583
    if min_price is not None:
        min_price *= eur_to_bgn
    if max_price is not None:
        max_price *= eur_to_bgn
    sort = (request.args.get("sort") or "newest").strip()
    view_mode = request.args.get("view", "cards")
    if view_mode not in ("cards", "table"):
        view_mode = "cards"

    price_expr = func.coalesce(
        Product.promo_price_unit_1,
        Product.visible_price_unit_1,
        Product.price_unit_1,
        Product.price_unit_2,
    )

    page = request.args.get("page", 1, type=int)
    per_page = 30
    search_service = ProductSearchService(current_app)
    use_es = search_service.is_enabled() and any(
        [search_query, brand_filter, min_price is not None, max_price is not None]
    )

    current_batch = []
    total_items = 0
    has_more = False

    if use_es:
        search_result = search_service.search(
            query=search_query,
            item_number=search_query,
            brand=brand_filter,
            main_group=None,
            page=page,
            per_page=per_page,
            category_ids=category_ids,
            price_min=min_price,
            price_max=max_price,
            sort=sort,
        )
        if search_result:
            ids, total_items = search_result
            if ids:
                products_by_id = {
                    product.id: product
                    for product in session.query(Product).filter(Product.id.in_(ids)).all()
                }
                current_batch = [products_by_id[pid] for pid in ids if pid in products_by_id]
            has_more = page * per_page < total_items
        else:
            use_es = False

    if not use_es:
        query = (
            session.query(Product)
            .filter(Product.is_active.is_(True))
            .filter(Product.category_id.in_(category_ids))
        )
        if search_query:
            like_search = f"%{search_query}%"
            query = query.filter(
                or_(
                    Product.name.ilike(like_search),
                    Product.item_number.ilike(like_search),
                    Product.brand.ilike(like_search),
                )
            )
        if brand_filter:
            query = query.filter(Product.brand == brand_filter)
        if min_price is not None:
            query = query.filter(price_expr >= min_price)
        if max_price is not None:
            query = query.filter(price_expr <= max_price)

        if sort == "price_asc":
            order_clause = price_expr.asc()
        elif sort == "price_desc":
            order_clause = price_expr.desc()
        else:
            order_clause = Product.id.desc()

        total_items = query.order_by(None).count()
        current_batch = (
            query.order_by(order_clause, Product.name)
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        has_more = page * per_page < total_items

    base_args = request.args.to_dict()
    base_args.pop("page", None)
    base_args.pop("view", None)
    base_args.pop("partial", None)
    cards_url = url_for("products.category_page", slug=category.slug, **{**base_args, "view": "cards"})
    table_url = url_for("products.category_page", slug=category.slug, **{**base_args, "view": "table"})

    is_partial = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get(
        "partial"
    )
    if is_partial:
        return render_template(
            "products_partial.html",
            products=current_batch,
            view_mode=view_mode,
        )

    brands = (
        session.query(Product.brand)
        .filter(Product.category_id.in_(category_ids))
        .filter(Product.is_active.is_(True))
        .filter(Product.brand.isnot(None))
        .filter(Product.brand != "")
        .distinct()
        .order_by(Product.brand)
        .all()
    )
    brands = [row[0] for row in brands]

    return render_template(
        "category_page.html",
        category=category,
        breadcrumb=breadcrumb,
        subcategories=subcategories,
        nav_categories=nav_categories,
        active_category_slug=category.slug,
        products=current_batch,
        total_items=total_items,
        has_more=has_more,
        next_page=page + 1,
        brands=brands,
        selected_brand=brand_filter,
        sort=sort,
        view_mode=view_mode,
        cards_url=cards_url,
        table_url=table_url,
        meta_title=category.meta_title,
        min_price=min_price,
        max_price=max_price,
        search_query=search_query,
    )


@products_bp.route("/product/<int:product_id>")
@login_required
def product_detail(product_id):
    session = g.db
    product = session.get(Product, product_id)
    if not product or not product.is_active:
        abort(404)

    stocks = []
    delivery_log = []
    kpi = {
        "physical": 0.0,
        "reserved": 0.0,
        "free": 0.0,
        "incoming": 0.0,
        "scrap": 0.0,
    }
    pricing = None

    art_id = ArtInfoService.normalize_art_id(product.versus_id)
    if art_id:
        try:
            service = ArtInfoService(session)
            payload = service.get_art_info(art_id)
            view = service.build_view(payload)
            stocks = view.get("stocks") or []
            kpi = view.get("kpi") or kpi
            pricing = view.get("pricing")
            delivery_log = view.get("price_rows") or []
        except Exception as exc:
            current_app.logger.warning("ArtInfo fetch failed for %s: %s", art_id, exc)

    if pricing is None:
        base_price = (
            product.visible_price_unit_1
            or product.price_unit_1
            or product.price_unit_2
            or 0.0
        )
        promo_price = product.promo_price_unit_1 or product.promo_price_unit_2
        current_price = promo_price if promo_price else base_price
        pricing = {
            "current": float(current_price or 0.0),
            "original": float(base_price or current_price or 0.0),
            "currency": "BGN",
            "has_promo": bool(promo_price and base_price and promo_price < base_price),
        }

    eur_rate = 1.95583
    competitor_base_price = (pricing.get("current") or pricing.get("original") or 0.0) / eur_rate

    warehouse = user_warehouse(current_user)
    product_printers = []
    product_default_printer_id = None
    if warehouse:
        product_printers = active_printers_for_warehouse(g.db, warehouse.id)
        default_printer = resolve_printer_for_warehouse(g.db, warehouse.id)
        product_default_printer_id = default_printer.id if default_printer else None
    competitor_prices = []
    if user_can_view_competitor_prices(g.current_user):
        latest_log = (
            session.query(PricemindSyncLog)
            .filter(PricemindSyncLog.status == "SUCCESS")
            .order_by(PricemindSyncLog.started_at.desc())
            .first()
        )
        if latest_log:
            snapshot = (
                session.query(PricemindSnapshot)
                .filter(PricemindSnapshot.sync_log_id == latest_log.id)
                .filter(
                    or_(
                        PricemindSnapshot.product_id == product.id,
                        PricemindSnapshot.sku == product.item_number,
                    )
                )
                .order_by(PricemindSnapshot.id.desc())
                .first()
            )
            if snapshot:
                competitor_rows = (
                    session.query(PricemindCompetitorPrice)
                    .filter(PricemindCompetitorPrice.snapshot_id == snapshot.id)
                    .all()
                )
                for row in competitor_rows:
                    price = row.offer_price or row.special_price or row.regular_price
                    if price is None:
                        continue
                    competitor_prices.append(
                        {
                            "name": row.competitor,
                            "price": float(price) / eur_rate,
                            "currency": "EUR",
                            "last_checked": row.retrieved_at.strftime("%Y-%m-%d %H:%M")
                            if row.retrieved_at
                            else None,
                            "url": None,
                        }
                    )

    return render_template(
        "product_detail.html",
        product=product,
        pricing=pricing,
        stocks=stocks,
        kpi=kpi,
        delivery_log=delivery_log,
        competitor_prices=competitor_prices,
        competitor_base_price=competitor_base_price,
        show_competitor_prices=user_can_view_competitor_prices(g.current_user),
        product_printers=product_printers,
        product_default_printer_id=product_default_printer_id,
    )

@products_bp.route("/products/import", methods=["GET", "POST"])
@login_required
def import_products():
    require_admin()
    session = g.db
    processed = {"created": 0, "updated": 0}
    changed_item_numbers = set()
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Моля, качете CSV файл.", "warning")
            return redirect(url_for("products.import_products"))
        if file.mimetype and file.mimetype not in ALLOWED_CSV_MIME_TYPES:
            flash("Неподдържан формат на файл.", "danger")
            return redirect(url_for("products.import_products"))
        file.stream.seek(0, os.SEEK_END)
        size = file.stream.tell()
        if size > current_app.config["UPLOAD_MAX_BYTES"]:
            flash(
                f"Файлът е твърде голям (максимум {current_app.config['UPLOAD_MAX_BYTES'] // 1024} KB).",
                "danger",
            )
            return redirect(url_for("products.import_products"))
        file.stream.seek(0)
        raw = file.read()
        data = None
        for encoding in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                data = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if data is None:
            data = raw.decode("utf-8", errors="ignore")
        sample = data[:2048]
        delimiter = ","
        delimiter_candidates = {",": sample.count(","), ";": sample.count(";"), "\t": sample.count("\t")}
        if any(delimiter_candidates.values()):
            delimiter = max(delimiter_candidates, key=delimiter_candidates.get)
        reader = csv.DictReader(StringIO(data), delimiter=delimiter)
        if not reader.fieldnames:
            flash("CSV файлът няма заглавен ред.", "danger")
            return redirect(url_for("products.import_products"))
        header_map = {normalize_header(name): name for name in reader.fieldnames}
        required_cols = {
            normalize_header(name)
            for name in ["Номенклатурен номер", "Наименование", "Мерна единица 1"]
        }
        if not required_cols.issubset(header_map.keys()):
            flash(
                "Задължителните колони са „Номенклатурен номер“, „Наименование“ и „Мерна единица 1“.",
                "danger",
            )
            return redirect(url_for("products.import_products"))
        brand_registry = BrandRegistry(session)
        category_registry = CategoryRegistry(session)
        for row in reader:
            payload = {}
            for normalized_name, header in header_map.items():
                attr = CSV_IMPORT_MAP.get(normalized_name)
                if not attr:
                    continue
                raw_value = row.get(header)
                if attr in BOOLEAN_FIELDS:
                    payload[attr] = parse_bool(raw_value)
                elif attr in FLOAT_FIELDS:
                    payload[attr] = parse_float(raw_value)
                else:
                    payload[attr] = (raw_value or "").strip() or None
            item_number = payload.get("item_number")
            name = payload.get("name")
            if not item_number or not name:
                continue
            ensure_catalog_fields(payload, brand_registry, category_registry)
            payload["main_unit"] = payload.get("main_unit") or "pcs"
            image_value = payload.get("image_url")
            if not image_value:
                payload["image_url"] = DEFAULT_PRODUCT_IMAGE
            elif not str(image_value).lower().startswith(("http://", "https://")):
                payload["image_url"] = image_value.lstrip("/").replace("static/", "")
            product = session.query(Product).filter_by(item_number=item_number).first()
            if product:
                for key, val in payload.items():
                    setattr(product, key, val)
                processed["updated"] += 1
                changed_item_numbers.add(product.item_number)
            else:
                session.add(Product(**payload))
                processed["created"] += 1
                changed_item_numbers.add(item_number)
        session.commit()
        if changed_item_numbers:
            service = ProductSearchService(current_app)
            if service.is_enabled() and service.ensure_index():
                products = (
                    session.query(Product)
                    .filter(Product.item_number.in_(list(changed_item_numbers)))
                    .all()
                )
                service.bulk_index(products)
        flash(
            f"Импорт завършен. Нови: {processed['created']}, обновени: {processed['updated']}.",
            "success",
        )
        return redirect(url_for("products.products"))
    return render_template("products_import.html")


@products_bp.route("/products/export")
@login_required
def export_products():
    require_admin()
    session = g.db
    products = session.query(Product).order_by(Product.item_number).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([header for _, header in PRODUCT_CSV_FIELDS])
    for product in products:
        row = []
        for attr, _ in PRODUCT_CSV_FIELDS:
            value = getattr(product, attr)
            if attr in BOOLEAN_FIELDS:
                row.append("1" if value else "0")
            elif attr in FLOAT_FIELDS:
                row.append("" if value is None else str(value))
            elif attr == "image_url":
                if not value:
                    row.append(DEFAULT_PRODUCT_IMAGE)
                else:
                    row.append(value.lstrip("/"))
            else:
                row.append(value or "")
        writer.writerow(row)
    buffer = BytesIO()
    buffer.write(output.getvalue().encode("utf-8-sig"))
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="text/csv",
        as_attachment=True,
        download_name="products_export.csv",
    )


@products_bp.route("/products/lookup")
def product_lookup():
    session = g.db
    item_number = (request.args.get("item_number") or "").strip()
    barcode = (request.args.get("barcode") or "").strip()
    if not item_number and not barcode:
        return jsonify({}), 400
    product = None
    if barcode:
        product = (
            session.query(Product)
            .filter(func.upper(Product.barcode) == barcode.upper())
            .first()
        )
    if product is None and item_number:
        product = (
            session.query(Product)
            .filter(func.upper(Product.item_number) == item_number.upper())
            .first()
        )
    if product is None:
        return jsonify({}), 404
    image_url = None
    if product.image_url and product.image_url != DEFAULT_PRODUCT_IMAGE:
        image_path = product.image_url.lstrip("/")
        image_url = url_for("static", filename=image_path)
    return jsonify(
        {
            "item_number": product.item_number,
            "name": product.name,
            "brand": product.brand,
            "category": product.category,
            "main_unit": product.main_unit,
            "secondary_unit": product.secondary_unit,
            "unit_conversion_ratio": product.unit_conversion_ratio,
            "canonical_main_unit": canonical_unit_name(product.main_unit),
            "canonical_secondary_unit": canonical_unit_name(product.secondary_unit),
            "image_url": image_url,
            "storage_location": product.storage_location,
        }
    )


@products_bp.route("/products/suggest")
def product_suggest():
    query = (request.args.get("q") or "").strip()
    limit = request.args.get("limit", type=int) or 8
    service = ProductSearchService(current_app)
    if not query or not service.is_enabled() or not service.ensure_index():
        return jsonify({"items": []})
    items = service.suggest(query, limit=limit)
    return jsonify({"items": items})
