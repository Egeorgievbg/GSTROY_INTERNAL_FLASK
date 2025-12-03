# Архитектура на GSTROY Light

## Обща картина

- `Flask` приложението (`app/__init__.py`) е организирано през blueprint-ове. Всеки blueprint отговаря за отделен бизнес домейн: `admin`, `catalog`, `logistics`, `orders`, `products`, `scanning`, `main`.
- Основните модели живеят в `models.py`, а `database.py` се грижи за миграции, seed-ове (warehouses, stock orders, products) и helper-и за уникални кодове.
- `app/services/order_tasks.py` е сервизният слой: обновява статусите на поръчки, осигурява сканиращите задачи и подготвя ERP payload-ове.
- `printer_service` е отделен blueprint под `/printer-hub`; проксира label server и гарантира, че потребителят вижда само принтери от неговия склад.

## Потоци и интеграции

1. **Поръчки (Stock Orders)**: `orders_bp` зарежда детайли от `STOCK_ORDER_EAGER_OPTIONS`, визуализира dashboard, осигурява prepare/handover/PPP flows и през `order_tasks` синхронизира статусите.
2. **Сканирания**: `scanning_bp` управлява `ScanTask`/`ScanTaskItem` логика, освобождаване на движения (`InventoryMovement`) и manual scan поддръжка.
3. **Каталог**: `catalog_bp` предоставя CRUD интерфейс за продукти, bulk import/export и принтер функционалности; той използва `catalog_sync` и `catalog_utils` за нормализиране на CSV/категории.
4. **Админ панел**: `admin_bp` комбинира продукти, категории, роли, складове, принтери и access windows, за да служи като Dashboard/CRUD console.

## Модулите и шаблоните

- Всички шаблони са в `templates/` и споделят nav, layout, скриптове (Bootstrap 5, `btn-brand-new`, spinner, modal).
- Статик ресурсите (css/js) са в `static/`, включително custom стилове за KPI, modals и cards.
- Документацията за принтер модула е в `docs/printer-module.md`. Следва да бъде подсигурена преди продукционен релийз.
