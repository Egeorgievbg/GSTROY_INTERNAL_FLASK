import os
import re
from datetime import datetime

from flask import Blueprint, flash, g, redirect, render_template, request, url_for, current_app, jsonify
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, load_only
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from app.services.sync_service import ProductSyncService
from app.services.feed_sync_service import ProductFeedSyncService
from app.services.pricemind_sync_service import PricemindSyncService
from app.services.search_service import ProductSearchService
from helpers import (
    hierarchical_address,
    parse_bool,
    parse_float,
    require_admin,
    slugify,
    unique_slug,
)
from .catalog_sync import ensure_catalog_entries_for_products
from models import (
    AccessWindow,
    AcademyCategory,
    AcademyContentType,
    Brand,
    Category,
    ContentItem,
    Product,
    PricemindSyncLog,
    PricemindSnapshot,
    PricemindCompetitorPrice,
    Role,
    ServicePoint,
    SyncLog,
    User,
    UserContentProgress,
    Warehouse,
    Printer,
    Location,
)
from printer_service import get_printer_status

# Създаваме Blueprint-а
admin_bp = Blueprint("admin", __name__, url_prefix="/admin", template_folder="templates")


def _get_products_query(request):
    session = g.db
    search = (request.args.get("search") or "").strip()
    category = (request.args.get("category") or "").strip()
    brand = (request.args.get("brand") or "").strip()
    status = request.args.get("status", "all")

    query = session.query(Product)
    if search:
        like_search = f"%{search}%"
        query = query.filter(
            or_(
                Product.name.ilike(like_search),
                Product.item_number.ilike(like_search),
                Product.brand.ilike(like_search),
            )
        )
    if category:
        query = query.filter(
            or_(
                Product.primary_group.ilike(category),
                Product.category.ilike(category),
            )
        )
    if brand:
        query = query.filter(Product.brand.ilike(f"%{brand}%"))
    if status == "active":
        query = query.filter(Product.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Product.is_active.is_(False))
    return query, search, category, brand, status


def _load_category_tree(session):
    return (
        session.query(Category)
        .options(joinedload(Category.parent))
        .order_by(Category.address)
        .all()
    )


def _refresh_category_tree(node):
    slug_value = node.slug or slugify(node.name) or "category"
    parent_address = node.parent.address if node.parent else None
    node.slug = slug_value
    node.address = hierarchical_address(slug_value, parent_address)
    node.level = (node.parent.level if node.parent else 0) + 1
    for child in node.children:
        _refresh_category_tree(child)


def _collect_category_ids(category):
    ids = [category.id]
    for child in category.children:
        ids.extend(_collect_category_ids(child))
    return ids


DAYS_OF_WEEK = [
    "Понеделник",
    "Вторник",
    "Сряда",
    "Четвъртък",
    "Петък",
    "Събота",
    "Неделя",
]


def _access_window_form_options(session):
    return {
        "roles": session.query(Role).filter(Role.is_active.is_(True)).order_by(Role.name).all(),
        "warehouses": session.query(Warehouse).order_by(Warehouse.name).all(),
        "users": session.query(User)
        .filter(User.is_staff.is_(True))
        .order_by(User.full_name)
        .all(),
        "days": DAYS_OF_WEEK,
    }


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def _format_seconds(total_seconds):
    total_seconds = max(int(total_seconds or 0), 0)
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _time_ago(value):
    if not value:
        return "Never"
    delta = datetime.utcnow() - value
    return f"{_format_seconds(delta.total_seconds())} ago"


def _format_duration(started_at, completed_at):
    if not started_at:
        return "-"
    if not completed_at:
        return "In progress"
    return _format_seconds((completed_at - started_at).total_seconds())


def _status_badge(status):
    if status == "SUCCESS":
        return "success"
    if status == "FAILED":
        return "danger"
    if status == "IN_PROGRESS":
        return "warning"
    return "secondary"


def _load_entities_by_ids(session, model, ids):
    if not ids:
        return []
    records = (
        session.query(model)
        .filter(model.id.in_(ids))
        .order_by(model.name if hasattr(model, "name") else model.full_name)
        .all()
    )
    record_map = {record.id: record for record in records}
    return [record_map[int(item_id)] for item_id in ids if item_id and int(item_id) in record_map]


def _apply_access_window_form(window, form, session):
    window.name = (form.get("name") or window.name or "").strip()
    start_time = _parse_time(form.get("start_time"))
    end_time = _parse_time(form.get("end_time"))
    window.start_time = start_time or window.start_time or datetime.utcnow().time()
    window.end_time = end_time or window.end_time or datetime.utcnow().time()
    window.days_list = [day for day in form.getlist("days") if day in DAYS_OF_WEEK]

    roles_ids = [value for value in form.getlist("roles") if value]
    window.roles = _load_entities_by_ids(session, Role, roles_ids)
    warehouse_ids = [value for value in form.getlist("warehouses") if value]
    window.warehouses = _load_entities_by_ids(session, Warehouse, warehouse_ids)
    user_ids = [value for value in form.getlist("users") if value]
    window.users = _load_entities_by_ids(session, User, user_ids)


def _apply_warehouse_form(warehouse, form):
    warehouse.name = (form.get("name") or warehouse.name or "").strip()
    warehouse.code = (form.get("code") or warehouse.code or "").strip()
    warehouse.description = (form.get("description") or "").strip() or None
    warehouse.printer_server_url = (form.get("printer_server_url") or "").strip() or None
    warehouse.is_active = parse_bool(form.get("is_active"))


def _apply_location_form(location, form):
    location.name = (form.get("name") or location.name or "").strip()
    location.code = (form.get("code") or location.code or "").strip()
    location.description = (form.get("description") or "").strip() or None
    location.is_active = parse_bool(form.get("is_active"))
    parent_id = form.get("parent_id", type=int)
    if parent_id and parent_id != getattr(location, "id", None):
        location.parent = next(
            (loc for loc in location.warehouse.locations if loc.id == parent_id),
            None,
        )
    else:
        location.parent = None


def _set_default_printer(session, printer):
    if not printer or not printer.is_default:
        return
    session.query(Printer).filter(
        Printer.warehouse_id == printer.warehouse_id,
        Printer.id != printer.id,
    ).update({"is_default": False}, synchronize_session="fetch")


def _academy_upload_dir():
    upload_root = os.path.join(current_app.static_folder, "uploads")
    os.makedirs(upload_root, exist_ok=True)
    return upload_root



def _estimate_read_time(html_text: str) -> int:
    if not html_text:
        return 2
    plain = re.sub(r"<[^>]+>", "", html_text)
    words = plain.split()
    return max(1, len(words) // 160)


def _academy_category_choices(session):
    return [category.name for category in session.query(AcademyCategory).order_by(AcademyCategory.name).all()]


def _academy_content_type_choices(session):
    return [ctype.name for ctype in session.query(AcademyContentType).order_by(AcademyContentType.name).all()]


def _ensure_choice_present(options, value):
    if value and value not in options:
        return options + [value]
    return options


@admin_bp.route("/academy/content", methods=["GET", "POST"])
@admin_bp.route("/academy", methods=["GET", "POST"])
def academy_content():
    require_admin()
    session = g.db
    if request.method == "POST":
        item_id = request.form.get("item_id", type=int)
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Заглавието е задължително.", "warning")
            return redirect(url_for(".academy_content"))
        content_type = (request.form.get("content_type") or "NEWS").upper()
        if content_type not in {"STORY", "NEWS", "GUIDE"}:
            content_type = "NEWS"
        media_url = (request.form.get("media_url") or "").strip()
        media_file = request.files.get("media_file")
        if media_file and media_file.filename:
            filename = secure_filename(media_file.filename)
            if filename:
                upload_path = os.path.join(_academy_upload_dir(), filename)
                media_file.save(upload_path)
                media_url = f"uploads/{filename}"
        html_content = (request.form.get("content_html") or "").strip()
        read_time = _estimate_read_time(html_content)
        target_item = session.get(ContentItem, item_id) if item_id else None
        if target_item:
            target_item.title = title
            target_item.summary = (request.form.get("summary") or "").strip()
            target_item.content_html = html_content
            if media_url:
                target_item.media_url = media_url
            target_item.content_type = content_type
            target_item.category = (request.form.get("category") or "").strip() or None
            target_item.read_time_minutes = read_time
            target_item.is_published = parse_bool(request.form.get("is_published"))
            item = target_item
        else:
            item = ContentItem(
                title=title,
                summary=(request.form.get("summary") or "").strip(),
                content_html=html_content,
                media_url=media_url or None,
                content_type=content_type,
                category=(request.form.get("category") or "").strip() or None,
                read_time_minutes=read_time,
                is_published=parse_bool(request.form.get("is_published")),
                created_at=datetime.utcnow(),
            )
        session.add(item)
        session.commit()
        flash("Контентът беше запазен успешно.", "success")
        return redirect(url_for(".academy_content"))
    edit_id = request.args.get("edit", type=int)
    is_new = request.args.get("new")
    if edit_id or is_new:
        edit_item = session.get(ContentItem, edit_id) if edit_id else None
        categories = _academy_category_choices(session)
        content_types = _academy_content_type_choices(session)
        categories = _ensure_choice_present(
            categories, edit_item.category if edit_item else None
        )
        content_types = _ensure_choice_present(
            content_types, edit_item.content_type if edit_item else None
        )
        return render_template(
            "admin/academy_editor.html",
            edit_item=edit_item,
            categories=categories,
            content_types=content_types,
        )

    items = session.query(ContentItem).order_by(ContentItem.created_at.desc()).all()
    progresses = session.query(UserContentProgress).all()
    stats = {}
    reactions = {}
    for record in progresses:
        stats.setdefault(record.content_item_id, 0)
        if record.is_read:
            stats[record.content_item_id] += 1
        if record.reaction:
            reactions.setdefault(record.content_item_id, {})
            reactions[record.content_item_id][record.reaction] = (
                reactions[record.content_item_id].get(record.reaction, 0) + 1
            )
    return render_template(
        "admin/academy_list.html",
        items=items,
        stats=stats,
        reactions=reactions,
    )


@admin_bp.route("/academy/upload-image", methods=["POST"])
def upload_editor_image():
    require_admin()
    if "image" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"error": "No selected file"}), 400
    filename = secure_filename(file.filename)
    if not filename:
        return jsonify({"error": "Invalid file name"}), 400
    upload_folder = os.path.join(
        current_app.static_folder, "uploads", "academy"
    )
    os.makedirs(upload_folder, exist_ok=True)
    file_path = os.path.join(upload_folder, filename)
    file.save(file_path)
    url = url_for("static", filename=f"uploads/academy/{filename}")
    return jsonify({"url": url})


@admin_bp.route("/academy/push", methods=["POST"])
def academy_push():
    require_admin()
    item_id = request.form.get("push_item_id", type=int)
    if not item_id:
        flash("Избери съдържание, което да се изпрати.", "warning")
        return redirect(url_for(".academy_content"))
    flash(f"Push sent to 150 devices with deep link: erp://academy/item/{item_id}", "success")
    return redirect(url_for(".academy_content"))


@admin_bp.route("/academy/delete/<int:item_id>", methods=["POST"])
def delete_academy_item(item_id):
    require_admin()
    session = g.db
    item = session.get(ContentItem, item_id)
    if not item:
        flash("Content item not found.", "warning")
        return redirect(url_for(".academy_content"))
    session.delete(item)
    session.commit()

    flash("Content item was deleted.", "success")
    return redirect(url_for(".academy_content"))


@admin_bp.route("/academy/categories", methods=["GET", "POST"])
def academy_categories():
    require_admin()
    session = g.db
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        if not name:
            flash("Моля въведете име на категория.", "warning")
            return redirect(url_for(".academy_categories"))
        existing = (
            session.query(AcademyCategory)
            .filter(func.lower(AcademyCategory.name) == name.lower())
            .first()
        )
        if existing:
            flash("Категория с това име вече съществува.", "warning")
            return redirect(url_for(".academy_categories"))
        category = AcademyCategory(name=name, description=description)
        session.add(category)
        session.commit()
        flash("Категорията е записана.", "success")
        return redirect(url_for(".academy_categories"))

    categories = session.query(AcademyCategory).order_by(AcademyCategory.name).all()
    return render_template("admin_academy_categories.html", categories=categories)


@admin_bp.route("/academy/categories/<int:category_id>/delete", methods=["POST"])
def delete_academy_category(category_id):
    require_admin()
    session = g.db
    category = session.get(AcademyCategory, category_id)
    if not category:
        return render_template("404.html"), 404
    session.delete(category)
    session.commit()
    flash("Категорията е премахната.", "success")
    return redirect(url_for(".academy_categories"))


@admin_bp.route("/academy/categories/<int:category_id>/update", methods=["POST"])
def update_academy_category(category_id):
    require_admin()
    session = g.db
    category = session.get(AcademyCategory, category_id)
    if not category:
        return render_template("404.html"), 404
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    if not name:
        flash("Моля въведете име на категория.", "warning")
        return redirect(url_for(".academy_categories"))
    existing = (
        session.query(AcademyCategory)
        .filter(
            func.lower(AcademyCategory.name) == name.lower(),
            AcademyCategory.id != category.id,
        )
        .first()
    )
    if existing:
        flash("Категория с това име вече съществува.", "warning")
        return redirect(url_for(".academy_categories"))
    category.name = name
    category.description = description
    session.commit()
    flash("Категорията е записана.", "success")
    return redirect(url_for(".academy_categories"))


@admin_bp.route("/academy/content-types", methods=["GET", "POST"])
def academy_content_types():
    require_admin()
    session = g.db
    if request.method == "POST":
        raw_name = (request.form.get("name") or "").strip()
        name = raw_name.upper()
        description = (request.form.get("description") or "").strip() or None
        if not name:
            flash("Моля въведете код на типа съдържание.", "warning")
            return redirect(url_for(".academy_content_types"))
        existing = (
            session.query(AcademyContentType)
            .filter(func.lower(AcademyContentType.name) == name.lower())
            .first()
        )
        if existing:
            flash("Тип със същото име вече съществува.", "warning")
            return redirect(url_for(".academy_content_types"))
        content_type = AcademyContentType(name=name, description=description)
        session.add(content_type)
        session.commit()
        flash("Типът съдържание е записан.", "success")
        return redirect(url_for(".academy_content_types"))

    content_types = session.query(AcademyContentType).order_by(AcademyContentType.name).all()
    return render_template("admin_academy_types.html", content_types=content_types)


@admin_bp.route("/academy/content-types/<int:type_id>/delete", methods=["POST"])
def delete_academy_content_type(type_id):
    require_admin()
    session = g.db
    content_type = session.get(AcademyContentType, type_id)
    if not content_type:
        return render_template("404.html"), 404
    session.delete(content_type)
    session.commit()
    flash("Типът съдържание беше премахнат.", "success")
    return redirect(url_for(".academy_content_types"))


@admin_bp.route("/academy/content-types/<int:type_id>/update", methods=["POST"])
def update_academy_content_type(type_id):
    require_admin()
    session = g.db
    content_type = session.get(AcademyContentType, type_id)
    if not content_type:
        return render_template("404.html"), 404
    raw_name = (request.form.get("name") or "").strip()
    name = raw_name.upper()
    description = (request.form.get("description") or "").strip() or None
    if not name:
        flash("Моля въведете код на типа съдържание.", "warning")
        return redirect(url_for(".academy_content_types"))
    existing = (
        session.query(AcademyContentType)
        .filter(
            func.lower(AcademyContentType.name) == name.lower(),
            AcademyContentType.id != content_type.id,
        )
        .first()
    )
    if existing:
        flash("Тип със същото име вече съществува.", "warning")
        return redirect(url_for(".academy_content_types"))
    content_type.name = name
    content_type.description = description
    session.commit()
    flash("Типът съдържание е записан.", "success")
    return redirect(url_for(".academy_content_types"))


def _user_form_options(session):
    return {
        "service_points": session.query(ServicePoint).order_by(ServicePoint.name).all(),
        "roles": session.query(Role).order_by(Role.name).all(),
        "warehouses": session.query(Warehouse).order_by(Warehouse.name).all(),
        "managers": session.query(User).filter(User.is_staff.is_(True)).order_by(User.full_name).all(),
    }


def _apply_user_form_values(user, form, session):
    user.full_name = (form.get("full_name") or user.full_name or "").strip()
    user.email = (form.get("email") or "").strip() or None
    user.phone = (form.get("phone") or "").strip() or None
    user.employee_number = (form.get("employee_number") or "").strip() or None
    user.is_staff = parse_bool(form.get("is_staff"))
    user.is_active = parse_bool(form.get("is_active"))
    manager_id = form.get("manager_id", type=int)
    if manager_id and manager_id != getattr(user, "id", None):
        user.manager = session.get(User, manager_id)
    else:
        user.manager = None
    warehouse_id = form.get("assigned_warehouse_id", type=int)
    if warehouse_id:
        user.assigned_warehouse = session.get(Warehouse, warehouse_id)
    else:
        user.assigned_warehouse = None

    role_ids = []
    for role_value in form.getlist("roles"):
        try:
            role_id = int(role_value)
        except (TypeError, ValueError):
            continue
        role_ids.append(role_id)
    user.roles = []
    for role_id in role_ids:
        role = session.get(Role, role_id)
        if role:
            user.roles.append(role)

    selected_sps = form.getlist("service_points")
    if selected_sps:
        user.service_points = [
            session.get(ServicePoint, int(sp_id)) for sp_id in selected_sps if sp_id
        ]
    else:
        user.service_points = []


@admin_bp.route("")
@admin_bp.route("/")
def panel():
    require_admin()
    session = g.db
    stats = {
        "products_count": session.query(func.count(Product.id)).scalar() or 0,
        "users_count": session.query(func.count(User.id)).scalar() or 0,
    }
    top_products = (
        session.query(Product)
        .order_by(Product.is_active.desc(), Product.name)
        .limit(5)
        .all()
    )
    top_users = (
        session.query(User)
        .order_by(User.full_name)
        .limit(4)
        .all()
    )
    integration_services = [
        {"name": "ERP Sync v2", "status": "Online", "last_synced": "16:12", "latency": "120ms"},
        {"name": "Inventory Webhook", "status": "Idle", "last_synced": "13:08", "latency": "--"},
    ]
    return render_template(
        "admin_panel.html",
        stats=stats,
        top_products=top_products,
        top_users=top_users,
        integration_services=integration_services,
        tab="dashboard",
    )


@admin_bp.route("/products")
def products():
    require_admin()
    session = g.db
    page = max(request.args.get("page", type=int, default=1), 1)
    per_page = 25

    query, search, category, brand, status = _get_products_query(request)
    total = query.count()

    products_list = (
        query.order_by(Product.primary_group, Product.name)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    categories = (
        session.query(Product.primary_group)
        .filter(Product.primary_group.isnot(None))
        .distinct()
        .order_by(Product.primary_group)
        .all()
    )
    brands = (
        session.query(Product.brand)
        .filter(Product.brand.isnot(None))
        .distinct()
        .order_by(Product.brand)
        .all()
    )

    stats = {
        "products_count": session.query(func.count(Product.id)).scalar() or 0,
        "users_count": session.query(func.count(User.id)).scalar() or 0,
    }

    return render_template(
        "admin_products.html",
        products=products_list,
        search=search,
        category_filter=category,
        brand=brand,
        status_filter=status,
        page=page,
        per_page=per_page,
        total=total,
        category_options=[row[0] for row in categories if row[0]],
        brands=[row[0] for row in brands if row[0]],
        stats=stats,
        tab="products",
    )


@admin_bp.route("/products/<int:product_id>", methods=["GET"])
def product_detail(product_id):
    require_admin()
    session = g.db
    product = session.get(Product, product_id)
    if not product:
        return render_template("404.html"), 404
    brands = session.query(Brand).order_by(Brand.name).all()
    categories = session.query(Category).order_by(Category.address).all()
    columns = [(col.name, getattr(product, col.name)) for col in Product.__table__.columns]
    return render_template(
        "admin_product_detail.html",
        product=product,
        brands=brands,
        categories=categories,
        fields=columns,
    )


@admin_bp.route("/products/<int:product_id>/update", methods=["POST"])
def update_product(product_id):
    require_admin()
    session = g.db
    product = session.get(Product, product_id)
    if not product:
        flash("Продуктът не е намерен.", "danger")
        return redirect(url_for(".products"))

    product.name = (request.form.get("name") or product.name).strip()
    brand_id = request.form.get("brand_id", type=int)
    if brand_id:
        brand = session.get(Brand, brand_id)
        if brand:
            product.brand_id = brand.id
            product.brand = brand.name
    else:
        product.brand_id = None

    product.price_unit_1 = parse_float(request.form.get("price_unit_1"))
    product.price_unit_2 = parse_float(request.form.get("price_unit_2"))
    product.short_description = request.form.get("short_description") or product.short_description
    product.long_description = request.form.get("long_description") or product.long_description
    product.meta_title = request.form.get("meta_title") or product.meta_title
    product.meta_description = request.form.get("meta_description") or product.meta_description

    category_id = request.form.get("category_id", type=int)
    if category_id:
        category = session.get(Category, category_id)
        if category:
            product.category_id = category.id
            product.category = category.full_address
            top_category = category
            while top_category.parent:
                top_category = top_category.parent
            product.primary_group = top_category.name
    else:
        product.category_id = None
    product.main_unit = (request.form.get("main_unit") or "").strip() or product.main_unit
    product.secondary_unit = (request.form.get("secondary_unit") or "").strip() or product.secondary_unit
    product.unit_conversion_ratio = parse_float(request.form.get("unit_conversion_ratio"))

    image_value = (request.form.get("image_url") or "").strip()
    if image_value:
        product.image_url = image_value
    barcode_value = (request.form.get("barcode") or "").strip()
    if barcode_value:
        product.barcode = barcode_value

    product.is_active = parse_bool(request.form.get("is_active"))
    product.is_special_offer = parse_bool(request.form.get("is_special_offer"))
    product.in_brochure = parse_bool(request.form.get("in_brochure"))
    product.is_most_viewed = parse_bool(request.form.get("is_most_viewed"))
    product.landing_page_accent = parse_bool(request.form.get("landing_page_accent"))
    product.show_request_button = parse_bool(request.form.get("show_request_button"))
    product.allow_two_unit_sales = parse_bool(request.form.get("allow_two_unit_sales"))
    product.show_in_special_carousel = parse_bool(request.form.get("show_in_special_carousel"))

    try:
        session.commit()
        try:
            service = ProductSearchService(current_app)
            if service.is_enabled() and service.ensure_index():
                service.bulk_index([product])
        except Exception as exc:
            current_app.logger.warning("Elasticsearch update failed: %s", exc)
        flash("Продуктът беше запазен успешно.", "success")
    except Exception as e:
        session.rollback()
        flash(f"Грешка при запис: {str(e)}", "danger")

    return redirect(url_for(".product_detail", product_id=product_id))


@admin_bp.route("/products/create", methods=["POST"])
def create_product():
    require_admin()
    session = g.db
    item_number = (request.form.get("item_number") or "").strip()
    name = (request.form.get("name") or "").strip()
    if not item_number or not name:
        flash("Item number and name are required.", "warning")
        return redirect(url_for(".products"))
    existing = session.query(Product).filter_by(item_number=item_number).first()
    if existing:
        flash("Product with that item number already exists.", "warning")
        return redirect(url_for(".products"))
    product = Product(
        item_number=item_number,
        name=name,
        brand=(request.form.get("brand") or "").strip() or None,
        price_unit_1=parse_float(request.form.get("price_unit_1")),
        is_active=parse_bool(request.form.get("is_active")),
    )
    product.in_brochure = parse_bool(request.form.get("in_brochure"))
    product.is_most_viewed = parse_bool(request.form.get("is_most_viewed"))
    product.landing_page_accent = parse_bool(request.form.get("landing_page_accent"))
    product.show_request_button = parse_bool(request.form.get("show_request_button"))
    product.allow_two_unit_sales = parse_bool(request.form.get("allow_two_unit_sales"))
    product.show_in_special_carousel = parse_bool(request.form.get("show_in_special_carousel"))
    session.add(product)
    try:
        session.commit()
        try:
            service = ProductSearchService(current_app)
            if service.is_enabled() and service.ensure_index():
                service.bulk_index([product])
        except Exception as exc:
            current_app.logger.warning("Elasticsearch update failed: %s", exc)
        flash(f"{product.name} was created successfully.", "success")
        return redirect(url_for(".product_detail", product_id=product.id))
    except Exception:
        session.rollback()
        flash("Unable to create product.", "danger")
        return redirect(url_for(".products"))


@admin_bp.route("/categories", methods=["GET", "POST"])
def categories_panel():
    require_admin()
    session = g.db
    ensure_catalog_entries_for_products(session)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        parent_id = request.form.get("parent_id", type=int)
        description = (request.form.get("description") or "").strip()
        meta_title = (request.form.get("meta_title") or "").strip() or None
        meta_description = (request.form.get("meta_description") or "").strip() or None
        canonical_url = (request.form.get("canonical_url") or "").strip() or None
        image_url = (request.form.get("image_url") or "").strip() or None
        image_url = (request.form.get("image_url") or "").strip() or None

        if name:
            existing = session.query(Category).filter(func.lower(Category.name) == name.lower()).first()
            if existing:
                flash("Category name already exists.", "warning")
                return redirect(url_for(".categories_panel"))

            parent = session.get(Category, parent_id) if parent_id else None
            slug_base = slugify(name) or "category"
            slug_value = unique_slug(session, Category, slug_base)
            level = (parent.level if parent else 0) + 1
            address = hierarchical_address(slug_value, parent.address if parent else None)
            category = Category(
                name=name,
                slug=slug_value,
                parent=parent,
                level=level,
                address=address,
                description=description or None,
                meta_title=meta_title,
                meta_description=meta_description,
                canonical_url=canonical_url,
                image_url=image_url,
            )
            session.add(category)
            session.commit()
            flash("Category added successfully.", "success")
        return redirect(url_for(".categories_panel"))

    page = max(request.args.get("page", type=int, default=1), 1)
    per_page = 50
    total_categories = session.query(func.count(Category.id)).scalar() or 0
    total_pages = max((total_categories + per_page - 1) // per_page, 1)
    page = min(page, total_pages)

    categories = (
        session.query(Category)
        .options(joinedload(Category.parent))
        .order_by(Category.address)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    parent_options = (
        session.query(Category)
        .options(load_only(Category.id, Category.name, Category.level, Category.parent_id))
        .order_by(Category.address)
        .all()
    )
    category_ids = [category.id for category in categories]
    category_product_counts = {}
    if category_ids:
        category_product_counts = dict(
            session.query(Product.category_id, func.count(Product.id))
            .filter(Product.category_id.in_(category_ids))
            .group_by(Product.category_id)
            .all()
        )
    edit_id = request.args.get("edit_id", type=int)
    edit_category = session.get(Category, edit_id) if edit_id else None
    stats = {
        "products_count": session.query(func.count(Product.id)).scalar() or 0,
        "users_count": session.query(func.count(User.id)).scalar() or 0,
    }
    return render_template(
        "admin_categories.html",
        categories=categories,
        total_categories=total_categories,
        category_product_counts=category_product_counts,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        stats=stats,
        parent_options=parent_options,
        edit_category=edit_category,
    )


@admin_bp.route("/categories/<int:category_id>/update", methods=["POST"])
def update_category(category_id):
    require_admin()
    session = g.db
    category = session.get(Category, category_id)
    if not category:
        return render_template("404.html"), 404
    name = (request.form.get("name") or "").strip()
    parent_id = request.form.get("parent_id", type=int)
    description = (request.form.get("description") or "").strip()
    meta_title = (request.form.get("meta_title") or "").strip() or None
    meta_description = (request.form.get("meta_description") or "").strip() or None
    canonical_url = (request.form.get("canonical_url") or "").strip() or None
    image_url = (request.form.get("image_url") or "").strip() or None
    if not name:
        flash("Please provide a category name.", "warning")
        return redirect(url_for(".categories_panel", edit_id=category_id))
    existing = (
        session.query(Category)
        .filter(func.lower(Category.name) == name.lower(), Category.id != category.id)
        .first()
    )
    if existing:
        flash("Category name already exists.", "warning")
        return redirect(url_for(".categories_panel", edit_id=category_id))
    parent = session.get(Category, parent_id) if parent_id else None
    node = parent
    while node:
        if node.id == category.id:
            flash("Cannot make a category its own ancestor.", "warning")
            return redirect(url_for(".categories_panel", edit_id=category_id))
        node = node.parent
    old_name = category.name
    category.name = name
    slug_base = slugify(name) or "category"
    if old_name != name:
        category.slug = unique_slug(session, Category, slug_base, exclude_id=category.id)
    category.parent = parent
    category.description = description or None
    category.meta_title = meta_title
    category.meta_description = meta_description
    category.canonical_url = canonical_url
    category.image_url = image_url
    _refresh_category_tree(category)
    session.commit()
    flash("Category updated successfully.", "success")
    return redirect(url_for(".categories_panel"))


@admin_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
def delete_category(category_id):
    require_admin()
    session = g.db
    category = session.get(Category, category_id)
    if not category:
        return render_template("404.html"), 404
    category_ids = _collect_category_ids(category)
    product_count = (
        session.query(func.count(Product.id)).filter(Product.category_id.in_(category_ids)).scalar()
        or 0
    )
    if product_count:
        flash(
            f"Категорията съдържа {product_count} продукта. Преместете/изтрийте продуктите преди да изтриете категорията.",
            "warning",
        )
        return redirect(url_for(".categories_panel", edit_id=category_id))

    session.delete(category)
    session.commit()
    flash("Категорията и нейният клон бяха изтрити.", "success")
    return redirect(url_for(".categories_panel"))


@admin_bp.route("/brands", methods=["GET", "POST"])
def brands_panel():
    require_admin()
    session = g.db
    ensure_catalog_entries_for_products(session)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        image_url = (request.form.get("image_url") or "").strip()
        if name:
            existing = session.query(Brand).filter(func.lower(Brand.name) == name.lower()).first()
            if existing:
                flash("Brand already exists.", "warning")
                return redirect(url_for(".brands_panel"))
            slug_base = slugify(name) or "brand"
            slug_value = unique_slug(session, Brand, slug_base)
            brand = Brand(
                name=name,
                slug=slug_value,
                description=description or None,
                image_url=image_url or None,
            )
            session.add(brand)
            session.commit()
            flash("Brand saved.", "success")
        return redirect(url_for(".brands_panel"))
    page = max(request.args.get("page", type=int, default=1), 1)
    per_page = 50
    total_brands = session.query(func.count(Brand.id)).scalar() or 0
    total_pages = max((total_brands + per_page - 1) // per_page, 1)
    page = min(page, total_pages)

    brands = (
        session.query(Brand)
        .order_by(Brand.name)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    brand_ids = [brand.id for brand in brands]
    brand_product_counts = {}
    if brand_ids:
        brand_product_counts = dict(
            session.query(Product.brand_id, func.count(Product.id))
            .filter(Product.brand_id.in_(brand_ids))
            .group_by(Product.brand_id)
            .all()
        )
    stats = {
        "products_count": session.query(func.count(Product.id)).scalar() or 0,
        "users_count": session.query(func.count(User.id)).scalar() or 0,
    }
    return render_template(
        "admin_brands.html",
        brands=brands,
        brand_product_counts=brand_product_counts,
        total_brands=total_brands,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        stats=stats,
    )


@admin_bp.route("/users")
def users_panel():
    require_admin()
    session = g.db
    users = (
        session.query(User)
        .options(joinedload(User.service_points))
        .order_by(User.full_name)
        .all()
    )
    stats = {
        "products_count": session.query(func.count(Product.id)).scalar() or 0,
        "users_count": session.query(func.count(User.id)).scalar() or 0,
    }
    return render_template("admin_users.html", users=users, stats=stats)


@admin_bp.route("/users/new", methods=["GET", "POST"])
def user_create():
    require_admin()
    session = g.db
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        full_name = (request.form.get("full_name") or "").strip()
        password = (request.form.get("password") or "").strip()
        if not username or not full_name or not password:
            flash("Всички полета са задължителни.", "warning")
            return redirect(url_for(".user_create"))
        if session.query(User).filter(func.lower(User.username) == username).first():
            flash("Ползвател с това име вече съществува.", "warning")
            return redirect(url_for(".user_create"))
        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=parse_bool(request.form.get("is_admin")),
            can_assign_orders=parse_bool(request.form.get("can_assign_orders")),
            can_prepare_orders=parse_bool(request.form.get("can_prepare_orders")),
            can_view_competitor_prices=parse_bool(request.form.get("can_view_competitor_prices")),
        )
        _apply_user_form_values(user, request.form, session)
        session.add(user)
        session.commit()
        flash("Потребителят е създаден.", "success")
        return redirect(url_for(".users_panel"))
    context = _user_form_options(session)
    return render_template("admin_user_detail.html", user=None, **context)


@admin_bp.route("/users/<int:user_id>", methods=["GET"])
def user_detail(user_id):
    require_admin()
    session = g.db
    user = session.get(User, user_id)
    if not user:
        return render_template("404.html"), 404
    context = _user_form_options(session)
    return render_template("admin_user_detail.html", user=user, **context)


@admin_bp.route("/users/<int:user_id>/update", methods=["POST"])
def update_user(user_id):
    require_admin()
    session = g.db
    user = session.get(User, user_id)
    if not user:
        return render_template("404.html"), 404
    user.is_admin = parse_bool(request.form.get("is_admin"))
    user.can_assign_orders = parse_bool(request.form.get("can_assign_orders"))
    user.can_prepare_orders = parse_bool(request.form.get("can_prepare_orders"))
    user.can_view_competitor_prices = parse_bool(request.form.get("can_view_competitor_prices"))
    if request.form.get("password"):
        user.password_hash = generate_password_hash(request.form.get("password"))
    _apply_user_form_values(user, request.form, session)
    session.commit()
    flash("Потребителят е обновен.", "success")
    return redirect(url_for(".user_detail", user_id=user_id))


@admin_bp.route("/users/<int:user_id>/password", methods=["POST"])
def reset_user_password(user_id):
    require_admin()
    session = g.db
    user = session.get(User, user_id)
    if not user:
        return render_template("404.html"), 404
    new_password = (request.form.get("password") or "").strip()
    if not new_password:
        flash("Моля въведете нова парола.", "warning")
        return redirect(url_for(".users_panel"))
    user.password_hash = generate_password_hash(new_password)
    session.commit()
    flash(f"Паролата за {user.full_name} беше ресетната.", "success")
    return redirect(url_for(".users_panel"))


@admin_bp.route("/access-windows")
def access_windows():
    require_admin()
    session = g.db
    windows = session.query(AccessWindow).order_by(AccessWindow.name).all()
    context = _access_window_form_options(session)
    return render_template("admin_access_windows.html", windows=windows, **context)


@admin_bp.route("/access-windows", methods=["POST"])
def create_access_window():
    require_admin()
    session = g.db
    window = AccessWindow(
        name="",
        start_time=datetime.utcnow().time(),
        end_time=datetime.utcnow().time(),
    )
    _apply_access_window_form(window, request.form, session)
    session.add(window)
    session.commit()
    flash("Новото ограничение беше добавено.", "success")
    return redirect(url_for(".access_windows"))


@admin_bp.route("/access-windows/<int:window_id>", methods=["GET", "POST"])
def access_window_detail(window_id):
    require_admin()
    session = g.db
    window = session.get(AccessWindow, window_id)
    if not window:
        return render_template("404.html"), 404
    if request.method == "POST":
        _apply_access_window_form(window, request.form, session)
        session.commit()
        flash("Ограничението беше обновено.", "success")
        return redirect(url_for(".access_windows"))
    context = _access_window_form_options(session)
    return render_template("admin_access_window_detail.html", window=window, **context)


@admin_bp.route("/access-windows/<int:window_id>/delete", methods=["POST"])
def delete_access_window(window_id):
    require_admin()
    session = g.db
    window = session.get(AccessWindow, window_id)
    if not window:
        return render_template("404.html"), 404
    session.delete(window)
    session.commit()
    flash("Ограничението беше изтрито.", "success")
    return redirect(url_for(".access_windows"))


@admin_bp.route("/warehouses", methods=["GET", "POST"])
def warehouses_panel():
    require_admin()
    session = g.db
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        code = (request.form.get("code") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        is_active = parse_bool(request.form.get("is_active"))
        if not name or not code:
            flash("Име и код на склада са задължителни.", "warning")
            return redirect(url_for(".warehouses_panel"))
        duplicate = (
            session.query(Warehouse)
            .filter(
                or_(
                    func.lower(Warehouse.name) == name.lower(),
                    Warehouse.code == code,
                )
            )
            .first()
        )
        if duplicate:
            flash("Склад с това име или код вече съществува.", "warning")
            return redirect(url_for(".warehouses_panel"))
        warehouse = Warehouse(name=name, code=code, description=description, is_active=is_active)
        session.add(warehouse)
        session.commit()
        flash("Складът беше добавен.", "success")
        return redirect(url_for(".warehouses_panel"))
    warehouses = session.query(Warehouse).order_by(Warehouse.name).all()
    return render_template("admin_warehouses.html", warehouses=warehouses)


@admin_bp.route("/warehouses/<int:warehouse_id>", methods=["GET", "POST"])
def warehouse_detail(warehouse_id):
    require_admin()
    session = g.db
    warehouse = session.get(Warehouse, warehouse_id)
    if not warehouse:
        return render_template("404.html"), 404
    if request.method == "POST":
        _apply_warehouse_form(warehouse, request.form)
        session.commit()
        flash("Складът беше обновен.", "success")
        return redirect(url_for(".warehouses_panel"))
    return render_template("admin_warehouse_detail.html", warehouse=warehouse)


@admin_bp.route("/warehouses/<int:warehouse_id>/delete", methods=["POST"])
def delete_warehouse(warehouse_id):
    require_admin()
    session = g.db
    warehouse = session.get(Warehouse, warehouse_id)
    if not warehouse:
        return render_template("404.html"), 404
    session.delete(warehouse)
    session.commit()
    flash("Складът беше изтрит.", "success")
    return redirect(url_for(".warehouses_panel"))


@admin_bp.route("/warehouses/<int:warehouse_id>/locations", methods=["POST"])
def create_location(warehouse_id):
    require_admin()
    session = g.db
    warehouse = session.get(Warehouse, warehouse_id)
    if not warehouse:
        return render_template("404.html"), 404
    location = Location(
        warehouse=warehouse,
        name="",
        code="",
    )
    _apply_location_form(location, request.form)
    session.add(location)
    session.commit()
    flash("Локацията беше добавена.", "success")
    return redirect(url_for(".warehouse_detail", warehouse_id=warehouse_id))



@admin_bp.route("/printers", methods=["GET", "POST"])
def printers_panel():
    require_admin()
    session = g.db
    warehouses = session.query(Warehouse).order_by(Warehouse.name).all()
    if request.method == "POST":
        warehouse_id = request.form.get("warehouse_id", type=int)
        warehouse = session.get(Warehouse, warehouse_id) if warehouse_id else None
        name = (request.form.get("name") or "").strip()
        ip_address = (request.form.get("ip_address") or "").strip()
        server_url = (request.form.get("server_url") or "").strip() or None
        access_key = (request.form.get("access_key") or "").strip() or None
        description = (request.form.get("description") or "").strip() or None
        is_active = parse_bool(request.form.get("is_active"))
        is_default = parse_bool(request.form.get("is_default"))
        if not warehouse or not ip_address:
            flash("Изберете склад и въведете IP на принтера.", "warning")
            return redirect(url_for(".printers_panel"))
        # Check for duplicate (warehouse_id, ip_address)
        existing = session.query(Printer).filter(
            Printer.warehouse_id == warehouse.id,
            Printer.ip_address == ip_address
        ).first()
        if existing:
            flash("В този склад вече има принтер с този IP адрес!", "danger")
            return redirect(url_for(".printers_panel"))
        printer = Printer(
            warehouse_id=warehouse.id,
            name=name or None,
            ip_address=ip_address,
            server_url=server_url,
            access_key=access_key,
            description=description,
            is_active=is_active,
            is_default=is_default,
        )
        session.add(printer)
        session.flush()
        _set_default_printer(session, printer)
        try:
            session.commit()
            flash("Принтерът е добавен.", "success")
        except Exception as exc:
            session.rollback()
            flash(f"Грешка при запис: {str(exc)}", "danger")
        return redirect(url_for(".printers_panel"))

    printers = (
        session.query(Printer)
        .options(joinedload(Printer.warehouse))
        .order_by(Printer.warehouse_id, Printer.name, Printer.ip_address)
        .all()
    )
    printer_statuses = {}
    for printer in printers:
        try:
            printer_statuses[printer.id] = get_printer_status(printer)
        except Exception as exc:
            printer_statuses[printer.id] = {"online": False, "error": str(exc)}
    return render_template(
        "admin_printers.html",
        printers=printers,
        printer_statuses=printer_statuses,
        warehouses=warehouses,
    )


@admin_bp.route("/printers/<int:printer_id>", methods=["GET", "POST"])
def printer_detail(printer_id):
    require_admin()
    session = g.db
    printer = session.get(Printer, printer_id)
    if not printer:
        return render_template("404.html"), 404
    warehouses = session.query(Warehouse).order_by(Warehouse.name).all()
    if request.method == "POST":
        warehouse_id = request.form.get("warehouse_id", type=int)
        warehouse = session.get(Warehouse, warehouse_id) if warehouse_id else None
        if not warehouse:
            flash("Изберете валиден склад.", "warning")
            return redirect(url_for(".printer_detail", printer_id=printer_id))
        new_ip = (request.form.get("ip_address") or "").strip()
        # Check for duplicate (warehouse_id, ip_address) except self
        existing = session.query(Printer).filter(
            Printer.warehouse_id == warehouse.id,
            Printer.ip_address == new_ip,
            Printer.id != printer.id
        ).first()
        if existing:
            flash("В този склад вече има принтер с този IP адрес!", "danger")
            return redirect(url_for(".printer_detail", printer_id=printer_id))
        printer.warehouse = warehouse
        printer.name = (request.form.get("name") or "").strip() or None
        printer.ip_address = new_ip
        printer.server_url = (request.form.get("server_url") or "").strip() or None
        printer.access_key = (request.form.get("access_key") or "").strip() or None
        printer.description = (request.form.get("description") or "").strip() or None
        printer.is_active = parse_bool(request.form.get("is_active"))
        printer.is_default = parse_bool(request.form.get("is_default"))
        session.flush()
        _set_default_printer(session, printer)
        try:
            session.commit()
            flash("Принтерът е обновен.", "success")
        except Exception as exc:
            session.rollback()
            flash(f"Грешка при запис: {str(exc)}", "danger")
        return redirect(url_for(".printers_panel"))
    return render_template("admin_printer_detail.html", printer=printer, warehouses=warehouses)


@admin_bp.route("/printers/<int:printer_id>/delete", methods=["POST"])
def delete_printer(printer_id):
    require_admin()
    session = g.db
    printer = session.get(Printer, printer_id)
    if not printer:
        return render_template("404.html"), 404
    session.delete(printer)
    session.commit()
    flash("Принтерът е изтрит.", "success")
    return redirect(url_for(".printers_panel"))

@admin_bp.route("/locations/<int:location_id>", methods=["GET", "POST"])
def location_detail(location_id):
    require_admin()
    session = g.db
    location = session.get(Location, location_id)
    if not location:
        return render_template("404.html"), 404
    if request.method == "POST":
        _apply_location_form(location, request.form)
        session.commit()
        flash("Локацията беше обновена.", "success")
        return redirect(url_for(".warehouse_detail", warehouse_id=location.warehouse_id))
    return render_template("admin_location_detail.html", location=location)


@admin_bp.route("/locations/<int:location_id>/delete", methods=["POST"])
def delete_location(location_id):
    require_admin()
    session = g.db
    location = session.get(Location, location_id)
    if not location:
        return render_template("404.html"), 404
    warehouse_id = location.warehouse_id
    session.delete(location)
    session.commit()
    flash("Локацията беше изтрита.", "success")
    return redirect(url_for(".warehouse_detail", warehouse_id=warehouse_id))


@admin_bp.route("/roles", methods=["GET", "POST"])
def roles_panel():
    require_admin()
    session = g.db
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        is_active = parse_bool(request.form.get("is_active"))
        if not name:
            flash("Името на ролята е задължително.", "warning")
            return redirect(url_for(".roles_panel"))
        existing = (
            session.query(Role)
            .filter(func.lower(Role.name) == name.lower())
            .first()
        )
        if existing:
            flash("Роля с това име вече съществува.", "warning")
            return redirect(url_for(".roles_panel"))
        slug_value = unique_slug(session, Role, slugify(name) or "role")
        role = Role(name=name, slug=slug_value, description=description, is_active=is_active)
        session.add(role)
        session.commit()
        flash("Новата роля беше добавена.", "success")
        return redirect(url_for(".roles_panel"))
    roles = session.query(Role).order_by(Role.name).all()
    return render_template("admin_roles.html", roles=roles)


@admin_bp.route("/roles/<int:role_id>", methods=["GET", "POST"])
def role_detail(role_id):
    require_admin()
    session = g.db
    role = session.get(Role, role_id)
    if not role:
        return render_template("404.html"), 404
    if request.method == "POST":
        new_name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        is_active = parse_bool(request.form.get("is_active"))
        if not new_name:
            flash("Името на ролята е задължително.", "warning")
            return redirect(url_for(".role_detail", role_id=role_id))
        duplicate = (
            session.query(Role)
            .filter(func.lower(Role.name) == new_name.lower(), Role.id != role.id)
            .first()
        )
        if duplicate:
            flash("Друга роля вече използва това име.", "warning")
            return redirect(url_for(".role_detail", role_id=role_id))
        role.name = new_name
        if not role.slug:
            role.slug = slugify(new_name) or "role"
        role.description = description
        role.is_active = is_active
        session.commit()
        flash("Ролята беше обновена.", "success")
        return redirect(url_for(".roles_panel"))
    return render_template("admin_role_detail.html", role=role)


@admin_bp.route("/erp")
def erp_panel():
    require_admin()
    services = [
        {"name": "ERP Sync v2", "status": "Online", "last_synced": "16:12", "latency": "120ms"},
        {"name": "Inventory Webhook", "status": "Idle", "last_synced": "13:08", "latency": "--"},
        {"name": "PPD Document Export", "status": "Healthy", "last_synced": "15:45", "latency": "200ms"},
    ]
    return render_template("admin_erp.html", services=services)


@admin_bp.route("/sync-center")
def sync_center():
    require_admin()
    session = g.db
    total_products = session.query(func.count(Product.id)).scalar() or 0
    last_success = (
        session.query(SyncLog)
        .filter(SyncLog.status == "SUCCESS")
        .order_by(SyncLog.completed_at.desc(), SyncLog.started_at.desc())
        .first()
    )
    last_log = session.query(SyncLog).order_by(SyncLog.started_at.desc()).first()
    last_status = last_log.status if last_log else "N/A"
    last_status_badge = _status_badge(last_status)

    history = (
        session.query(SyncLog)
        .order_by(SyncLog.started_at.desc())
        .limit(20)
        .all()
    )
    history_rows = []
    for entry in history:
        history_rows.append(
            {
                "id": entry.id,
                "started_at": entry.started_at,
                "duration": _format_duration(entry.started_at, entry.completed_at),
                "total_fetched": entry.total_fetched or 0,
                "created_count": entry.created_count or 0,
                "updated_count": entry.updated_count or 0,
                "status": entry.status,
            }
        )

    pricemind_last = (
        session.query(PricemindSyncLog)
        .order_by(PricemindSyncLog.started_at.desc())
        .first()
    )
    pricemind_rows = []
    if pricemind_last and pricemind_last.status == "SUCCESS":
        latest_snapshots = (
            session.query(PricemindSnapshot)
            .filter(PricemindSnapshot.sync_log_id == pricemind_last.id)
            .order_by(PricemindSnapshot.id.desc())
            .limit(30)
            .all()
        )
        for snap in latest_snapshots:
            pricemind_rows.append(
                {
                    "sku": snap.sku,
                    "title": snap.title,
                    "my_price": snap.my_price,
                    "lowest_price": snap.lowest_price,
                    "lowest_competitor": snap.lowest_price_competitor,
                    "is_matched": bool(snap.product_id),
                }
            )

    return render_template(
        "admin/sync_center.html",
        total_products=total_products,
        last_success_ago=_time_ago(last_success.completed_at) if last_success else "Never",
        last_status=last_status,
        last_status_badge=last_status_badge,
        history_rows=history_rows,
        pricemind_last=pricemind_last,
        pricemind_rows=pricemind_rows,
    )


@admin_bp.route("/sync/trigger", methods=["POST"])
def trigger_sync():
    require_admin()
    session = g.db
    user = getattr(g, "current_user", None)
    triggered_by = "Admin User"
    if user and getattr(user, "is_authenticated", False):
        triggered_by = user.full_name or user.username or triggered_by

    service = ProductSyncService(session)
    apply_to_catalog = current_app.config.get("NOMEN_SYNC_APPLY_TO_CATALOG", True)
    deactivate_missing = current_app.config.get("NOMEN_SYNC_DEACTIVATE_MISSING", True)
    single_id = (current_app.config.get("NOMEN_API_SINGLE_ID") or "").strip()
    if single_id:
        deactivate_missing = False
    nomen_log = service.run_sync(
        triggered_by=triggered_by,
        apply_to_catalog=apply_to_catalog,
        deactivate_missing=deactivate_missing,
    )

    feed_log = None
    feed_enabled = current_app.config.get("FB_FEED_SYNC_ENABLED", True)
    if feed_enabled:
        feed_service = ProductFeedSyncService(session)
        feed_log = feed_service.run_sync(triggered_by=f"{triggered_by} (FB Feed)")

    messages = []
    overall_success = True
    if nomen_log and nomen_log.status == "SUCCESS":
        messages.append(
            "ERP Sync completed. "
            f"Fetched {nomen_log.total_fetched or 0}, "
            f"created {nomen_log.created_count or 0}, "
            f"updated {nomen_log.updated_count or 0}."
        )
    else:
        overall_success = False
        messages.append("ERP Sync failed. Check the sync history for details.")

    if feed_enabled:
        if feed_log and feed_log.status == "SUCCESS":
            messages.append(
                "FB feed sync completed. "
                f"Fetched {feed_log.total_fetched or 0}, "
                f"updated {feed_log.updated_count or 0}."
            )
        else:
            overall_success = False
            messages.append("FB feed sync failed. Check the sync history for details.")

    flash(" ".join(messages), "success" if overall_success else "danger")
    return redirect(url_for(".sync_center"))


@admin_bp.route("/pricemind/trigger", methods=["POST"])
def trigger_pricemind_sync():
    require_admin()
    session = g.db
    user = getattr(g, "current_user", None)
    triggered_by = "Admin User"
    if user and getattr(user, "is_authenticated", False):
        triggered_by = user.full_name or user.username or triggered_by

    service = PricemindSyncService(session)
    log = service.run_sync(triggered_by=triggered_by)
    if log and log.status == "SUCCESS":
        flash(
            (
                "Pricemind sync completed. "
                f"Fetched {log.total_rows or 0}, "
                f"matched {log.matched_count or 0}, "
                f"unmatched {log.unmatched_count or 0}."
            ),
            "success",
        )
    else:
        flash("Pricemind sync failed. Check the sync history for details.", "danger")
    return redirect(url_for(".sync_center"))


@admin_bp.route("/pricemind/snapshots")
def pricemind_snapshots():
    require_admin()
    session = g.db
    sku_query = (request.args.get("sku") or "").strip()
    competitor_query = (request.args.get("competitor") or "").strip()
    unmatched_only = parse_bool(request.args.get("unmatched"))
    page = request.args.get("page", 1, type=int)
    per_page = 50

    last_log = (
        session.query(PricemindSyncLog)
        .filter(PricemindSyncLog.status == "SUCCESS")
        .order_by(PricemindSyncLog.started_at.desc())
        .first()
    )
    if not last_log:
        return render_template(
            "admin/pricemind_snapshots.html",
            rows=[],
            page=page,
            per_page=per_page,
            total=0,
            sku_query=sku_query,
            competitor_query=competitor_query,
            unmatched_only=unmatched_only,
        )

    query = session.query(PricemindSnapshot).filter(
        PricemindSnapshot.sync_log_id == last_log.id
    )
    if sku_query:
        query = query.filter(PricemindSnapshot.sku.ilike(f"%{sku_query}%"))
    if unmatched_only:
        query = query.filter(PricemindSnapshot.product_id.is_(None))
    if competitor_query:
        query = query.join(PricemindCompetitorPrice).filter(
            PricemindCompetitorPrice.competitor.ilike(f"%{competitor_query}%")
        )

    total = query.order_by(None).count()
    rows = (
        query.order_by(PricemindSnapshot.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return render_template(
        "admin/pricemind_snapshots.html",
        rows=rows,
        page=page,
        per_page=per_page,
        total=total,
        sku_query=sku_query,
        competitor_query=competitor_query,
        unmatched_only=unmatched_only,
    )
