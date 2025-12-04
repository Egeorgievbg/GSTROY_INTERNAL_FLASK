{% raw %}
# GSTROY Mini Internal — Production-ready Flask ERP Demo

This repository houses a Flask-based stock-order fulfillment simulator with PPP document generation, scan tracking, and academy/administration dashboards. The goal is to present a complete working example that can be referenced when porting the experience to Django 4.2.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture & Core Components](#architecture--core-components)
3. [Stock-order Workflow](#stock-order-workflow)
4. [PPP document lifecycle](#ppp-document-lifecycle)
5. [Scanning & event history](#scanning--event-history)
6. [Academy / Admin extras](#academy--admin-extras)
7. [Local setup](#local-setup)
8. [Running & debugging](#running--debugging)
9. [Database / seed data](#database--seed-data)
10. [Testing](#testing)
11. [Contribution & GitHub publish](#contribution--github-publish)
12. [Key references](#key-references)

---

## Project Overview

- **Language / Framework:** Python 3.11+, Flask + Blueprint structure.
- **Purpose:** Demonstrate an ERP-like flow covering stock order creation, preparation, handover with signed PPPs, scan tracking, and a lightweight academy/content hub. The README, code, and views intentionally mirror what teams might rebuild in Django 4.2.
- **Tone:** production-grade (transactions, logging, document history), but intentionally simple enough for demonstration and follow-up learning.

## Architecture & Core Components

| Layer | Description |
| --- | --- |
| **app/\*** | Flask imperative code grouped in blueprints (`orders`, `academy`, `printer`, etc.). Each blueprint exposes REST endpoints and template renders. |
| **models.py** | SQLAlchemy model graph: `StockOrder`, `StockOrderItem`, `ScanTask`, `ScanEvent`, `PPPDocument`, `User`, etc. Relationships power eager-loading in views. |
| **constants.py** | Shared constants (PPP output paths, bootstrap helpers, stock order status labels). |
| **services/** | `order_tasks.py` and other helpers encapsulate domain logic (ERP payloads, status updates, scan recording). |
| **templates/** | Twig-like Jinja UI for dashboards and handovers, with components like `stock_order_handover.html`, `stock_order_ppp.html`, and academy pages. |
| **utils.py** | Shared helpers: PDF generation (`generate_ppp_pdf`), signature persistence, normalization utilities. |

## Stock-order Workflow

1. `/stock-orders` dashboard loads 15 latest orders (non-delivered by default). The view uses `orders.stock_orders_dashboard`.
2. `stock_order_prepare` allows warehouse staff to sync `ScanTask` progress vs. `StockOrderItem` via manual entry/editing. The API logs preparation events using `record_scan_event`.
3. `stock_order_handover` (see `app/blueprints/orders.py`) is the signature-ready screen: it keeps analytics badges (`total_ordered`, `prepared_total`, `deliverable_total`), accepts per-item delivery quantities, captures a signature graphic via SignaturePad (stored by `utils.save_signature_image`), and persists updates in SQLAlchemy.
4. Completed handovers update `StockOrder.status`, `delivered_at`, and emit _contextual logs_ through `_log_order_context`.

## PPP document lifecycle

- Each handover creates a new `PPPDocument` record that stores:
  - `versus_ppp_id` (sequential identifier),
  - `pdf_url` (draft copy) and `signed_pdf_url` (with signature image),
  - `signature_image` path (e.g., `static/ppp/signature_<order>_<token>.png`),
  - `status`, timestamps, and relations back to `StockOrder`.
- This flow is implemented inside `stock_order_handover`. The PDF creation uses `utils.generate_ppp_pdf`. Signed PDFs are prioritized when viewing via `/stock-orders/<order>/ppp/pdf`.
- `/stock-orders/<order>/ppp` renders:
  - latest PPP summary and signed PDF/PNG previews,
  - the order's full item list (read-only),
  - PPP history with quick download buttons,
  - scanning history pulled from `ScanTask`/`ScanEvent` to prove chain of custody.
- `/stock-orders/completed` now surfaces every order that contains at least one PPP document, not just delivered status, making it a PPP archive (cards or table view toggle).

## Scanning & event history

- `ScanTask`/`ScanTaskItem` models track per-order picks, updates, and statuses (`open`, `in_progress`, `completed`).
- Events are logged via `record_scan_event`, capturing `qty`, `source` (`scan`/`manual`), `message`, and any errors.
- The PPP page eager-loads each scan task plus its events (and the event’s item/product) to display a full audit trail without reattaching or hitting `DetachedInstanceError`.

## Academy & Admin extras

- `printer_service/` simulates an external label server, showcasing how list/product labels could be sent.
- The academy blueprint builds knowledge base, push notifications, and progressive reading tracking via `UserContentProgress`.
- Admin templates (`templates/admin_academy.html`, `templates/admin_panel.html`) expose quick insights and controls (academy pushes, PPP stats, scanning dashboards).

## Local setup

```powershell
python -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
.\\setup.ps1      # seeds DB + assets
.\\setup.ps1 -Run  # starts dev server with reload
```

- Environment variables:
  - `ERP_DEMO_DATABASE_URL`: defaults to `sqlite:///erp_demo.db`; swap to Postgres if needed.
  - `ERP_DEMO_SECRET_KEY`: change from `change-me`.
  - `ERP_DEMO_DEFAULT_PASSWORD`: used when seeding `admin/demo1234`.
  - `SIGNATURE_MAX_BYTES`: caps PNG uploads (default `200000` bytes).

## Running & debugging

- Launch via `python -m flask run --reload` after activating `.venv`.
- Debug endpoints:
  - `/stock-orders/<id>/prepare`
  - `/stock-orders/<id>/handover`
  - `/stock-orders/<id>/ppp`
  - `/stock-orders/<id>/erp-input|output`
  - `/academy/dashboard`
  - `/printer-hub/*` label mocks.
- Logs: `_log_order_context` captures per-order metrics to the Flask logger for handover tracking.

## Database & seed data

- `database.py` ensures schema additions (PPP columns, last handover timestamps) and seeds:
  - brands, service points, warehouses.
  - `StockOrder` examples (`2200923775`, `2200923777`).
  - `ScanTask`, `ScanTaskItem`, `PPPDocument` history.
- Running `python database.py` from `setup.ps1` loads the demo dataset.

## Testing

- No automated test suite is wired yet, but you can add `pytest` or `unittest` modules near helpers like `helpers.py`, `utils.py`, `app/services/order_tasks.py`.
- Suggested quick checks:
  - `python -m flask shell` to load session and ensure PPP creation paths work.
  - `pytest tests` once new tests are added.

## Contribution & GitHub publish

1. Keep branches per feature/fix (e.g., `feature/ppp-history`).
2. Run linters/testers before committing changes tied to PPP generation or scanning tracking.
3. After local changes:
   ```bash
   git add .
   git commit -m "feat: describe PPP history"
   git push origin <your-branch>
   ```
4. Create a PR referencing the Django 4.2 port intent; highlight how PPP docs, scan history, and academy modules map to Django apps/views.

> 📌 _Note:_ I can’t push commits directly to GitHub from this environment, so please run the above push/PR steps after reviewing the changes.

## Key references

- `app/blueprints/orders.py`: dashboards, handovers, PPP viewing, PDF download, scan history enrichment.
- `models.py`: `StockOrder`, `StockOrderItem`, `PPPDocument`, `ScanTask`, `ScanEvent`.
- `templates/stock_order_handover.html` & `stock_order_ppp.html`: UX for deliveries and PPP archives.
- `utils.py`: `generate_ppp_pdf`, `save_signature_image`, signature constraints.
- `app/services/order_tasks.py`: helpers used by multiple blueprints (status updates, ERP payloads, scan logging).

---

If you’re showcasing this to a Django audience, pair this README with a short cheat sheet that maps Flask blueprints/model relationships to Django views/apps and highlights how the PPP + scan history sections should be ported (models, templates, class-based views, signals).
{% endraw %}
