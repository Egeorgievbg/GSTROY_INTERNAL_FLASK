import qrcode
from datetime import datetime
from io import BytesIO

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from reportlab.lib.pagesizes import A6, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from constants import DEFAULT_PRODUCT_IMAGE, PDF_FONT_NAME
from models import Product, ProductList, ProductListItem, TransferDocument
from utils import (
    calculate_list_totals,
    canonical_unit_name,
    default_warehouse_for_user,
    generate_list_code,
    generate_pallet_code,
    generate_transfer_code,
    load_warehouses,
    parse_float,
    user_with_default_warehouse,
)


logistics_bp = Blueprint("logistics", __name__)


def _get_product_by_number(session, item_number):
    code = (item_number or "").strip()
    if not code:
        return None
    return (
        session.query(Product)
        .filter(func.upper(Product.item_number) == code.upper())
        .first()
    )


def _resolve_unit_label(product, unit_mode):
    if not product:
        return "pcs"
    mode = (unit_mode or "").lower()

    def _match(mode_name):
        for candidate in (product.main_unit, product.secondary_unit):
            if canonical_unit_name(candidate) == mode_name:
                return candidate
        return None

    if mode == "packages":
        return _match("packages") or _match("pieces") or product.main_unit or product.secondary_unit or "pcs"
    if mode in {"pieces", "pieces_from_packages"}:
        return _match("pieces") or product.main_unit or product.secondary_unit or "pcs"
    return product.main_unit or product.secondary_unit or "pcs"


def _collect_items_from_form():
    item_numbers = request.form.getlist("item_number[]")
    if not item_numbers:
        raise ValueError("Add at least one item to the list.")
    quantities = request.form.getlist("quantity[]")
    unit_modes = request.form.getlist("unit_mode[]")
    rows = []
    for idx, raw_code in enumerate(item_numbers):
        code = (raw_code or "").strip()
        if not code:
            continue
        qty_raw = quantities[idx] if idx < len(quantities) else None
        qty = parse_float(qty_raw)
        if qty is None or qty <= 0:
            raise ValueError(f"Quantity for {code} must be greater than zero.")
        rows.append(
            {
                "item_number": code,
                "quantity": qty,
                "unit_mode": unit_modes[idx] if idx < len(unit_modes) else None,
            }
        )
    if not rows:
        raise ValueError("Add at least one item to the list.")
    return rows


def _populate_list_from_form(product_list, rows):
    session = g.db
    product_list.items.clear()
    for row in rows:
        product = _get_product_by_number(session, row["item_number"])
        if not product:
            raise ValueError(f"Product {row['item_number']} was not found.")
        unit_label = _resolve_unit_label(product, row.get("unit_mode"))
        product_list.items.append(
            ProductListItem(product=product, quantity=row["quantity"], unit=unit_label)
        )


def _populate_simple_list(product_list, rows):
    _populate_list_from_form(product_list, rows)


@logistics_bp.route("/lists")
@logistics_bp.route("/pallets")
def pallet_list():
    session = g.db
    view_mode = request.args.get("view", "cards")
    if view_mode not in ("cards", "table"):
        view_mode = "cards"
    base_args = request.args.to_dict()
    base_args.pop("view", None)
    cards_url = url_for(".pallet_list", **{**base_args, "view": "cards"})
    table_url = url_for(".pallet_list", **{**base_args, "view": "table"})
    product_lists = (
        session.query(ProductList)
        .options(
            joinedload(ProductList.current_warehouse),
            joinedload(ProductList.target_warehouse),
            joinedload(ProductList.items).joinedload(ProductListItem.product),
        )
        .filter(ProductList.is_light.is_(False))
        .order_by(ProductList.created_at.desc())
        .all()
    )
    totals_by_list = {p.id: calculate_list_totals(p) for p in product_lists}
    return render_template(
        "pallet_list.html",
        product_lists=product_lists,
        totals_by_list=totals_by_list,
        view_mode=view_mode,
        cards_url=cards_url,
        table_url=table_url,
    )


@logistics_bp.route("/lists/new")
@logistics_bp.route("/pallets/new")
def new_pallet():
    session = g.db
    warehouses = load_warehouses(session)
    product_options = session.query(Product).order_by(Product.item_number).all()
    return render_template(
        "pallet_new.html",
        warehouses=warehouses,
        product_options=product_options,
        product_list=None,
        form_action=url_for(".create_list"),
        existing_items=[],
    )


@logistics_bp.route("/simple-lists")
@login_required
def simple_list_index():
    session = g.db
    view_mode = request.args.get("view", "cards")
    if view_mode not in ("cards", "table"):
        view_mode = "cards"
    base_args = request.args.to_dict()
    base_args.pop("view", None)
    cards_url = url_for(".simple_list_index", **{**base_args, "view": "cards"})
    table_url = url_for(".simple_list_index", **{**base_args, "view": "table"})
    product_lists = (
        session.query(ProductList)
        .options(
            joinedload(ProductList.current_warehouse),
            joinedload(ProductList.created_by),
            joinedload(ProductList.items).joinedload(ProductListItem.product),
        )
        .filter(ProductList.is_light.is_(True))
        .order_by(ProductList.created_at.desc())
        .all()
    )
    totals_by_list = {p.id: calculate_list_totals(p) for p in product_lists}
    return render_template(
        "simple_list_index.html",
        product_lists=product_lists,
        totals_by_list=totals_by_list,
        view_mode=view_mode,
        cards_url=cards_url,
        table_url=table_url,
    )


@logistics_bp.route("/simple-lists/new")
@login_required
def simple_list_new():
    user = user_with_default_warehouse(current_user)
    warehouse = default_warehouse_for_user(current_user)
    if not user or not warehouse:
        flash("Assign a default warehouse before creating simple lists.", "warning")
        return redirect(url_for(".pallet_list"))
    return render_template(
        "simple_list_form.html",
        product_list=None,
        existing_items=[],
        user_warehouse=warehouse,
        form_action=url_for(".simple_list_create"),
    )


@logistics_bp.route("/simple-lists", methods=["POST"])
@login_required
def simple_list_create():
    session = g.db
    user = user_with_default_warehouse(current_user)
    if not user:
        flash("Assign a default warehouse before creating simple lists.", "warning")
        return redirect(url_for(".simple_list_new"))
    try:
        rows = _collect_items_from_form()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for(".simple_list_new"))
    product_list = ProductList(
        code=generate_list_code(session),
        status="draft",
        is_light=True,
        current_warehouse_id=user.default_warehouse_id,
        created_by_id=user.id if user.id else None,
    )
    session.add(product_list)
    try:
        _populate_simple_list(product_list, rows)
    except ValueError as exc:
        session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for(".simple_list_new"))
    session.commit()
    flash("Simple list saved.", "success")
    return redirect(
        url_for(".simple_list_detail", list_id=product_list.id, created="1")
    )


@logistics_bp.route("/simple-lists/<int:list_id>/edit")
@login_required
def simple_list_edit(list_id):
    session = g.db
    product_list = (
        session.query(ProductList)
        .options(joinedload(ProductList.items).joinedload(ProductListItem.product))
        .filter(ProductList.id == list_id, ProductList.is_light.is_(True))
        .first()
    )
    if product_list is None:
        abort(404)
    user = user_with_default_warehouse(current_user)
    warehouse = default_warehouse_for_user(current_user)
    if not user or not warehouse:
        flash("Assign a default warehouse before editing lists.", "warning")
        return redirect(url_for(".simple_list_index"))
    existing_items = [
        {
            "item_number": item.product.item_number,
            "quantity": item.quantity,
            "name": item.product.name,
            "brand": item.product.brand,
            "unit": item.unit,
            "storage_location": item.product.storage_location,
            "unit_mode": canonical_unit_name(item.unit) or "manual",
            "image_url": (
                url_for("static", filename=item.product.image_url.lstrip("/"))
                if item.product.image_url
                and item.product.image_url != DEFAULT_PRODUCT_IMAGE
                else None
            ),
        }
        for item in product_list.items
    ]
    return render_template(
        "simple_list_form.html",
        product_list=product_list,
        existing_items=existing_items,
        user_warehouse=warehouse,
        form_action=url_for(".simple_list_update", list_id=list_id),
    )


@logistics_bp.route("/simple-lists/<int:list_id>/update", methods=["POST"])
@login_required
def simple_list_update(list_id):
    session = g.db
    product_list = (
        session.query(ProductList)
        .options(joinedload(ProductList.items))
        .filter(ProductList.id == list_id, ProductList.is_light.is_(True))
        .first()
    )
    if product_list is None:
        abort(404)
    try:
        rows = _collect_items_from_form()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for(".simple_list_edit", list_id=list_id))
    try:
        _populate_simple_list(product_list, rows)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for(".simple_list_edit", list_id=list_id))
    session.commit()
    flash("Simple list updated.", "success")
    return redirect(url_for(".simple_list_detail", list_id=list_id))


@logistics_bp.route("/simple-lists/<int:list_id>")
@login_required
def simple_list_detail(list_id):
    session = g.db
    product_list = (
        session.query(ProductList)
        .options(
            joinedload(ProductList.current_warehouse),
            joinedload(ProductList.created_by),
            joinedload(ProductList.items).joinedload(ProductListItem.product),
        )
        .filter(ProductList.id == list_id, ProductList.is_light.is_(True))
        .first()
    )
    if product_list is None:
        abort(404)
    totals = calculate_list_totals(product_list)
    show_summary = request.args.get("created") == "1"
    return render_template(
        "simple_list_detail.html",
        product_list=product_list,
        totals=totals,
        show_summary=show_summary,
    )


@logistics_bp.route("/lists", methods=["POST"])
@logistics_bp.route("/pallets", methods=["POST"])
def create_list():
    session = g.db
    if not request.form.get("source_warehouse_id"):
        flash("Please supply a source warehouse.", "warning")
        return redirect(url_for(".new_pallet"))
    target_raw = request.form.get("destination_warehouse_id")
    product_list = ProductList(
        code=generate_list_code(session),
        title=request.form.get("title") or "Stock list",
        current_warehouse_id=int(request.form.get("source_warehouse_id")),
        target_warehouse_id=int(target_raw) if target_raw else None,
        status="draft",
        created_by_id=current_user.id if current_user.is_authenticated else None,
    )
    session.add(product_list)
    try:
        rows = _collect_items_from_form()
    except ValueError as exc:
        session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for(".new_pallet"))
    _populate_list_from_form(product_list, rows)
    if product_list.target_warehouse_id:
        transfer = TransferDocument(
            list_id=product_list.id,
            code=generate_transfer_code(session),
            from_warehouse_id=product_list.current_warehouse_id,
            to_warehouse_id=product_list.target_warehouse_id,
            status="in_transit",
            shipped_at=datetime.utcnow(),
        )
        session.add(transfer)
        product_list.status = "in_transit"
    session.commit()
    flash("List created successfully.")
    return redirect(url_for(".pallet_detail", list_id=product_list.id))


@logistics_bp.route("/lists/<int:list_id>/edit")
def edit_list(list_id):
    session = g.db
    product_list = session.get(ProductList, list_id)
    if product_list is None:
        abort(404)
    warehouses = load_warehouses(session)
    product_options = session.query(Product).order_by(Product.item_number).all()
    existing_items = [
        {"item_number": item.product.item_number, "quantity": item.quantity, "name": item.product.name}
        for item in product_list.items
    ]
    return render_template(
        "pallet_new.html",
        warehouses=warehouses,
        product_options=product_options,
        product_list=product_list,
        form_action=url_for(".update_list", list_id=list_id),
        existing_items=existing_items,
    )


@logistics_bp.route("/lists/<int:list_id>/update", methods=["POST"])
def update_list(list_id):
    session = g.db
    product_list = session.query(ProductList).options(joinedload(ProductList.items)).get(list_id)
    if product_list is None:
        abort(404)
    if not request.form.get("source_warehouse_id"):
        flash("Please supply a source warehouse.", "warning")
        return redirect(url_for(".edit_list", list_id=list_id))
    try:
        rows = _collect_items_from_form()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for(".edit_list", list_id=list_id))
    _populate_list_from_form(product_list, rows)
    if product_list.status == "received":
        product_list.status = "draft"
    session.commit()
    flash("List updated successfully.", "success")
    return redirect(url_for(".pallet_detail", list_id=list_id))


@logistics_bp.route("/lists/<int:list_id>")
@logistics_bp.route("/pallets/<int:list_id>")
def pallet_detail(list_id):
    session = g.db
    product_list = (
        session.query(ProductList)
        .options(
            joinedload(ProductList.current_warehouse),
            joinedload(ProductList.target_warehouse),
            joinedload(ProductList.items).joinedload(ProductListItem.product),
            joinedload(ProductList.transfers).joinedload(TransferDocument.from_warehouse),
            joinedload(ProductList.transfers).joinedload(TransferDocument.to_warehouse),
            joinedload(ProductList.created_by),
        )
        .get(list_id)
    )
    if product_list is None:
        abort(404)
    if product_list.is_light:
        return redirect(url_for(".simple_list_detail", list_id=list_id))
    active_transfer = next((t for t in product_list.transfers if t.status == "in_transit"), None)
    return render_template(
        "pallet_detail.html",
        product_list=product_list,
        active_transfer=active_transfer,
        totals=calculate_list_totals(product_list),
    )


@logistics_bp.route("/lists/<int:list_id>/qr")
@logistics_bp.route("/pallets/<int:list_id>/qr")
def pallet_qr(list_id):
    session = g.db
    product_list = session.get(ProductList, list_id)
    if product_list is None:
        abort(404)
    payload = product_list.pallet_code or product_list.code
    img = qrcode.make(payload)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


@logistics_bp.route("/lists/<int:list_id>/label")
def pallet_label(list_id):
    session = g.db
    product_list = (
        session.query(ProductList)
        .options(
            joinedload(ProductList.current_warehouse),
            joinedload(ProductList.target_warehouse),
        )
        .get(list_id)
    )
    if product_list is None:
        abort(404)
    totals = calculate_list_totals(product_list)
    buffer = BytesIO()
    page_size = landscape(A6)
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    pdf.setFont(PDF_FONT_NAME, 16)
    pdf.drawString(15 * mm, page_size[1] - 20 * mm, product_list.title)
    pdf.setFont(PDF_FONT_NAME, 12)
    pdf.drawString(15 * mm, page_size[1] - 30 * mm, f"Code: {product_list.code}")
    if product_list.pallet_code:
        pdf.drawString(15 * mm, page_size[1] - 38 * mm, f"Pallet: {product_list.pallet_code}")
    pdf.drawString(15 * mm, page_size[1] - 48 * mm, f"From: {product_list.current_warehouse.name}")
    pdf.drawString(
        15 * mm,
        page_size[1] - 56 * mm,
        f"To: {product_list.target_warehouse.name if product_list.target_warehouse else 'N/A'}",
    )
    pdf.drawString(15 * mm, page_size[1] - 66 * mm, f"Status: {product_list.status.upper()}")
    location_text = product_list.storage_location or "No location provided"
    pdf.drawString(15 * mm, page_size[1] - 74 * mm, f"Location: {location_text}")
    pdf.drawString(
        15 * mm,
        page_size[1] - 82 * mm,
        f"Items: {totals['total_quantity']:.2f} ({totals['line_count']} rows)",
    )
    pdf.drawString(15 * mm, page_size[1] - 90 * mm, f"Weight: {totals['total_weight']:.2f} kg")
    pdf.drawString(15 * mm, page_size[1] - 98 * mm, f"Volume: {totals['total_volume']:.3f} m³")
    img = qrcode.make(product_list.pallet_code or product_list.code)
    qr_buffer = BytesIO()
    img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    pdf.drawImage(
        ImageReader(qr_buffer),
        page_size[0] - 70 * mm,
        page_size[1] - 95 * mm,
        50 * mm,
        50 * mm,
    )
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    filename = f"{product_list.code}.pdf"
    return send_file(buffer, mimetype="application/pdf", download_name=filename)


@logistics_bp.route("/lists/<int:list_id>/palletize", methods=["POST"])
def palletize_list(list_id):
    session = g.db
    product_list = session.get(ProductList, list_id)
    if product_list is None:
        abort(404)
    if product_list.is_pallet:
        flash("List is already palletized.", "warning")
        return redirect(url_for(".pallet_detail", list_id=list_id))
    product_list.is_pallet = True
    product_list.pallet_code = generate_pallet_code(session)
    product_list.status = "palletized"
    product_list.updated_at = datetime.utcnow()
    session.commit()
    flash(f"Pallet code assigned ({product_list.pallet_code}).")
    return redirect(url_for(".pallet_detail", list_id=list_id))


@logistics_bp.route("/lists/<int:list_id>/transfer", methods=["GET", "POST"])
def transfer_list(list_id):
    session = g.db
    product_list = (
        session.query(ProductList)
        .options(joinedload(ProductList.current_warehouse), joinedload(ProductList.target_warehouse))
        .get(list_id)
    )
    if product_list is None:
        abort(404)
    warehouses = load_warehouses(session)
    if request.method == "POST":
        dest_id = request.form.get("destination_warehouse_id")
        if not dest_id:
            flash("Select a destination warehouse.", "warning")
            return redirect(url_for(".transfer_list", list_id=list_id))
        dest_id = int(dest_id)
        if dest_id == product_list.current_warehouse_id:
            flash("Destination cannot match the source warehouse.", "warning")
            return redirect(url_for(".transfer_list", list_id=list_id))
        active = session.query(TransferDocument).filter_by(list_id=list_id, status="in_transit").first()
        if active:
            flash("Another transfer is already in transit.", "warning")
            return redirect(url_for(".pallet_detail", list_id=list_id))
        transfer = TransferDocument(
            list_id=list_id,
            code=generate_transfer_code(session),
            from_warehouse_id=product_list.current_warehouse_id,
            to_warehouse_id=dest_id,
            status="in_transit",
            shipped_at=datetime.utcnow(),
        )
        session.add(transfer)
        product_list.status = "in_transit"
        product_list.target_warehouse_id = dest_id
        product_list.updated_at = datetime.utcnow()
        session.commit()
        flash("Transfer started.", "success")
        return redirect(url_for(".pallet_detail", list_id=list_id))
    return render_template(
        "transfer_form.html", product_list=product_list, warehouses=warehouses
    )


@logistics_bp.route("/transfers")
def transfers():
    session = g.db
    view_mode = request.args.get("view", "cards")
    if view_mode not in ("cards", "table"):
        view_mode = "cards"
    base_args = request.args.to_dict()
    base_args.pop("view", None)
    cards_url = url_for(".transfers", **{**base_args, "view": "cards"})
    table_url = url_for(".transfers", **{**base_args, "view": "table"})
    documents = (
        session.query(TransferDocument)
        .options(
            joinedload(TransferDocument.product_list),
            joinedload(TransferDocument.from_warehouse),
            joinedload(TransferDocument.to_warehouse),
        )
        .order_by(TransferDocument.created_at.desc())
        .all()
    )
    return render_template(
        "transfers.html",
        documents=documents,
        view_mode=view_mode,
        cards_url=cards_url,
        table_url=table_url,
    )


@logistics_bp.route("/receive", methods=["GET", "POST"])
def receive():
    session = g.db
    code = request.form.get("code") if request.method == "POST" else request.args.get("code")
    scanned_list = None
    transfer = None
    totals = None
    if code:
        normalized = code.strip().upper()
        scanned_list = (
            session.query(ProductList)
            .options(
                joinedload(ProductList.current_warehouse),
                joinedload(ProductList.target_warehouse),
                joinedload(ProductList.items).joinedload(ProductListItem.product),
                joinedload(ProductList.transfers),
            )
            .filter(
                or_(
                    func.upper(ProductList.code) == normalized,
                    func.upper(ProductList.pallet_code) == normalized,
                )
            )
            .first()
        )
        if scanned_list:
            transfer = next((t for t in scanned_list.transfers if t.status == "in_transit"), None)
            if not transfer:
                flash("The scanned list has no active transfer.", "warning")
            totals = calculate_list_totals(scanned_list)
    return render_template("receive.html", scanned_list=scanned_list, transfer=transfer, code=code or "", totals=totals)


@logistics_bp.route("/receive/<int:transfer_id>/complete", methods=["POST"])
def complete_receive(transfer_id):
    session = g.db
    transfer = (
        session.query(TransferDocument)
        .options(
            joinedload(TransferDocument.product_list).joinedload(ProductList.current_warehouse),
            joinedload(TransferDocument.to_warehouse),
        )
        .get(transfer_id)
    )
    if transfer is None or transfer.status != "in_transit":
        flash("This transfer cannot be completed.", "warning")
        return redirect(url_for(".receive"))
    product_list = transfer.product_list
    product_list.current_warehouse_id = transfer.to_warehouse_id
    product_list.target_warehouse_id = None
    product_list.status = "received"
    product_list.updated_at = datetime.utcnow()
    transfer.status = "received"
    transfer.received_at = datetime.utcnow()
    session.commit()
    flash("Transfer completed and list updated.", "success")
    return redirect(url_for(".pallet_detail", list_id=product_list.id))
