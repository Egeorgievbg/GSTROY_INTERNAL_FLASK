# GSTROY Mini Internal — AI Agent Documentation

## Overview
This document provides a comprehensive, file-by-file and line-by-line overview of the GSTROY Mini Internal project. It is intended as a reference for AI agents and developers to understand the structure, purpose, and interconnections of all components in the codebase.

---

## Top-Level Structure

- **app.py**: Main Flask application entry point. Initializes the app, loads configs, and registers blueprints.
- **constants.py**: Shared constants for the project (paths, status labels, etc).
- **database.py**: Handles database schema, migrations, and seeding demo data.
- **dedupe_brands.py**: Script for deduplicating brand data.
- **docker-compose.yml**: Docker Compose file for services like Elasticsearch.
- **extensions.py**: Initializes Flask extensions (SQLAlchemy, LoginManager, etc).
- **gstroy_constants.py**: Additional constants specific to GSTROY logic.
- **helpers.py**: Utility functions for various helpers used across the app.
- **models.py**: SQLAlchemy ORM models for all business entities (StockOrder, User, PPPDocument, etc).
- **printer_utils.py**: Utilities for printer management and label generation.
- **requirements.txt**: Python dependencies for the project.
- **test.html**: Test HTML file, likely for UI or template experiments.
- **utils.py**: Core utility functions (PDF generation, signature handling, normalization, etc).

---

## Key Folders

### app/
- **__init__.py**: App factory, blueprint registration, and extension setup.
- **blueprints/**: Contains all Flask blueprints, each handling a domain (orders, products, academy, printers, etc).
  - **admin.py**: Admin panel routes and logic.
  - **auth.py**: Authentication (login, logout, user management).
  - **catalog.py, catalog_sync.py, catalog_utils.py**: Product catalog and sync logic.
  - **deliveries.py**: Invoice/receipt intake, OCR, and scan task generation.
  - **logistics.py**: Pallet, transfer, and logistics management.
  - **main.py**: Main dashboard and landing routes.
  - **orders.py**: Stock order dashboards, handover, PPP document lifecycle.
  - **pdf_printers.py**: PDF printer management via REST API (new logic).
  - **products.py**: Product CRUD and search.
  - **scanning.py**: Scan task and event management.
  - **academy/**: Academy/knowledge base blueprints.
- **services/**: Domain logic and integrations.
  - **art_info_service.py**: Art info sync and enrichment.
  - **feed_sync_service.py**: Product feed sync.
  - **invoice_service.py**: Invoice OCR and PDF/image extraction.
  - **order_tasks.py**: Stock order and scan task helpers.
  - **pricemind_sync_scheduler.py, pricemind_sync_service.py**: Price monitoring and sync.
  - **search_indexer.py, search_service.py**: Elasticsearch integration.
  - **sync_service.py**: General sync logic.

### printer_service/
- **__init__.py**: Simulated external label server for ZPL printers.

### scripts/
- **reindex_products.py**: Script to rebuild Elasticsearch product index.

### static/
- **css/**: Stylesheets (admin.css, base.css, custom.css).
- **images/**: Static images (e.g., no_image.png).
- **js/**: JavaScript files (e.g., product_detail.js).
- **ppp/**: Generated PPP PDFs and signatures.
- **uploads/**: Uploaded files (invoices, etc).
- **styles.css**: Main stylesheet.

### templates/
- **base.html**: Main layout, navigation, and includes for all pages.
- **admin_*.html**: Admin panel templates (users, roles, printers, products, etc).
- **academy/**: Academy/knowledge base templates.
- **admin/**: Admin dashboard and editor templates.
- **404.html, index.html, login.html, etc**: Main UI pages.
- **_*.html**: Partial templates/components.

### docs/
- **architecture.md**: System architecture overview.
- **blueprints.md**: Blueprint structure and routing.
- **printer-module.md**: Printer integration details.

---

## File-by-File and Line-by-Line Details

For a full breakdown of each file and its lines, see the README and docs/ folder. Each file is structured with clear comments and logical separation of concerns. Models, services, and blueprints are grouped by domain, and templates are organized by feature.

---

## How to Use This Documentation
- Use this file as a map to quickly locate logic, templates, and services.
- For API and workflow details, see the README.md and docs/.
- For new features, follow the blueprint and service structure for maintainability.

---

_Last updated: January 28, 2026_
