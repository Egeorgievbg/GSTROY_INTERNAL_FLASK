import csv
from io import BytesIO, StringIO

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
from sqlalchemy.orm import joinedload

from models import ProductList, ProductListItem, ScanEvent, ScanTask, ScanTaskItem
from utils import load_warehouses, parse_scan_task_lines
from app.services.order_tasks import (
    apply_inventory_movement,
    find_product_by_barcode,
    record_scan_event,
    update_scan_task_status,
)


scanning_bp = Blueprint("scanning", __name__)


@scanning_bp.route("/scan-tasks", endpoint="scan_task_list")
def scan_task_list():
    session = g.db
    view_mode = request.args.get("view", "cards")
    if view_mode not in ("cards", "table"):
        view_mode = "cards"
    base_args = request.args.to_dict()
    base_args.pop("view", None)
    cards_url = url_for("scanning.scan_task_list", **{**base_args, "view": "cards"})
    table_url = url_for("scanning.scan_task_list", **{**base_args, "view": "table"})
    tasks = (
        session.query(ScanTask)
        .options(
            joinedload(ScanTask.warehouse),
            joinedload(ScanTask.items),
        )
        .order_by(ScanTask.created_at.desc())
        .all()
    )
    return render_template(
        "scan_task_list.html",
        tasks=tasks,
        view_mode=view_mode,
        cards_url=cards_url,
        table_url=table_url,
    )


@scanning_bp.route("/scan-tasks/new", methods=["GET", "POST"], endpoint="scan_task_new")
def scan_task_new():
    session = g.db
    if request.method == "POST":
        name = (request.form.get("name") or "Scan Task").strip()
        task_type = (request.form.get("type") or "inventory").strip()
        raw_list = request.form.get("input_list") or ""
        warehouse_id = request.form.get("warehouse_id")
        source_list_id = request.form.get("source_list_id") or None
        if not warehouse_id:
            flash("Select a warehouse.")
            return redirect(url_for("scan_task_new"))
        entries = parse_scan_task_lines(raw_list)
        if source_list_id:
            src = (
                session.query(ProductList)
                .options(joinedload(ProductList.items).joinedload(ProductListItem.product))
                .get(int(source_list_id))
            )
            if src:
                for item in src.items:
                    barcode = item.product.barcode if item.product and item.product.barcode else item.product.item_number
                    entries.append((barcode, item.quantity))
        if not entries:
            flash("Add at least one barcode.")
            return redirect(url_for("scan_task_new"))

        task = ScanTask(
            name=name,
            type=task_type or "inventory",
            status="open",
            warehouse_id=int(warehouse_id) if warehouse_id else None,
        )
        session.add(task)
        session.flush()

        for barcode, qty in entries:
            product = find_product_by_barcode(session, barcode)
            session.add(
                ScanTaskItem(
                    task_id=task.id,
                    product_id=product.id if product else None,
                    barcode=barcode.strip(),
                    expected_qty=qty,
                    scanned_qty=0.0,
                    unit=product.main_unit if product else None,
                )
            )

        update_scan_task_status(task)
        session.commit()
        flash("Task created.")
        return redirect(url_for("scan_task_detail", task_id=task.id))

    warehouses = load_warehouses(session)
    product_lists = (
        session.query(ProductList)
        .order_by(ProductList.updated_at.desc())
        .all()
    )
    return render_template("scan_task_new.html", warehouses=warehouses, product_lists=product_lists)


@scanning_bp.route("/scan-tasks/<int:task_id>", endpoint="scan_task_detail")
def scan_task_detail(task_id):
    session = g.db
    task = (
        session.query(ScanTask)
        .options(
            joinedload(ScanTask.items).joinedload(ScanTaskItem.product),
            joinedload(ScanTask.warehouse),
        )
        .get(task_id)
    )
    if task is None:
        abort(404)
    events = (
        session.query(ScanEvent)
        .filter_by(task_id=task_id)
        .order_by(ScanEvent.created_at.desc())
        .limit(10)
        .all()
    )
    manual_count = sum(1 for item in task.items if item.requires_manual)
    return render_template("scan_task_detail.html", task=task, events=events, manual_count=manual_count)


@scanning_bp.post("/scan-tasks/<int:task_id>/scan", endpoint="scan_task_scan")
def scan_task_scan(task_id):
    session = g.db
    task = (
        session.query(ScanTask)
        .options(joinedload(ScanTask.items).joinedload(ScanTaskItem.product))
        .get(task_id)
    )
    if task is None:
        abort(404)

    payload = request.get_json(silent=True) or request.form
    barcode = (payload.get("barcode") if payload else "").strip()
    qty_raw = payload.get("qty") if payload else None
    try:
        qty = float(qty_raw) if qty_raw is not None else 1.0
    except (TypeError, ValueError):
        qty = 1.0
    if qty <= 0:
        qty = 1.0

    item = next((i for i in task.items if i.barcode == barcode), None)
    if item is None:
        record_scan_event(task, None, qty, source="scan", message=f"{barcode} not in task", is_error=True)
        session.commit()
        return jsonify({"success": False, "error": "Barcode not in task"}), 400

    item.scanned_qty += qty
    over_scanned = item.scanned_qty > item.expected_qty

    update_scan_task_status(task)
    record_scan_event(
        task,
        item,
        qty,
        source="scan",
        message="over scanned" if over_scanned else "scan ok",
        is_error=over_scanned,
    )
    apply_inventory_movement(task, item, qty)
    session.commit()

    item_data = {
        "id": item.id,
        "barcode": item.barcode,
        "product_name": item.product.name if item.product else "Unknown product",
        "expected_qty": item.expected_qty,
        "scanned_qty": item.scanned_qty,
        "remaining_qty": max(item.expected_qty - item.scanned_qty, 0),
        "is_completed": item.is_completed,
        "is_over_scanned": over_scanned,
    }
    summary = {
        "total_items": task.total_items,
        "completed_items": task.completed_items,
        "all_completed": task.all_completed,
    }
    return jsonify({"success": True, "item": item_data, "summary": summary})


@scanning_bp.route("/scan-tasks/<int:task_id>/events", endpoint="scan_task_events")
def scan_task_events(task_id):
    session = g.db
    since_id = request.args.get("since_id", type=int)
    query = session.query(ScanEvent).filter_by(task_id=task_id)
    if since_id:
        query = query.filter(ScanEvent.id > since_id)
    events = query.order_by(ScanEvent.created_at.desc()).limit(20).all()
    return jsonify(
        [
            {
                "id": event.id,
                "created_at": event.created_at.isoformat(),
                "barcode": event.barcode,
                "qty": event.qty,
                "source": event.source,
                "message": event.message,
                "is_error": event.is_error,
            }
            for event in events
        ]
    )


@scanning_bp.route("/scan-tasks/<int:task_id>/export", endpoint="scan_task_export")
def scan_task_export(task_id):
    session = g.db
    task = (
        session.query(ScanTask)
        .options(joinedload(ScanTask.items).joinedload(ScanTaskItem.product))
        .get(task_id)
    )
    if task is None:
        abort(404)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Barcode", "Product", "Expected", "Scanned", "Remaining", "Status"])
    for item in task.items:
        writer.writerow(
            [
                item.barcode,
                item.product.name if item.product else "Unknown",
                item.expected_qty,
                item.scanned_qty,
                max(item.expected_qty - item.scanned_qty, 0),
                "OK" if item.is_completed else ("OVER" if item.is_over_scanned else "Pending"),
            ]
        )
    output = BytesIO(buffer.getvalue().encode("utf-8"))
    output.seek(0)
    filename = f"scan_task_{task.id}.csv"
    return send_file(output, mimetype="text/csv", as_attachment=True, download_name=filename)


@scanning_bp.post("/scan-tasks/<int:task_id>/manual", endpoint="scan_task_manual")
def scan_task_manual(task_id):
    session = g.db
    task = (
        session.query(ScanTask)
        .options(joinedload(ScanTask.items).joinedload(ScanTaskItem.product))
        .get(task_id)
    )
    if task is None:
        abort(404)
    payload = request.get_json(silent=True) or request.form
    item_id = payload.get("item_id")
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid item"}), 400
    item = next((i for i in task.items if i.id == item_id), None)
    if item is None:
        return jsonify({"success": False, "error": "Item not found"}), 404
    if not item.requires_manual:
        return jsonify({"success": False, "error": "Item supports barcode"}), 400
    qty_raw = payload.get("qty")
    try:
        qty = float(qty_raw)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid quantity"}), 400
    if qty <= 0:
        return jsonify({"success": False, "error": "Quantity must be positive"}), 400
    item.scanned_qty += qty
    update_scan_task_status(task)
    record_scan_event(task, item, qty, source="manual", message="manual entry")
    apply_inventory_movement(task, item, qty)
    session.commit()
    item_data = {
        "id": item.id,
        "barcode": item.barcode,
        "product_name": item.product.name if item.product else "Unknown product",
        "expected_qty": item.expected_qty,
        "scanned_qty": item.scanned_qty,
        "remaining_qty": max(item.expected_qty - item.scanned_qty, 0),
        "is_completed": item.is_completed,
        "is_over_scanned": item.is_over_scanned,
    }
    summary = {
        "total_items": task.total_items,
        "completed_items": task.completed_items,
        "all_completed": task.all_completed,
    }
    return jsonify({"success": True, "item": item_data, "summary": summary})
