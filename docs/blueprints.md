# Blueprints и рутове

| Blueprint | Префикс | Какво прави |
|-----------|---------|------------|
| `admin_bp` | `/admin` | Пълно администриране на продукти, категории, роли, потребители, складове и принтери. Използва `helpers.require_admin`. |
| `catalog_bp` | `/` (без префикс) | Визуализация на продуктов каталог, принтери, импорти/експорти, конкурентни цени. Използва `catalog_sync` за автоматични категории и `printer_utils`. |
| `logistics_bp` | `/logistics` | Допълнителни оперативни страници (внос, складиране); ползва специфични шаблони. |
| `orders_bp` | `/stock-orders` | Дашборд, Assigned, Prepare, Handover, PPP, ERP input/output. Поддържа по-малки API (scan/erp payload). |
| `products_bp` | `/products` | Модул за преглед, филтриране и action-и върху продукти (дистанция, KPI). |
| `scanning_bp` | `/scan-tasks` | Управление на scan tasks: create, detail, scan, manual, export и live events. |
| `main_bp` | `/` | Landing страница и overview. |
| `auth_bp` | `/auth` | Flask-Login логин/авторизация, CSRF защитни форми. |
| `printer_bp` | `/printer-hub` | Прокси към вътрешния label server за печат на етикети/листове. |

## Service компоненти

- `app/services/order_tasks.py`: логика за статуса на поръчките, scan tasks, scan events и inventory movement.
- `catalog_sync.py`/`catalog_utils.py`: registry за марки/категории и CSV header mapping.
- `printer_service`: обвива HTTP изход към принтер сървъра, има sanitizers (`_sanitize_text`) и rate control (`_clamp_copies`).

## Template групи

- `admin_*.html`: панели за админ контрол.
- `products*`, `catalog*`, `stock_order*`, `scan_task*`: UI за екрани.
- `base.html` дефинира nav, sidebar, floating action buttons.
