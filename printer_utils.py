import json
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin

from gstroy_constants import PRINTER_AUTH_HEADERS, PRINTER_REQUEST_TIMEOUT
from models import Printer


def active_printers_for_warehouse(session, warehouse_id=None):
    base = (
        session.query(Printer)
        .filter(Printer.is_active.is_(True))
        .order_by(Printer.is_default.desc(), Printer.name)
    )
    if warehouse_id:
        specialized = base.filter(Printer.warehouse_id == warehouse_id).all()
        if specialized:
            return specialized
    return base.all()


def resolve_printer_for_warehouse(session, warehouse_id, printer_id=None):
    if printer_id is not None:
        try:
            printer_id = int(printer_id)
        except (TypeError, ValueError):
            return None
        printer = session.get(Printer, printer_id)
        if printer and printer.is_active:
            return printer
        return None
    if warehouse_id:
        printer = (
            session.query(Printer)
            .filter(Printer.warehouse_id == warehouse_id, Printer.is_active.is_(True))
            .order_by(Printer.is_default.desc(), Printer.id)
            .first()
        )
        if printer:
            return printer
    return (
        session.query(Printer)
        .filter(Printer.is_active.is_(True))
        .order_by(Printer.is_default.desc(), Printer.id)
        .first()
    )


def printer_server_base(printer):
    if not printer or not printer.warehouse:
        return None
    url = (printer.server_url or printer.warehouse.printer_server_url or "").strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = f"http://{url}"
    return url.rstrip("/")

def _build_printer_headers(access_key, content_type=None):
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    key = (access_key or "").strip()
    if not key:
        return headers
    for header in PRINTER_AUTH_HEADERS:
        headers.setdefault(header, key)
    headers["Authorization"] = f"Bearer {key}"
    return headers


def send_printer_request(printer, endpoint, payload):
    url_base = printer_server_base(printer)
    if not url_base:
        raise RuntimeError("Принтерът няма конфигуриран сървър.")
    url = urljoin(f"{url_base}/", endpoint.lstrip("/"))
    body = json.dumps(payload).encode("utf-8")
    headers = _build_printer_headers(getattr(printer, "access_key", None), "application/json")
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=PRINTER_REQUEST_TIMEOUT) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Грешка при печат: {exc}") from exc
    except URLError as exc:
        raise RuntimeError(f"Невъзможно е да се свърже принтерът: {exc}") from exc
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Принтерът върна грешка.")
    return result
