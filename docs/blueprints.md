# Blueprints и модулна структура

Всеки Blueprint служи за отделен домейн и регистрира URL пространство под префикс. Ето подробно описание на всеки модул:

| Blueprint | URL префикс | Отговорност | Ключови функции/ендпойнти |
|-----------|-------------|-------------|----------------------------|
| `main_bp` | `/` | Главен landing и навигация. | Приветствена страница, маршрути за статични справки и redirect към `/stock-orders`. |
| `auth_bp` | `/auth` | Аутентикация/Flask-Login. | `login`, `logout`, CSRF защитени форми, управление на сесии. |
| `orders_bp` | `/stock-orders` | Цялостен lifecycle на stock orders. | Dashboards (`/`), assigned orders(`/assigned-to-me`), prepare (`/<id>/prepare`), handover (`/<id>/handover`), PPP archive (`/<id>/ppp`, `/completed`), ERP input/output, scan APIs (`/scan`, `/manual`), scan history. |
| `scanning_bp` | `/scan-tasks` | Управление на ScanTask/ScanEvent. | Създаване на scan задачи, детайл, сканиране през камера/ручни количества, листове за инвентаризация, export. |
| `catalog_bp` | `/products` (+ `/catalog-sync`) | CRUD за продукти/категории и CSV синхронизации. | Импортиране, експортиране, API за registry (Brand/Category), helpers за заглавия. |
| `admin_bp` | `/admin` | Админ панел (Academy, PPP stats). | Управление на съдържание, push mock за academy, мониторинг на PPP документи и потребители. Нужно `require_admin`. |
| `printer_bp` | `/printer-hub` | Симулатор за label принтер. | Принтиране на продукт/списък, status check, sanitization на payload. |

### Общи наблюдения

- Всеки Blueprint е регистриран в `app/__init__.py` и достъпва общи помощни функции от `helpers.py` или `order_tasks`.
- `orders_bp` използва `STOCK_ORDER_EAGER_OPTIONS` (joinedload) за dashboard, за да има подготвен контекст.
- `scanning_bp` и `orders_bp` взаимодействат чрез моделите (`ScanTask` + `StockOrder`).
- `admin_bp`/`academy` използват `templates/admin_*.html` и `ContentItem` модел, за да показват аналитици/notification.
- `catalog_bp` извиква `printer_utils` и `catalog_sync` за външни CSV и синхронизации.

### Примерен път през blueprints

1. Потребителът отваря `/stock-orders` (orders_bp) → разглежда поръчка.
2. Придвижва се към `/stock-orders/123/prepare` → създава scan задача (scanning_bp).
3. Завършва подготовката → отваря `/stock-orders/123/handover` → генерира PPP (orders_bp + utils).
4. Проверява документите в `/stock-orders/123/ppp` → видеть историята (orders_bp).
5. Администратор разглежда academy dashboard (admin_bp + academy templates) и може да изпрати push.

Така всеки blueprint изпълнява отделен слой от стойност, а комбинирани те покриват целия процес на ERP потока.
