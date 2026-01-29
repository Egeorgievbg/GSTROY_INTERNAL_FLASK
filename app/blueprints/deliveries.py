import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app.services.invoice_service import (
    InvoiceOcrService,
    build_match_lookup,
    match_vendor_code,
    normalize_invoice_payload,
)
from app.services.order_tasks import update_scan_task_status
from helpers import user_warehouse
from models import (
    ScanTask,
    ScanTaskItem,
    SupplierInvoice,
    SupplierInvoiceLine,
    Warehouse,
)


deliveries_bp = Blueprint("deliveries", __name__)

ALLOWED_INVOICE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


def _invoice_upload_dir():
    upload_root = Path(current_app.static_folder) / "uploads" / "invoices"
    upload_root.mkdir(parents=True, exist_ok=True)
    return upload_root


def _validate_invoice_upload(file_storage):
    if not file_storage or not file_storage.filename:
        raise ValueError("Моля, качете файл с фактура.")
    ext = Path(file_storage.filename).suffix.lower()
    if ext not in ALLOWED_INVOICE_EXTENSIONS:
        raise ValueError("Позволени са PDF, PNG, JPG и JPEG файлове.")
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    max_bytes = current_app.config.get("INVOICE_UPLOAD_MAX_BYTES", 15 * 1024 * 1024)
    if size > max_bytes:
        raise ValueError(f"Файлът е твърде голям (макс. {max_bytes // 1024} KB).")
    file_storage.stream.seek(0)
    return ext


@deliveries_bp.route("/deliveries", methods=["GET", "POST"])
@login_required
def deliveries_index():
    session = g.db
    if request.method == "POST":
        file = request.files.get("invoice_file")
        try:
            ext = _validate_invoice_upload(file)
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(url_for("deliveries.deliveries_index"))

        filename = f"invoice_{datetime.utcnow():%Y%m%d_%H%M%S}_{secrets.token_hex(4)}{ext}"
        upload_path = _invoice_upload_dir() / filename
        file.save(upload_path)
        rel_path = f"uploads/invoices/{filename}"

        invoice = SupplierInvoice(
            file_path=rel_path,
            ocr_status="processing",
            created_by_id=getattr(current_user, "id", None),
        )
        session.add(invoice)
        session.commit()

        try:
            ocr_service = InvoiceOcrService()

            def _append_page_log(page_num, info):
                try:
                    inv = session.get(SupplierInvoice, invoice.id)
                    logs = json.loads(inv.ocr_pages_log) if inv.ocr_pages_log else []
                    entry = {
                        "page": page_num,
                        "status": info.get("status"),
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                    if info.get("status") == "ok":
                        res = info.get("result") or {}
                        entry["line_items_count"] = len(res.get("line_items") or [])
                        entry["usage"] = info.get("usage") or {}
                    else:
                        entry["error"] = str(info.get("error"))[:1000]
                    logs.append(entry)
                    inv.ocr_pages_log = json.dumps(logs, ensure_ascii=False)
                    session.commit()
                except Exception:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    current_app.logger.exception("Failed to append OCR page log")

            raw_payload, usage = ocr_service.extract_invoice_data(upload_path, progress_callback=_append_page_log)
            normalized = normalize_invoice_payload(raw_payload)

            invoice.invoice_number = normalized["header"]["invoice_number"]
            invoice.issue_date = normalized["header"]["issue_date"]
            invoice.currency = normalized["header"]["currency"]
            invoice.vendor_name = normalized["vendor"]["name"]
            invoice.vendor_vat_id = normalized["vendor"]["vat_id"]
            invoice.vendor_iban = normalized["vendor"]["iban"]
            invoice.receiver_name = normalized["receiver"]["name"]
            invoice.receiver_vat_id = normalized["receiver"]["vat_id"]
            invoice.net_amount = normalized["totals"]["net_amount"]
            invoice.vat_amount = normalized["totals"]["vat_amount"]
            invoice.total_due = normalized["totals"]["total_due"]
            invoice.ocr_payload = json.dumps(raw_payload, ensure_ascii=False)
            invoice.ocr_status = "success"
            invoice.error_message = None

            session.query(SupplierInvoiceLine).filter_by(invoice_id=invoice.id).delete()
            vendor_codes = [item.get("article_no") for item in normalized["line_items"]]
            lookup = build_match_lookup(session, vendor_codes)

            for idx, item in enumerate(normalized["line_items"], start=1):
                if not (item.get("article_no") or item.get("description")):
                    continue
                product, method = match_vendor_code(item.get("article_no"), lookup)
                session.add(
                    SupplierInvoiceLine(
                        invoice_id=invoice.id,
                        row_index=idx,
                        vendor_code=item.get("article_no"),
                        description=item.get("description"),
                        quantity=item.get("quantity"),
                        unit=item.get("unit"),
                        unit_price=item.get("unit_price"),
                        total_row=item.get("total_row"),
                        matched_product_id=product.id if product else None,
                        match_method=method,
                    )
                )

            session.commit()
            flash("Фактурата е обработена успешно.", "success")
            return redirect(url_for("deliveries.delivery_detail", invoice_id=invoice.id))
        except Exception as exc:
            session.rollback()
            invoice = session.get(SupplierInvoice, invoice.id)
            if invoice:
                invoice.ocr_status = "failed"
                invoice.error_message = str(exc)[:1000]
                session.commit()
            flash("Грешка при обработка на фактурата.", "danger")
            return redirect(url_for("deliveries.delivery_detail", invoice_id=invoice.id))

    invoices = (
        session.query(SupplierInvoice)
        .options(joinedload(SupplierInvoice.lines))
        .order_by(SupplierInvoice.created_at.desc())
        .limit(50)
        .all()
    )
    # Build lightweight stats for each invoice to show in the index
    invoice_stats = {}
    for inv in invoices:
        total_lines = len(inv.lines)
        matched_lines = sum(1 for l in inv.lines if l.matched_product_id)
        total_qty = sum((l.quantity or 0) for l in inv.lines)
        matched_qty = sum((l.quantity or 0) for l in inv.lines if l.matched_product_id)
        unmatched_codes = [ (l.vendor_code or l.description or "").strip() for l in inv.lines if not l.matched_product_id ]
        from collections import Counter

        top_unmatched = [c for c, _ in Counter([c for c in unmatched_codes if c]).most_common(5)]
        pct_lines = (matched_lines / total_lines * 100) if total_lines else 0
        pct_qty = (matched_qty / total_qty * 100) if total_qty else 0
        invoice_stats[inv.id] = {
            "total_lines": total_lines,
            "matched_lines": matched_lines,
            "match_pct_lines": round(pct_lines, 1),
            "total_qty": total_qty,
            "matched_qty": matched_qty,
            "match_pct_qty": round(pct_qty, 1),
            "top_unmatched": top_unmatched,
        }

    return render_template("deliveries_index.html", invoices=invoices, invoice_stats=invoice_stats)


@deliveries_bp.route("/deliveries/<int:invoice_id>")
@login_required
def delivery_detail(invoice_id):
    session = g.db
    invoice = (
        session.query(SupplierInvoice)
        .options(
            joinedload(SupplierInvoice.lines).joinedload(SupplierInvoiceLine.matched_product),
            joinedload(SupplierInvoice.scan_task),
        )
        .get(invoice_id)
    )
    if not invoice:
        abort(404)

    lines_view = []
    for line in invoice.lines:
        product = line.matched_product
        internal_price = None
        if product:
            internal_price = (
                product.visible_price_unit_1
                or product.price_unit_1
                or product.price_unit_2
            )
        diff = None
        diff_pct = None
        if internal_price is not None and line.unit_price is not None:
            diff = internal_price - line.unit_price
            diff_pct = (diff / line.unit_price * 100) if line.unit_price else None
        lines_view.append(
            {
                "line": line,
                "product": product,
                "internal_price": internal_price,
                "diff": diff,
                "diff_pct": diff_pct,
            }
        )

    total_lines = len(invoice.lines)
    matched_lines = sum(1 for line in invoice.lines if line.matched_product_id)
    total_qty = sum((l.quantity or 0) for l in invoice.lines)
    matched_qty = sum((l.quantity or 0) for l in invoice.lines if l.matched_product_id)
    unmatched_lines = [l for l in invoice.lines if not l.matched_product_id]
    unmatched_count = len(unmatched_lines)
    # breakdown by match method
    match_method_counts = {}
    for l in invoice.lines:
        key = l.match_method or "unmatched"
        match_method_counts[key] = match_method_counts.get(key, 0) + 1
    # top unmatched vendor codes (for manual review / training)
    from collections import Counter

    top_unmatched = [c for c, _ in Counter([ (l.vendor_code or l.description or "").strip() for l in unmatched_lines if (l.vendor_code or l.description) ]).most_common(10)]
    default_warehouse = user_warehouse(current_user)
    warehouses = session.query(Warehouse).order_by(Warehouse.name).all()
    # parse per-page OCR logs for display
    ocr_pages_log = []
    try:
        if invoice.ocr_pages_log:
            ocr_pages_log = json.loads(invoice.ocr_pages_log)
    except Exception:
        current_app.logger.exception("Failed to parse invoice.ocr_pages_log")

    return render_template(
        "delivery_detail.html",
        invoice=invoice,
        lines_view=lines_view,
        total_lines=total_lines,
        matched_lines=matched_lines,
        total_qty=total_qty,
        matched_qty=matched_qty,
        unmatched_count=unmatched_count,
        match_method_counts=match_method_counts,
        top_unmatched=top_unmatched,
        ocr_pages_log=ocr_pages_log,
        warehouses=warehouses,
        default_warehouse_id=default_warehouse.id if default_warehouse else None,
    )


@deliveries_bp.post("/deliveries/<int:invoice_id>/scan-task")
@login_required
def delivery_create_scan_task(invoice_id):
    session = g.db
    invoice = (
        session.query(SupplierInvoice)
        .options(joinedload(SupplierInvoice.lines).joinedload(SupplierInvoiceLine.matched_product))
        .get(invoice_id)
    )
    if not invoice:
        abort(404)
    if invoice.scan_task_id:
        flash("Вече има създаден scan task.", "warning")
        return redirect(url_for("deliveries.delivery_detail", invoice_id=invoice.id))

    warehouse_id = request.form.get("warehouse_id", type=int)
    if not warehouse_id:
        default_wh = user_warehouse(current_user)
        warehouse_id = default_wh.id if default_wh else None
    if not warehouse_id:
        flash("Изберете склад за приемането.", "warning")
        return redirect(url_for("deliveries.delivery_detail", invoice_id=invoice.id))

    task_name = f"Приход {invoice.invoice_number or f'#{invoice.id}'}"
    task = ScanTask(
        name=task_name,
        type="receipt",
        status="open",
        warehouse_id=warehouse_id,
        created_by_id=getattr(current_user, "id", None),
    )
    session.add(task)
    session.flush()

    aggregated = {}
    for line in invoice.lines:
        product = line.matched_product
        if product:
            barcode = (product.barcode or product.item_number or "").strip()
        else:
            barcode = (line.vendor_code or "").strip()
        if not barcode:
            continue
        qty = line.quantity or 0
        if qty <= 0:
            continue
        key = (barcode, product.id if product else None)
        bucket = aggregated.setdefault(
            key,
            {
                "barcode": barcode,
                "product_id": product.id if product else None,
                "qty": 0.0,
                "unit": product.main_unit if product else line.unit,
            },
        )
        bucket["qty"] += qty

    if not aggregated:
        flash("Няма редове за сканиране.", "warning")
        return redirect(url_for("deliveries.delivery_detail", invoice_id=invoice.id))

    for payload in aggregated.values():
        session.add(
            ScanTaskItem(
                task_id=task.id,
                product_id=payload["product_id"],
                barcode=payload["barcode"],
                expected_qty=payload["qty"],
                scanned_qty=0.0,
                unit=payload["unit"],
            )
        )

    update_scan_task_status(task)
    invoice.scan_task_id = task.id
    session.commit()
    flash("Scan task е създаден.", "success")
    return redirect(url_for("scanning.scan_task_detail", task_id=task.id))
