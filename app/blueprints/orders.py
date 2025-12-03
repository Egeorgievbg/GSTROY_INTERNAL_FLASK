from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required
from sqlalchemy.orm import joinedload

from constants import (
    PPP_STATIC_DIR,
    STATUS_BADGE_CLASSES,
    STOCK_ORDER_EAGER_OPTIONS,
    STOCK_ORDER_STATUS_LABELS,
    STOCK_ORDER_STATUSES,
    STOCK_ORDER_TYPE_LABELS,
    STOCK_ORDER_TYPES,
)
from models import (
    PPPDocument,
    ScanTaskItem,
    ServicePoint,
    StockOrder,
    StockOrderAssignment,
)
from utils import generate_ppp_pdf, save_signature_image
from app.services.order_tasks import (
    apply_inventory_movement,
    attach_service_point_sections,
    assignment_load_counts,
    build_service_point_candidates,
    ensure_scan_task_for_order,
    find_product_by_barcode,
    get_stock_order_with_details,
    order_service_point_ids,
    record_scan_event,
    stock_order_erp_input_payload,
    stock_order_erp_output_payload,
    stock_order_status_counts,
    update_scan_task_status,
    update_stock_order_status,
    user_service_point_ids,
)


orders_bp = Blueprint("orders", __name__)


@orders_bp.route("/stock-orders", endpoint="stock_orders_dashboard")
def stock_orders_dashboard():
    session = g.db
    status_filter = request.args.get("status")
    type_filter = request.args.get("type")
    query = session.query(StockOrder)
    for option in STOCK_ORDER_EAGER_OPTIONS:
        query = query.options(option)
    if status_filter:
        query = query.filter(StockOrder.status == status_filter)
    else:
        query = query.filter(StockOrder.status != "delivered")
    if type_filter:
        query = query.filter(StockOrder.type == type_filter)
    orders = query.order_by(StockOrder.created_at.desc()).limit(15).all()
    attach_service_point_sections(orders)
    base_args = request.args.to_dict()
    view_mode = base_args.pop("view", "cards")
    if view_mode not in ("cards", "table"):
        view_mode = "cards"
    cards_url = url_for("orders.stock_orders_dashboard", **{**base_args, "view": "cards"})
    table_url = url_for("orders.stock_orders_dashboard", **{**base_args, "view": "table"})
    context = {
        "orders": orders,
        "status_counts": stock_order_status_counts(session),
        "status_labels": STOCK_ORDER_STATUS_LABELS,
        "type_labels": STOCK_ORDER_TYPE_LABELS,
        "status_order": STOCK_ORDER_STATUSES,
        "type_order": STOCK_ORDER_TYPES,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "service_point_candidates": build_service_point_candidates(),
        "assignment_counts": assignment_load_counts(session),
        "show_filters": True,
        "page_title": "Stock order dashboard",
        "auto_refresh": True,
        "view_mode": view_mode,
        "cards_url": cards_url,
        "table_url": table_url,
        "status_classes": STATUS_BADGE_CLASSES,
    }
    return render_template("stock_orders_dashboard.html", **context)


@orders_bp.route("/stock-orders/assigned-to-me", endpoint="stock_orders_assigned")
def stock_orders_assigned():
    user = g.current_user
    if not user:
        flash("Log in to continue.", "warning")
        return redirect(url_for("stock_orders_dashboard"))
    session = g.db
    query = session.query(StockOrder)
    for option in STOCK_ORDER_EAGER_OPTIONS:
        query = query.options(option)
    query = query.join(StockOrder.assignments).filter(StockOrderAssignment.user_id == user.id)
    query = query.filter(
        StockOrder.status.in_(["assigned", "in_progress", "ready_for_handover", "partially_delivered"])
    )
    orders = query.order_by(StockOrder.updated_at.desc()).all()
    attach_service_point_sections(orders)
    context = {
        "orders": orders,
        "status_counts": stock_order_status_counts(session),
        "status_labels": STOCK_ORDER_STATUS_LABELS,
        "type_labels": STOCK_ORDER_TYPE_LABELS,
        "status_order": STOCK_ORDER_STATUSES,
        "type_order": STOCK_ORDER_TYPES,
        "status_filter": None,
        "type_filter": None,
        "service_point_candidates": build_service_point_candidates(),
        "assignment_counts": assignment_load_counts(session),
        "show_filters": False,
        "page_title": "Assigned orders",
        "auto_refresh": True,
        "view_mode": "cards",
        "status_classes": STATUS_BADGE_CLASSES,
    }
    return render_template("stock_orders_assigned.html", **context)


@orders_bp.post("/stock-orders/<int:order_id>/assign", endpoint="stock_order_assign")
def stock_order_assign(order_id):
    if not g.current_user or not g.current_user.can_assign_orders:
        abort(403)
    session = g.db
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    service_point_ids = order_service_point_ids(order)
    for sp_id in service_point_ids:
        field_name = f"service_point_{sp_id}"
        raw_values = request.form.getlist(field_name)
        selected = []
        for raw in raw_values:
            try:
                candidate = int(raw)
            except (TypeError, ValueError):
                continue
            if candidate not in selected:
                selected.append(candidate)
            if len(selected) == 2:
                break
        assignments = [a for a in order.assignments if a.service_point_id == sp_id]
        existing_ids = {a.user_id for a in assignments}
        for assignment in assignments:
            if assignment.user_id not in selected:
                session.delete(assignment)
        for candidate in selected:
            if candidate not in existing_ids:
                session.add(
                    StockOrderAssignment(
                        stock_order_id=order.id,
                        service_point_id=sp_id,
                        user_id=candidate,
                    )
                )
    update_stock_order_status(order)
    session.commit()
    flash("Assignments updated.", "success")
    return redirect(request.referrer or url_for("stock_orders_dashboard"))


@orders_bp.post("/stock-orders/<int:order_id>/take", endpoint="stock_order_take")
def stock_order_take(order_id):
    user = g.current_user
    if not user or not user.can_prepare_orders:
        abort(403)
    session = g.db
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    target_sp_ids = order_service_point_ids(order) & user_service_point_ids(user)
    if not target_sp_ids:
        flash("You are not assigned to any required service point.", "warning")
        return redirect(request.referrer or url_for("stock_orders_dashboard"))
    for sp_id in target_sp_ids:
        assignments = [a for a in order.assignments if a.service_point_id == sp_id]
        if user.id in {a.user_id for a in assignments}:
            continue
        if len(assignments) >= 2:
            continue
        session.add(
            StockOrderAssignment(
                stock_order_id=order.id,
                service_point_id=sp_id,
                user_id=user.id,
            )
        )
    update_stock_order_status(order)
    session.commit()
    flash("Order claimed.", "success")
    return redirect(url_for("orders.stock_order_prepare", order_id=order.id))


@orders_bp.route("/stock-orders/<int:order_id>/prepare", endpoint="stock_order_prepare")
def stock_order_prepare(order_id):
    session = g.db
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    user = g.current_user
    if not user or not user.can_prepare_orders:
        flash("Prepare permissions are required.", "warning")
        return redirect(url_for("stock_orders_dashboard"))
    accessible_sp_ids = order_service_point_ids(order) & user_service_point_ids(user)
    if not accessible_sp_ids:
        flash("No service point access for this order.", "warning")
        return redirect(url_for("stock_orders_dashboard"))
    sections = []
    for sp_id in sorted(accessible_sp_ids):
        service_point = g.db.query(ServicePoint).get(sp_id)
        items = [item for item in order.items if item.service_point_id == sp_id]
        if not items:
            continue
        task = ensure_scan_task_for_order(order, sp_id, user)
        sections.append({"service_point": service_point, "items": items, "scan_task": task})
    stats = {"items": 0, "completed": 0, "remaining": 0.0}
    for section in sections:
        for item in section["items"]:
            stats["items"] += 1
            if item.quantity_prepared >= item.quantity_ordered:
                stats["completed"] += 1
            stats["remaining"] += item.remaining_to_prepare
    return render_template(
        "stock_order_prepare.html",
        order=order,
        sections=sections,
        stats=stats,
        scan_url=url_for("orders.stock_order_scan", order_id=order.id),
        manual_url=url_for("orders.stock_order_manual", order_id=order.id),
        status_labels=STOCK_ORDER_STATUS_LABELS,
    )


@orders_bp.post("/stock-orders/<int:order_id>/scan", endpoint="stock_order_scan")
def stock_order_scan(order_id):
    session = g.db
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    user = g.current_user
    if not user or not user.can_prepare_orders:
        abort(403)
    payload = request.get_json(silent=True) or request.form
    if not payload:
        return jsonify({"success": False, "error": "Missing payload"}), 400
    try:
        service_point_id = int(payload.get("service_point_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid service point"}), 400
    if service_point_id not in (order_service_point_ids(order) & user_service_point_ids(user)):
        return jsonify({"success": False, "error": "Unauthorized service point"}), 403
    barcode = (payload.get("barcode") or "").strip()
    if not barcode:
        return jsonify({"success": False, "error": "Missing barcode"}), 400
    qty_raw = payload.get("qty")
    try:
        qty = float(qty_raw) if qty_raw is not None else 1.0
    except (TypeError, ValueError):
        qty = 1.0
    if qty <= 0:
        return jsonify({"success": False, "error": "Quantity must be positive"}), 400
    product = find_product_by_barcode(session, barcode)
    if product is None:
        return jsonify({"success": False, "error": "Product not found"}), 404
    order_item = next(
        (item for item in order.items if item.product_id == product.id and item.service_point_id == service_point_id),
        None,
    )
    if order_item is None:
        return jsonify({"success": False, "error": "Item not part of this order"}), 404
    if order_item.quantity_prepared + qty > order_item.quantity_ordered:
        return jsonify({"success": False, "error": "Quantity exceeds order"}), 400
    task = ensure_scan_task_for_order(order, service_point_id, user)
    scan_item = next((item for item in task.items if item.product_id == product.id), None)
    if scan_item is None:
        return jsonify({"success": False, "error": "Task item missing"}), 404
    order_item.quantity_prepared += qty
    scan_item.scanned_qty = order_item.quantity_prepared
    update_scan_task_status(task)
    update_stock_order_status(order)
    record_scan_event(task, scan_item, qty, source="stock_order", message="stock order preparation")
    session.commit()
    item_data = {
        "product": product.name,
        "ordered": order_item.quantity_ordered,
        "prepared": order_item.quantity_prepared,
        "remaining": order_item.remaining_to_prepare,
        "status": order_item.preparation_status,
        "item_id": order_item.id,
    }
    return jsonify(
        {
            "success": True,
            "item": item_data,
            "order_status": order.status,
            "order_status_label": STOCK_ORDER_STATUS_LABELS.get(order.status, order.status),
        }
    )


@orders_bp.post("/stock-orders/<int:order_id>/manual", endpoint="stock_order_manual")
def stock_order_manual(order_id):
    session = g.db
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    user = g.current_user
    if not user or not user.can_prepare_orders:
        abort(403)
    payload = request.get_json(silent=True) or request.form
    if not payload:
        return jsonify({"success": False, "error": "Missing payload"}), 400
    try:
        service_point_id = int(payload.get("service_point_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid service point"}), 400
    if service_point_id not in (order_service_point_ids(order) & user_service_point_ids(user)):
        return jsonify({"success": False, "error": "Unauthorized service point"}), 403
    try:
        item_id = int(payload.get("item_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid item"}), 400
    order_item = next(
        (item for item in order.items if item.id == item_id and item.service_point_id == service_point_id),
        None,
    )
    if order_item is None:
        return jsonify({"success": False, "error": "Order item missing"}), 404
    qty_raw = payload.get("qty")
    try:
        qty = float(qty_raw)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid quantity"}), 400
    if qty <= 0:
        return jsonify({"success": False, "error": "Quantity must be positive"}), 400
    if order_item.quantity_prepared + qty > order_item.quantity_ordered:
        return jsonify({"success": False, "error": "Quantity exceeds order"}), 400
    task = ensure_scan_task_for_order(order, service_point_id, user)
    scan_item = next((item for item in task.items if item.product_id == order_item.product_id), None)
    if scan_item is None:
        scan_item = ScanTaskItem(
            task_id=task.id,
            product_id=order_item.product_id,
            barcode=(order_item.product.barcode if order_item.product and order_item.product.barcode else f"ITEM-{order_item.id}"),
            expected_qty=order_item.quantity_ordered,
            scanned_qty=order_item.quantity_prepared,
            unit=order_item.unit or (order_item.product.main_unit if order_item.product else "pcs"),
        )
        g.db.add(scan_item)
        g.db.flush()
        task.items.append(scan_item)
    order_item.quantity_prepared += qty
    scan_item.scanned_qty = order_item.quantity_prepared
    update_scan_task_status(task)
    update_stock_order_status(order)
    record_scan_event(task, scan_item, qty, source="manual", message="stock order manual entry")
    g.db.commit()
    item_data = {
        "item_id": order_item.id,
        "product": order_item.product.name if order_item.product else "Unknown",
        "ordered": order_item.quantity_ordered,
        "prepared": order_item.quantity_prepared,
        "remaining": order_item.remaining_to_prepare,
        "status": order_item.preparation_status,
    }
    return jsonify(
        {
            "success": True,
            "item": item_data,
            "order_status": order.status,
            "order_status_label": STOCK_ORDER_STATUS_LABELS.get(order.status, order.status),
        }
    )


@orders_bp.route("/stock-orders/<int:order_id>/handover", methods=["GET", "POST"], endpoint="stock_order_handover")
def stock_order_handover(order_id):
    session = g.db
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    if request.method == "POST":
        updates = 0
        for item in order.items:
            key = f"deliver_{item.id}"
            raw = request.form.get(key)
            if raw in (None, "", "0"):
                continue
            try:
                qty = float(raw)
            except (TypeError, ValueError):
                flash("Invalid delivery quantity.", "danger")
                return redirect(url_for("orders.stock_order_handover", order_id=order.id))
            if qty < 0 or qty > item.remaining_to_deliver:
                flash("Delivery qty out of range.", "danger")
                return redirect(url_for("orders.stock_order_handover", order_id=order.id))
            if qty == 0:
                continue
            item.quantity_delivered += qty
            updates += 1
        recipient_name = (request.form.get("recipient_name") or "").strip()
        if recipient_name:
            order.recipient_name = recipient_name
        if updates == 0:
            flash("No items updated.", "warning")
            return redirect(url_for("orders.stock_order_handover", order_id=order.id))
        timestamp = datetime.utcnow()
        user_id = g.current_user.id if g.current_user else None
        order.last_handover_at = timestamp
        order.last_handover_by_id = user_id
        signature_data = request.form.get("signature_data")
        if not signature_data:
            flash("Please provide a signature.", "warning")
            return redirect(url_for("orders.stock_order_handover", order_id=order.id))
        try:
            signature_rel = save_signature_image(order.id, signature_data)
        except ValueError:
            flash("Invalid signature payload.", "danger")
            return redirect(url_for("orders.stock_order_handover", order_id=order.id))
        update_stock_order_status(order)
        document = order.ppp_document
        if document is None:
            document = PPPDocument(
                stock_order_id=order.id,
                versus_ppp_id=f"PPP-{order.id}-{int(datetime.utcnow().timestamp())}",
            )
            order.ppp_document = document
            session.add(document)
        document.signature_image = signature_rel
        pdf_path = generate_ppp_pdf(order, signature_rel)
        document.pdf_url = pdf_path
        if order.status == "delivered":
            order.delivered_at = timestamp
            order.delivered_by_id = user_id
        if document.status == "signed":
            document.signed_pdf_url = pdf_path
        else:
            document.status = "signed"
            document.signed_pdf_url = pdf_path
        document.updated_at = datetime.utcnow()
        session.commit()
        flash("Handover recorded.", "success")
        return redirect(url_for("orders.stock_order_handover", order_id=order.id))
    attach_service_point_sections([order])
    return render_template("stock_order_handover.html", order=order, status_labels=STOCK_ORDER_STATUS_LABELS)


@orders_bp.route("/stock-orders/<int:order_id>/ppp", endpoint="stock_order_ppp")
def stock_order_ppp(order_id):
    session = g.db
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    document = order.ppp_document
    if document is None:
        flash("No PPP document found.", "warning")
        return redirect(url_for("orders.stock_order_handover", order_id=order.id))
    signature_url = url_for("static", filename=document.signature_image) if document.signature_image else None
    pdf_url = url_for("orders.stock_order_ppp_pdf", order_id=order.id) if document.pdf_url else None
    signed_pdf_url = pdf_url
    return render_template(
        "stock_order_ppp.html",
        order=order,
        ppp=document,
        pdf_url=pdf_url,
        signed_pdf_url=signed_pdf_url,
        signature_url=signature_url,
    )


@orders_bp.route("/stock-orders/completed", endpoint="ppp_documents")
def ppp_documents():
    session = g.db
    view_mode = request.args.get("view", "cards")
    if view_mode not in ("cards", "table"):
        view_mode = "cards"
    base_args = request.args.to_dict()
    base_args.pop("view", None)
    cards_url = url_for("orders.ppp_documents", **{**base_args, "view": "cards"})
    table_url = url_for("orders.ppp_documents", **{**base_args, "view": "table"})
    query = session.query(StockOrder)
    for option in STOCK_ORDER_EAGER_OPTIONS:
        query = query.options(option)
    orders = (
        query.filter(StockOrder.status == "delivered")
        .order_by(StockOrder.updated_at.desc())
        .all()
    )
    attach_service_point_sections(orders)
    return render_template(
        "ppp_documents.html",
        orders=orders,
        status_labels=STOCK_ORDER_STATUS_LABELS,
        view_mode=view_mode,
        cards_url=cards_url,
        table_url=table_url,
    )


@orders_bp.route("/stock-orders/<int:order_id>/ppp/pdf", endpoint="stock_order_ppp_pdf")
def stock_order_ppp_pdf(order_id):
    order = get_stock_order_with_details(order_id)
    if order is None or order.ppp_document is None or not order.ppp_document.pdf_url:
        abort(404)
    pdf_path = PPP_STATIC_DIR / Path(order.ppp_document.pdf_url).name
    if not pdf_path.exists():
        abort(404)
    return send_file(pdf_path, mimetype="application/pdf", as_attachment=False)


@orders_bp.route("/stock-orders/<int:order_id>/erp-input", endpoint="stock_order_erp_input")
def stock_order_erp_input(order_id):
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    return jsonify(stock_order_erp_input_payload(order))


@orders_bp.route("/stock-orders/<int:order_id>/erp-output", endpoint="stock_order_erp_output")
def stock_order_erp_output(order_id):
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    return jsonify(stock_order_erp_output_payload(order))
