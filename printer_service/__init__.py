import json
import os
from typing import Any, Dict, Optional

import urllib.request
from urllib.error import HTTPError, URLError

from flask import Blueprint, abort, g, jsonify, request
from flask_login import current_user, login_required

from models import Printer, User

LABEL_SERVER_TIMEOUT = float(os.environ.get("ERP_LABEL_SERVER_TIMEOUT", "6"))
LABEL_SERVER_STATUS_TIMEOUT = float(os.environ.get("ERP_LABEL_STATUS_TIMEOUT", "2"))


def _user_warehouse_id() -> Optional[int]:
    if not current_user or not getattr(current_user, "is_authenticated", False):
        return None
    session = getattr(g, "db", None)
    if not session:
        return None
    user = session.get(User, current_user.id)
    if not user:
        return None
    warehouse = getattr(user, "assigned_warehouse", None)
    if warehouse:
        return warehouse.id
    warehouse = getattr(user, "default_warehouse", None)
    if warehouse:
        return warehouse.id
    return None

printer_bp = Blueprint("printers", __name__, url_prefix="/printer-hub")


def _sanitize_text(value: Optional[str], max_len: int = 64) -> str:
    if not value:
        return ""
    text = (
        str(value)
        .replace("^", " ")
        .replace("~", " ")
        .replace("\n", " ")
        .replace("\r", " ")
    )
    return text[:max_len]


def _clamp_copies(value: Any, max_copies: int = 50) -> int:
    try:
        copies = int(value)
    except (TypeError, ValueError):
        copies = 1
    copies = max(1, min(copies, max_copies))
    return copies


def get_printers_for_warehouse(session, warehouse_id: int) -> list[Printer]:
    if not warehouse_id:
        return []
    return (
        session.query(Printer)
        .filter(Printer.warehouse_id == warehouse_id, Printer.is_active.is_(True))
        .order_by(Printer.is_default.desc(), Printer.name)
        .all()
    )


def _printer_server(printer: Printer) -> Optional[str]:
    if not printer:
        return None
    if printer.server_url:
        base = printer.server_url.strip()
    else:
        warehouse_url = getattr(printer.warehouse, "printer_server_url", None)
        base = (warehouse_url or "").strip()
    if not base:
        return None
    if not base.lower().startswith(("http://", "https://")):
        base = f"http://{base}"
    return base.rstrip("/")


def _send_label_request(printer: Printer, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    base = _printer_server(printer)
    if not base:
        raise RuntimeError("Не е зададен етикетен сървър за този принтер.")
    url = f"{base.rstrip('/')}/{endpoint.lstrip('/')}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=LABEL_SERVER_TIMEOUT) as resp:
            return json.load(resp)
    except HTTPError as exc:
        raise RuntimeError(f"Грешка от label сървъра: {exc}") from exc
    except URLError as exc:
        raise RuntimeError(f"Не може да се свърже с label сървъра: {exc}") from exc


def get_printer_status(printer: Printer) -> Dict[str, Any]:
    base = _printer_server(printer)
    if not base:
        return {"online": False, "error": "Липсва URL към етикетния сървър"}
    url = f"{base}/printers/{printer.ip_address}/status"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=LABEL_SERVER_STATUS_TIMEOUT) as resp:
            return json.load(resp)
    except HTTPError as exc:
        return {"online": False, "error": str(exc)}
    except URLError as exc:
        return {"online": False, "error": str(exc)}


@printer_bp.route("/print-product", methods=["POST"])
@login_required
def print_product_label():
    payload = request.get_json(silent=True) or {}
    printer_id = payload.get("printer_id")
    name = _sanitize_text(payload.get("name"))
    barcode = _sanitize_text(payload.get("barcode"))
    copies = _clamp_copies(payload.get("copies", 1))
    quantity = payload.get("quantity")
    if not printer_id:
        abort(400, "printer_id is required")
    if not name and not barcode:
        abort(400, "name or barcode is required")
    session = g.db
    printer = session.get(Printer, printer_id)
    warehouse_id = _user_warehouse_id()
    if not printer or not printer.is_active or printer.warehouse_id != warehouse_id:
        abort(404, "printer not found")
    label_payload = {
        "name": name,
        "barcode": barcode,
        "quantity": quantity,
        "copies": copies,
        "unit_info": payload.get("unit_info"),
    }
    try:
        result = _send_label_request(printer, f"printers/{printer.ip_address}/print-product-label", label_payload)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "printer": printer.ip_address, "message": result.get("message", "")})


@printer_bp.route("/print-list", methods=["POST"])
@login_required
def print_list_label():
    payload = request.get_json(silent=True) or {}
    printer_id = payload.get("printer_id")
    name = _sanitize_text(payload.get("name"))
    qr_data = _sanitize_text(payload.get("qr_data"))
    copies = _clamp_copies(payload.get("copies", 1))
    if not printer_id:
        abort(400, "printer_id is required")
    if not name and not qr_data:
        abort(400, "name or qr_data is required")
    session = g.db
    printer = session.get(Printer, printer_id)
    warehouse_id = _user_warehouse_id()
    if not printer or not printer.is_active or printer.warehouse_id != warehouse_id:
        abort(404, "printer not found")
    label_payload = {
        "name": name,
        "qr_data": qr_data,
        "copies": copies,
    }
    try:
        result = _send_label_request(printer, f"printers/{printer.ip_address}/print-list-label", label_payload)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "printer": printer.ip_address, "message": result.get("message", "")})
