from datetime import datetime
from pathlib import Path

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
    ScanEvent,
    ScanTask,
    ScanTaskItem,
    ServicePoint,
    StockOrder,
    StockOrderAssignment,
    StockOrderItem,
)
from utils import generate_ppp_pdf, save_signature_image
from helpers import parse_float
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


def _log_order_context(prefix: str, order: StockOrder, extra: str | None = None):
    items_summary = ", ".join(
        f"id={item.id} prepared={item.quantity_prepared:.2f} ordered={item.quantity_ordered:.2f} delivered={item.quantity_delivered:.2f}"
        for item in order.items
    )
    message = f"{prefix} order={order.id} status={order.status} {items_summary}"
    if extra:
        message = f"{message} | {extra}"
    current_app.logger.info(message)


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
    _log_order_context("prepare-view", order)
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
    _log_order_context("scan", order, extra=f"barcode={barcode} qty={qty}")
    item_data = {
        "product": product.name,
        "ordered": order_item.quantity_ordered,
        "prepared": order_item.quantity_prepared,
        "delivered": order_item.quantity_delivered,
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
    target_raw = payload.get("target_prepared")
    new_prepared = None
    delta = None
    if target_raw not in (None, ""):
        try:
            new_prepared = float(target_raw)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Invalid target quantity"}), 400
        if new_prepared < 0 or new_prepared > order_item.quantity_ordered:
            return jsonify({"success": False, "error": "Target quantity must be between 0 and ordered"}), 400
        delta = new_prepared - order_item.quantity_prepared
    else:
        qty_raw = payload.get("qty")
        try:
            qty = float(qty_raw)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Invalid quantity"}), 400
        if qty <= 0:
            return jsonify({"success": False, "error": "Quantity must be positive"}), 400
        if order_item.quantity_prepared + qty > order_item.quantity_ordered:
            return jsonify({"success": False, "error": "Quantity exceeds order"}), 400
        new_prepared = order_item.quantity_prepared + qty
        delta = qty
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
    # ...
    # Просто променяме стойността на вече заредения обект.
    # SQLAlchemy ще засече тази промяна и ще я запише при commit.
    order_item.quantity_prepared = new_prepared
    scan_item.scanned_qty = new_prepared
# ...
    update_scan_task_status(task)
    update_stock_order_status(order)
    record_scan_event(task, scan_item, abs(delta) if delta is not None else 0.0, source="manual", message="stock order manual entry")
    g.db.commit()
    log_extra = f"item={order_item.id} delta={delta:.2f}" if delta is not None else f"item={order_item.id}"
    _log_order_context("manual", order, extra=log_extra)
    item_data = {
        "item_id": order_item.id,
        "product": order_item.product.name if order_item.product else "Unknown",
        "ordered": order_item.quantity_ordered,
        "prepared": order_item.quantity_prepared,
        "delivered": order_item.quantity_delivered,
        "remaining": order_item.remaining_to_prepare,
        "status": order_item.preparation_status,
        "target": new_prepared,
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
        # 1. Прикачваме обекта към сесията, за да сме сигурни, че SQLAlchemy го следи.
        session.add(order)

        updates = 0
        delivered_qty = 0.0
        for item in order.items:
            key = f"deliver_{item.id}"
            raw = request.form.get(key)
            if raw in (None, "", "0"):
                continue
            
            qty = parse_float(raw)
            if qty is None:
                flash("Невалидно количество за доставка.", "danger")
                return redirect(url_for("orders.stock_order_handover", order_id=order.id))
            
            remaining = item.remaining_to_deliver
            if qty < 0 or qty > remaining + 1e-6:
                flash(f"Количеството за доставка ({qty}) е извън валидния диапазон (0 до {remaining}).", "danger")
                return redirect(url_for("orders.stock_order_handover", order_id=order.id))
            
            actual_qty = min(qty, remaining)
            if actual_qty <= 0:
                continue
            
            # 2. ПРОМЕНЯМЕ ДИРЕКТНО ОБЕКТА. Това е правилният ORM подход.
            # SQLAlchemy ще "забележи" тази промяна и ще я подготви за запис.
            item.quantity_delivered += actual_qty
            
            delivered_qty += actual_qty
            updates += 1

        if updates == 0:
            flash("Не са избрани артикули за предаване.", "warning")
            return redirect(url_for("orders.stock_order_handover", order_id=order.id))

        recipient_name = (request.form.get("recipient_name") or "").strip()
        if recipient_name:
            order.recipient_name = recipient_name

        signature_data = request.form.get("signature_data")
        if not signature_data:
            flash("Моля, предоставете подпис.", "warning")
            return redirect(url_for("orders.stock_order_handover", order_id=order.id))

        try:
            signature_rel = save_signature_image(order.id, signature_data)
        except ValueError:
            flash("Невалиден формат на подписа.", "danger")
            return redirect(url_for("orders.stock_order_handover", order_id=order.id))

        # 3. Обновяваме всички останали данни по поръчката.
        timestamp = datetime.utcnow()
        user_id = g.current_user.id if g.current_user else None
        order.last_handover_at = timestamp
        order.last_handover_by_id = user_id
        
        update_stock_order_status(order)

        update_stock_order_status(order)

        if order.status == "delivered":
            order.delivered_at = timestamp
            order.delivered_by_id = user_id

        # --- НАЧАЛО НА КОРЕКЦИЯТА ---
        # Тъй като сесията не записва статуса надеждно,
        # ние изрично ѝ казваме да го направи с директна заявка.
        # Това ще се изпълни в същата транзакция.
        session.query(StockOrder).filter(StockOrder.id == order.id).update(
            {
                "status": order.status,
                "delivered_at": order.delivered_at,
                "delivered_by_id": order.delivered_by_id,
                "last_handover_at": order.last_handover_at,
                "last_handover_by_id": order.last_handover_by_id,
            },
            synchronize_session=False # Важно е да е False тук!
        )
        # --- КРАЙ НА КОРЕКЦИЯТА ---


        # Създаваме и свързваме ППП документа.
        reference_key = order.external_id or str(order.id)
        sequence = len(order.ppp_documents) + 1
        versus_ppp_id = f"{reference_key}-{sequence:02d}"
        identifier_suffix = timestamp.strftime("%Y%m%d%H%M%S")
        pdf_identifier = f"{order.id}_{identifier_suffix}_draft"
        signed_pdf_identifier = f"{order.id}_{identifier_suffix}_signed"
        pdf_url = generate_ppp_pdf(order, identifier=pdf_identifier)
        signed_pdf_url = generate_ppp_pdf(order, signature_rel_path=signature_rel, identifier=signed_pdf_identifier)

        document = PPPDocument(
            stock_order=order,
            versus_ppp_id=versus_ppp_id,
            pdf_url=pdf_url,
            signed_pdf_url=signed_pdf_url,
            signature_image=signature_rel,
            status=order.status,
        )
        session.add(document)
        session.commit()

        flash("Предаването е записано успешно.", "success")
        _log_order_context("handover-complete", order, extra=f"delivered_items={updates} qty={delivered_qty:.2f}")
        return redirect(url_for("orders.stock_order_handover", order_id=order.id))
    
    # --- Начало на GET частта на функцията ---
    attach_service_point_sections([order])
    deliverable_items = [item for item in order.items if item.remaining_to_deliver > 0]
    deliverable_total = sum(item.remaining_to_deliver for item in deliverable_items)
    prepared_total = sum(item.quantity_prepared for item in order.items)
    total_ordered = sum(item.quantity_ordered for item in order.items)

    # --- ТУК Е КОРЕКЦИЯТА ---
    deliverable_hint = f"Имате {deliverable_total:.2f} артикула, готови за предаване. Проверете количествата и вземете подпис."
    no_deliverable_hint = "Няма артикули за предаване. Ако има грешка, върнете се в екрана за подготовка."

    _log_order_context("handover-view", order, extra=f"deliverable_total={deliverable_total:.2f}")
    return render_template(
        "stock_order_handover.html",
        order=order,
        status_labels=STOCK_ORDER_STATUS_LABELS,
        deliverable_total=deliverable_total,
        prepared_total=prepared_total,
        total_ordered=total_ordered,
        has_deliverable=bool(deliverable_items),
        deliverable_hint=deliverable_hint,
        no_deliverable_hint=no_deliverable_hint,
    )


@orders_bp.route("/stock-orders/<int:order_id>/ppp", endpoint="stock_order_ppp")
def stock_order_ppp(order_id):
    session = g.db
    order = get_stock_order_with_details(order_id)
    if order is None:
        abort(404)
    documents = sorted(
        order.ppp_documents,
        key=lambda doc: doc.created_at or doc.id,
        reverse=True,
    )
    if not documents:
        flash("No PPP document found.", "warning")
        return redirect(url_for("orders.stock_order_handover", order_id=order.id))
    latest_document = documents[0]
    signature_url = url_for("static", filename=latest_document.signature_image) if latest_document.signature_image else None
    pdf_url = url_for("orders.stock_order_ppp_pdf", order_id=order.id, doc_id=latest_document.id) if latest_document.pdf_url else None
    scan_tasks = (
        session.query(ScanTask)
        .options(
            joinedload(ScanTask.events)
            .joinedload(ScanEvent.item)
            .joinedload(ScanTaskItem.product),
            joinedload(ScanTask.items).joinedload(ScanTaskItem.product),
            joinedload(ScanTask.created_by),
        )
        .filter(ScanTask.stock_order_id == order.id)
        .order_by(ScanTask.created_at.desc())
        .all()
    )
    return render_template(
        "stock_order_ppp.html",
        order=order,
        ppp=latest_document,
        pdf_url=pdf_url,
        signature_url=signature_url,
        documents=documents,
        scan_tasks=scan_tasks,
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
        query.filter(StockOrder.ppp_documents.any())
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
    if order is None:
        abort(404)
    doc_id = request.args.get("doc_id", type=int)
    document = None
    if doc_id:
        document = next((doc for doc in order.ppp_documents if doc.id == doc_id), None)
    if document is None:
        document = order.ppp_document
    if document is None:
        abort(404)
    pdf_reference = document.signed_pdf_url or document.pdf_url
    if not pdf_reference:
        abort(404)
    pdf_path = PPP_STATIC_DIR / Path(pdf_reference).name
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
