from flask import Blueprint, render_template, request, flash, redirect, url_for
import requests
import base64

pdf_printers_bp = Blueprint("pdf_printers", __name__, url_prefix="/admin/pdf-printers")

PRINT_SERVER_URL = "http://109.104.213.14:8002"
API_KEY = "J6nPr+K28f2NCrpX8enXc8Q9dF5GhfnQFbGk/9jjS6E="

@pdf_printers_bp.route("/", methods=["GET", "POST"])
def pdf_printers_panel():
    printers = []
    error = None
    result = None
    # Зареждане на PDF принтерите от API
    try:
        resp = requests.get(f"{PRINT_SERVER_URL}/api/printers", headers={"X-API-KEY": API_KEY})
        resp.raise_for_status()
        all_printers = resp.json()
        # Ако API връща речник с ключ 'printers', вземи го, иначе очаквай списък
        if isinstance(all_printers, dict) and "printers" in all_printers:
            all_printers = all_printers["printers"]
        printers = [p for p in all_printers if isinstance(p, dict) and p.get("type") == "pdf"]
    except Exception as exc:
        error = f"Грешка при зареждане на принтери: {exc}"
    # Печат на PDF
    if request.method == "POST":
        printer_id = request.form.get("printer_id")
        copies = int(request.form.get("copies") or 1)
        pdf_file = request.files.get("pdf_file")
        if not printer_id or not pdf_file:
            flash("Изберете принтер и PDF файл!", "warning")
        else:
            try:
                pdf_b64 = base64.b64encode(pdf_file.read()).decode()
                payload = {
                    "pdf_base64": pdf_b64,
                    "copies": copies,
                    "duplex": request.form.get("duplex", "none"),
                    "scale": request.form.get("scale", "fit"),
                    "orientation": request.form.get("orientation", "portrait")
                }
                resp = requests.post(
                    f"{PRINT_SERVER_URL}/documents/{printer_id}/print-pdf",
                    headers={
                        "X-API-KEY": API_KEY,
                        "Content-Type": "application/json"
                    },
                    json=payload
                )
                result = resp.json()
                if resp.status_code == 200 and result.get("ok"):
                    flash("Успешно изпратен PDF за печат!", "success")
                else:
                    flash(f"Грешка при печат: {result}", "danger")
            except Exception as exc:
                flash(f"Грешка при печат: {exc}", "danger")
    return render_template(
        "admin_pdf_printers.html",
        printers=printers,
        error=error,
        result=result
    )
