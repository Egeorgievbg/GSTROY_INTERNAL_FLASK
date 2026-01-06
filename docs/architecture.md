# Архитектура на GSTROY Mini Internal

Документът описва всички съставни части на системата и връзките между тях. Целта е да се даде професионално, техническо резюме, подходящо за разработчици, които ще пренасят идеята във Flask или Django.

## 1. Основна идея
GSTROY Mini Internal е демонстрационен ERP/CMMS проект, симулиращ управление на складови поръчки, генериране на подписани ППП документи и проследяване на сканиранията през целия процес. Системата се стреми да бъде достъпна и обучителна, затова събра всички важни части на един MVC-like стек.

## 2. Стек и структура
| Отговорност | Компоненти |
|-------------|------------|
| HTTP сървър | Flask (`app.py`, `app/__init__.py`). Зарежда бекенд логика, байндва Blueprints и регистрира Flask-Login, CSRF, базата. |
| Модели | `models.py` съдържа SQLAlchemy декларации: `StockOrder`, `StockOrderItem`, `ScanTask`, `ScanEvent`, `PPPDocument`, `User`, `ContentItem` и др. |
| Сервизни слоеве | `app/services/order_tasks.py` и помощни функции изпълняват общи операции: актуализиране на статуси, ERP payload-и, сканирания. |
| Помощници | `utils.py` (PDF генериране, запис на подписи, помощни нормализации), `helpers.py` (parse_float, safe_redirect и др.). |
| Константи | `constants.py` дефинира PPP пътища, статусни етикети, Bootstrap класове и font fallback схема. |
| Templates | Jinja2 файлове под `templates/` строят потребителския интерфейс (dashboards, handover, PPP, academy, admin). |
| Статични ресурси | `static/` съдържа CSS, подписи, PDF файлове в `static/ppp/`, цифрови активи и JS. |
| Printer hub | `printer_service/` симулира външен label сървър чрез Flask blueprint (извиквания към `/printer-hub`). |
| Docs | README + `docs/` обясняват архитектурата и процесите. |

## 3. Поток на Stock Order
1. **Dashboard** `/stock-orders` зарежда последните 15 не доставени поръчки (може да филтрира по статус и тип) и визуализира `StockOrder`, `StockOrderItem`, `assignments`, `ppp_documents`.
2. **Prepare** (`/stock-orders/<id>/prepare`) позволява:
   - сканиране на артикули (`ScanTask` + `ScanTaskItem`),
   - ръчни промени на подготовката (`stock_order_manual` API),
   - логване на `ScanEvent`.
3. **Handover** (`/stock-orders/<id>/handover`) събира:
   - delivery количества, recipient,
   - SignaturePad → `save_signature_image`,
   - генерация на PPP PDF (`generate_ppp_pdf`),
   - запис на `PPPDocument`,
   - обновяване на `StockOrder.status`, `delivered_at`, `delivered_by`.
4. **PPP архив** (`/stock-orders/<id>/ppp` + `/completed`) показва:
   - най-новия документ (ако има) и iframe на последния signed PDF,
   - детайлна история на всички PPP-та,
   - scan history от `ScanTask` и `ScanEvent`.
5. **ERP вход/изход** (`/stock-orders/<id>/erp-input` и `erp-output`) са JSON API-та, за да може външни системи да получават статуса и артикули на поръчката.

## 4. PPP документи
- `PPPDocument` съдържа `pdf_url`, `signed_pdf_url`, `signature_image`, `versus_ppp_id`, `status`, timestamps и FK към `StockOrder`.
- Всеки handover:
  1. Създава уникален identifier (`{order.id}_{timestamp}_draft` / `_signed`).
  2. Генерира PDF (ReportLab) и записва PNG на подписа в `static/ppp`.
  3. Записва запис в `PPPDocument` (статус, signature path, URLs).
  4. Помага на `/stock-orders/<id>/ppp/pdf` да избере signed PDF, ако има.
- Версията на PPP (чернова vs. подписан) и историята се пазят, за да се вижда кой документ е бил валидиран кога.

## 5. Scanning и лог на събития
- `ScanTask` представлява задача за сканиране, `ScanTaskItem` – очакван артикул.
- При всяко сканиране или ръчна промяна:
  - `record_scan_event` записва `ScanEvent` (qty, barcode, source, message, is_error).
  - `ScanEvent` има `relationship` към `ScanTaskItem`, за да се знае кой артикул.
- В PPP страницата се eager-loadва:
  - `ScanTask.events` + `ScanEvent.item.product`,
  - `ScanTask.items` + `ScanTaskItem.product`,
  - `ScanTask.created_by`.
- Това гарантира, че историята на сканиранията може да се визуализира без `DetachedInstanceError`.

## 6. HPC и Academy
- `academy` blueprint създава:
  - `ContentItem` (статии/новини), `UserContentProgress`,
  - feed с `stories`, `guides`, `news`,
  - API за mark-as-read, push mock съобщения.
- Интеграцията показва, че системата може да комбинира ERP/PPP функционалност с образователна част и админ панел.

## 7. Обобщение
Архитектурата следва module-per-domain принцип, като:
1. Blueprints отделят REST/HTML функционалността;
2. Services/Helpers обработват бизнес логиката;
3. Моделите съдържат данни и връзки (cascade, eager load);
4. Templates и статични ресурси формират потребителското изживяване.

Кодът е готов за постепенен пренос към Django 4.2 — Blueprint → App, Services → човешки мениджъри, Templates → Django templates, utils → helper modules.
