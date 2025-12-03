from datetime import datetime

from flask import g
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from constants import (
    STOCK_ORDER_AUTOMATION_STATUSES,
    STOCK_ORDER_EAGER_OPTIONS,
    STOCK_ORDER_STATUSES,
)
from models import (
    InventoryMovement,
    Product,
    ScanEvent,
    ScanTask,
    ScanTaskItem,
    ServicePoint,
    StockOrder,
    StockOrderAssignment,
    User,
    user_service_points,
)


def user_service_point_ids(user: User | None):
    if not user:
        return set()
    session = getattr(g, "db", None)
    if session and getattr(user, "id", None):
        rows = session.execute(
            select(user_service_points.c.service_point_id).where(user_service_points.c.user_id == user.id)
        ).scalars()
        return set(rows)
    if getattr(user, "service_points", None):
        return {sp.id for sp in user.service_points}
    return set()


def order_service_point_ids(order: StockOrder):
    return {item.service_point_id for item in order.items if item.service_point_id}


def update_stock_order_status(order: StockOrder):
    items = order.items or []
    if not items:
        order.status = "new"
        order.updated_at = datetime.utcnow()
        return order.status
    all_delivered = all(item.quantity_delivered >= item.quantity_ordered > 0 for item in items)
    any_delivered = any(item.quantity_delivered > 0 for item in items)
    if all_delivered:
        new_status = "delivered"
    elif any_delivered:
        new_status = "partially_delivered"
    else:
        all_prepared = all(item.quantity_prepared >= item.quantity_ordered > 0 for item in items)
        any_prepared = any(item.quantity_prepared > 0 for item in items)
        if all_prepared:
            new_status = "ready_for_handover"
        elif any_prepared:
            new_status = "in_progress"
        elif order.assignments:
            new_status = "assigned"
        else:
            new_status = "new"
    order.status = new_status
    order.updated_at = datetime.utcnow()
    return new_status


def ensure_scan_task_for_order(order: StockOrder, service_point_id: int, user: User | None):
    session = g.db
    task = (
        session.query(ScanTask)
        .filter(
            ScanTask.stock_order_id == order.id,
            ScanTask.service_point_id == service_point_id,
            ScanTask.type == "stock_order_preparation",
        )
        .order_by(ScanTask.id.asc())
        .first()
    )
    service_point = session.query(ServicePoint).get(service_point_id)
    if task is None:
        task = ScanTask(
            name=f"SO {order.external_id or order.id} - {service_point.name if service_point else service_point_id}",
            type="stock_order_preparation",
            stock_order_id=order.id,
            service_point_id=service_point_id,
            created_by_id=user.id if user else None,
        )
        session.add(task)
        session.flush()
        for order_item in order.items:
            if order_item.service_point_id != service_point_id:
                continue
            barcode = ""
            if order_item.product and order_item.product.barcode:
                barcode = order_item.product.barcode.split(",")[0].strip()
            else:
                barcode = f"ITEM-{order_item.id}"
            task.items.append(
                ScanTaskItem(
                    product_id=order_item.product_id,
                    barcode=barcode,
                    expected_qty=order_item.quantity_ordered,
                    scanned_qty=order_item.quantity_prepared,
                    unit=order_item.unit or (order_item.product.main_unit if order_item.product else "pcs"),
                )
            )
    else:
        for scan_item in task.items:
            match = next(
                (
                    itm
                    for itm in order.items
                    if itm.product_id == scan_item.product_id and itm.service_point_id == service_point_id
                ),
                None,
            )
            if match:
                scan_item.expected_qty = match.quantity_ordered
                scan_item.scanned_qty = match.quantity_prepared
    session.flush()
    return task


def build_service_point_candidates():
    session = g.db
    users = (
        session.query(User)
        .options(joinedload(User.service_points))
        .filter(User.can_prepare_orders.is_(True))
        .order_by(User.full_name)
        .all()
    )
    mapping = {}
    for user in users:
        for sp in user.service_points:
            mapping.setdefault(sp.id, []).append(user)
    for sp_users in mapping.values():
        sp_users.sort(key=lambda u: u.full_name)
    return mapping


def stock_order_status_counts(session):
    counts = {status: 0 for status in STOCK_ORDER_STATUSES}
    rows = session.query(StockOrder.status, func.count(StockOrder.id)).group_by(StockOrder.status).all()
    for status, count in rows:
        counts[status] = count
    return counts


def assignment_load_counts(session):
    rows = (
        session.query(StockOrderAssignment.user_id, func.count(StockOrderAssignment.id))
        .join(StockOrder, StockOrder.id == StockOrderAssignment.stock_order_id)
        .filter(StockOrder.status.in_(STOCK_ORDER_AUTOMATION_STATUSES))
        .group_by(StockOrderAssignment.user_id)
        .all()
    )
    return {user_id: count for user_id, count in rows}


def get_stock_order_with_details(order_id):
    query = g.db.query(StockOrder)
    for option in STOCK_ORDER_EAGER_OPTIONS:
        query = query.options(option)
    return query.get(order_id)


def attach_service_point_sections(orders):
    for order in orders:
        sections = {}
        for item in order.items:
            key = item.service_point_id or 0
            if key not in sections:
                sections[key] = {
                    "service_point": item.service_point,
                    "items": [],
                }
            sections[key]["items"].append(item)
        order.service_point_sections = list(sections.values())


def stock_order_erp_input_payload(order: StockOrder):
    return {
        "order_id": order.id,
        "external_id": order.external_id,
        "warehouse": order.warehouse.code if order.warehouse else None,
        "type": order.type,
        "client": {
            "name": order.client_name,
            "phone": order.client_phone,
            "address": order.client_address,
        },
        "delivery": {
            "date": order.delivery_date.isoformat() if order.delivery_date else None,
            "time": order.delivery_time.isoformat() if order.delivery_time else None,
            "address": order.delivery_address,
        },
        "items": [
            {
                "stock_order_item_id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else None,
                "service_point": item.service_point.code if item.service_point else None,
                "unit": item.unit,
                "quantity_ordered": item.quantity_ordered,
            }
            for item in order.items
        ],
    }


def stock_order_erp_output_payload(order: StockOrder):
    return {
        "order_id": order.id,
        "status": order.status,
        "versus_status": order.versus_status,
        "items": [
            {
                "stock_order_item_id": item.id,
                "product_id": item.product_id,
                "quantity_ordered": item.quantity_ordered,
                "quantity_prepared": item.quantity_prepared,
                "quantity_delivered": item.quantity_delivered,
            }
            for item in order.items
        ],
        "ppp_document": {
            "versus_ppp_id": order.ppp_document.versus_ppp_id if order.ppp_document else None,
            "status": order.ppp_document.status if order.ppp_document else None,
        },
    }


def find_product_by_barcode(session, code: str):
    barcode = (code or "").strip()
    if not barcode:
        return None
    product = session.query(Product).filter(Product.barcode == barcode).first()
    if product:
        return product
    candidates = session.query(Product).filter(Product.barcode.contains(barcode)).all()
    for candidate in candidates:
        if not candidate.barcode:
            continue
        parts = [part.strip() for part in candidate.barcode.split(",")]
        if barcode in parts:
            return candidate
    return None


def update_scan_task_status(task: ScanTask):
    if task.all_completed:
        task.status = "completed"
    elif any(item.scanned_qty > 0 for item in task.items):
        task.status = "in_progress"
    else:
        task.status = "open"
    task.updated_at = datetime.utcnow()


def record_scan_event(task, item, qty, source="scan", message=None, is_error=False):
    event = ScanEvent(
        task_id=task.id,
        item_id=item.id if item else None,
        barcode=item.barcode if item else None,
        qty=qty,
        source=source,
        message=message,
        is_error=is_error,
    )
    g.db.add(event)
    return event


def apply_inventory_movement(task, item, qty):
    if not task.warehouse_id or not item or not item.product:
        return
    delta = 0.0
    if task.type == "receipt":
        delta = qty
    elif task.type == "issue":
        delta = -qty
    elif task.type == "inventory":
        delta = 0.0
    if delta == 0:
        return
    movement = InventoryMovement(
        task_id=task.id,
        product_id=item.product.id,
        warehouse_id=task.warehouse_id,
        quantity=delta,
        movement_type=task.type,
    )
    g.db.add(movement)
