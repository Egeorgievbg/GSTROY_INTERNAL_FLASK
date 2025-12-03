import csv
import os
import random

from io import BytesIO, StringIO

from .catalog_sync import BrandRegistry, CategoryRegistry
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
from sqlalchemy import func

from .catalog_utils import CSV_IMPORT_MAP, ensure_catalog_fields, normalize_header
from gstroy_constants import (
    ALLOWED_CSV_MIME_TYPES,
    BOOLEAN_FIELDS,
    DEFAULT_PRODUCT_IMAGE,
    FLOAT_FIELDS,
    PRODUCT_CSV_FIELDS,
)
from helpers import canonical_unit_name, require_admin, user_warehouse
from models import Product
from printer_utils import (
    active_printers_for_warehouse,
    resolve_printer_for_warehouse,
    send_printer_request,
)

catalog_bp = Blueprint("catalog", __name__)


def build_product_category_tree(products):
    tree = {}
    for product in products:
        main = (product.primary_group or product.category or "Други").strip()
        sub = (product.secondary_group or product.group or "").strip()
        sub2 = (product.tertiary_group or product.subgroup or "").strip()
        sub3 = (product.quaternary_group or "").strip()
        node = tree.setdefault(main, {"children": {}})
        if sub:
            node = node["children"].setdefault(sub, {"children": {}})
        if sub2:
            node = node["children"].setdefault(sub2, {"children": {}})
        if sub3:
            node["children"].setdefault(sub3, {"children": {}})
    return tree


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


@catalog_bp.route("/products")
@login_required
def products():
    session = g.db
    all_products = session.query(Product).order_by(Product.name).all()
    item_number = (request.args.get("item_number") or "").strip().lower()
    name_query = (request.args.get("name") or "").strip().lower()
    brand_filter = (request.args.get("brand") or "").strip()
    main_group_filter = (request.args.get("main_group") or "").strip()
    view_mode = request.args.get("view", "cards")
    if view_mode not in ("cards", "table"):
        view_mode = "cards"

    def matches(product):
        code = (product.item_number or "").lower()
        name_val = (product.name or "").lower()
        brand_val = (product.brand or "")
        main_group = (product.primary_group or product.category or "Други")
        if item_number and item_number not in code:
            return False
        if name_query and name_query not in name_val:
            return False
        if brand_filter and brand_val != brand_filter:
            return False
        if main_group_filter and main_group != main_group_filter:
            return False
        return True

    filtered_products = [product for product in all_products if matches(product)]
    page = request.args.get("page", 1, type=int)
    per_page = 30
    total_items = len(filtered_products)
    start = (page - 1) * per_page
    end = start + per_page
    current_batch = filtered_products[start:end]
    has_more = end < total_items
    base_args = request.args.to_dict()
    base_args.pop("page", None)
    base_args.pop("view", None)
    cards_url = url_for("catalog.products", **{**base_args, "view": "cards"})
    table_url = url_for("catalog.products", **{**base_args, "view": "table"})
    brands = sorted({p.brand for p in all_products if p.brand})
    main_groups = sorted({p.primary_group or p.category or "Други" for p in all_products})
    category_tree = build_product_category_tree(all_products)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get(
        "partial"
    ):
        return render_template(
            "products_partial.html",
            products=current_batch,
            view_mode=view_mode,
        )
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
    )


@catalog_bp.route("/product/<int:product_id>")
@login_required
def product_detail(product_id):
    session = g.db
    product = session.get(Product, product_id)
    if not product:
        abort(404)
    warehouses_list = [
        "Склад 1",
        "Склад 2",
        "Склад 3",
        "Склад 4",
        "Склад 5",
        "Склад 6",
        "Склад 7",
        "Склад 8",
    ]
    stock_matrix = []
    total_physical = 0.0
    total_reserved = 0.0
    random.seed(product.id)
    for wh_name in warehouses_list:
        has_stock = random.random() > 0.6
        if has_stock:
            qty = float(random.randint(1, 50))
            reserved = 0.0
            if random.random() > 0.8:
                reserved = float(random.randint(1, int(qty)))
            free = qty - reserved
            stock_matrix.append(
                {
                    "name": wh_name,
                    "physical": qty,
                    "reserved": reserved,
                    "free": free,
                    "active": True,
                }
            )
            total_physical += qty
            total_reserved += reserved
        else:
            stock_matrix.append(
                {"name": wh_name, "physical": 0.0, "reserved": 0.0, "free": 0.0, "active": False}
            )
    kpi_data = {
        "physical": total_physical,
        "reserved": total_reserved,
        "free": total_physical - total_reserved,
        "incoming": random.choice([0.0, 0.0, 100.0, 500.0]),
        "scrap": 0.0,
    }
    base_price = (
        product.price_unit_1
        if hasattr(product, "price_unit_1") and product.price_unit_1
        else 10.0
    )
    delivery_history = [
        {"warehouse": "Склад А", "price": base_price * 1.02, "date": "15.08.2025"},
        {"warehouse": "Склад Б", "price": base_price * 0.98, "date": "10.08.2025"},
    ]
    competitor_base_price = (
        product.promo_price_unit_1 or product.price_unit_1 or product.price_unit_2 or 0.0
    )
    show_competitor_prices = user_can_view_competitor_prices(g.current_user)
    competitor_prices = (
        sample_competitor_prices(competitor_base_price) if show_competitor_prices else []
    )
    warehouse = user_warehouse(g.current_user)
    product_printers = []
    product_default_printer_id = None
    if warehouse:
        product_printers = active_printers_for_warehouse(session, warehouse.id)
        default_printer = resolve_printer_for_warehouse(session, warehouse.id)
        product_default_printer_id = default_printer.id if default_printer else None
    return render_template(
        "product_detail.html",
        product=product,
        stocks=stock_matrix,
        kpi=kpi_data,
        delivery_log=delivery_history,
        competitor_prices=competitor_prices,
        competitor_base_price=competitor_base_price,
        show_competitor_prices=show_competitor_prices,
        product_printers=product_printers,
        product_default_printer_id=product_default_printer_id,
    )


@catalog_bp.route("/api/printers/print-product-label", methods=["POST"])
@login_required
def api_print_product_label():
    session = g.db
    data = request.get_json(silent=True) or request.form
    if not data:
        return jsonify({"success": False, "message": "Невалидна заявка"}), 400
    product_id = data.get("product_id")
    if not product_id:
        return jsonify({"success": False, "message": "Липсва продукт"}), 400
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Невалиден продукт"}), 400
    product = session.get(Product, product_id)
    if not product:
        return jsonify({"success": False, "message": "Продуктът не е намерен"}), 404
    warehouse = user_warehouse(g.current_user)
    if not warehouse:
        return jsonify({"success": False, "message": "Липсва склад"}), 400
    printer = resolve_printer_for_warehouse(session, warehouse.id, data.get("printer_id"))
    if not printer:
        return jsonify({"success": False, "message": "Няма наличен принтер"}), 400
    try:
        copies = int(data.get("copies", 1))
    except (TypeError, ValueError):
        copies = 1
    copies = max(1, copies)
    payload = {
        "name": product.name,
        "barcode": (product.barcode or product.item_number or "").strip(),
        "qr_data": url_for("catalog.product_detail", product_id=product.id, _external=True),
        "copies": copies,
    }
    try:
        send_printer_request(printer, f"printers/{printer.ip_address}/print-product-label", payload)
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 502
    return jsonify({"success": True})


@catalog_bp.route("/products/import", methods=["GET", "POST"])
@login_required
def import_products():
    require_admin()
    session = g.db
    processed = {"created": 0, "updated": 0}
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Моля, прикачете CSV файл.", "warning")
            return redirect(url_for("catalog.import_products"))
        if file.mimetype and file.mimetype not in ALLOWED_CSV_MIME_TYPES:
            flash("Невалиден тип на файла.", "danger")
            return redirect(url_for("catalog.import_products"))
        file.stream.seek(0, os.SEEK_END)
        size = file.stream.tell()
        if size > current_app.config["UPLOAD_MAX_BYTES"]:
            flash("Файлът е твърде голям.", "danger")
            return redirect(url_for("catalog.import_products"))
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
        delimiter_candidates = {
            ",": sample.count(","),
            ";": sample.count(";"),
            "\t": sample.count("\t"),
        }
        if any(delimiter_candidates.values()):
            delimiter = max(delimiter_candidates, key=delimiter_candidates.get)
        reader = csv.DictReader(StringIO(data), delimiter=delimiter)
        if not reader.fieldnames:
            flash("CSV файлът е празен или невалиден.", "danger")
            return redirect(url_for("catalog.import_products"))
        header_map = {normalize_header(name): name for name in reader.fieldnames}
        required_cols = {
            normalize_header(name)
            for name in ["Номенклатурен номер", "Наименование", "Мерна единица 1"]
        }
        if not required_cols.issubset(header_map.keys()):
            flash("CSV файлът няма задължителните колони.", "danger")
            return redirect(url_for("catalog.import_products"))
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
                    payload[attr] = bool(raw_value and raw_value.strip() in {"1", "true"})
                elif attr in FLOAT_FIELDS:
                    try:
                        payload[attr] = float(str(raw_value).replace(",", "."))
                    except (ValueError, TypeError):
                        payload[attr] = None
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
            else:
                session.add(Product(**payload))
                processed["created"] += 1
        session.commit()
        flash(
            f"Импортът завърши. Създадени: {processed['created']}, Обновени: {processed['updated']}.",
            "success",
        )
        return redirect(url_for("catalog.products"))
    return render_template("products_import.html")


@catalog_bp.route("/products/export")
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


@catalog_bp.route("/products/lookup")
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
