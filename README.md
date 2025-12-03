# GSTROY Internal ERP Demo

Комплектът е почти продукционна среда за проследяване на складови поръчки, инвентарни сканирания и администраторски операции за GSTROY Pallet & PickPoint. Решението е изградено върху Flask, SQLAlchemy, Bootstrap 5 и вътрешни микросервизни Routеs (PPP/ERP, принтер хъб, документни потоци).

## Архитектура в резюме

1. **Flask + blueprints**: `app/blueprints/` съдържа модулите `admin`, `catalog`, `logistics`, `orders`, `products`, `scanning` и `main`. Всеки описва своята UI логика, шаблони и API тунели, плюс обвързва общи services (`app/services/order_tasks.py`).
2. **Database + models**: `database.py` и `models.py` описват SQLAlchemy измеренията (продукти, потребители, поръчки, задачи, принтери...) и bootstrap seed данни (`init_db()`), така че демото винаги стартира с валидна структура.
3. **Printer service**: `printer_service/` е независим blueprint под `/printer-hub`, който проксира заявките към вътрешния label server, филтрира достъпа на потребителя и затяга копията (виж `docs/printer-module.md` за подробности).
4. **Интеграция с Versus ERP**: Payload генератори (`app/services/order_tasks.py`) подготвят JSON за вход/изход, а UI (templates `stock_orders_*`) визуализира статуси, KPI панели и състояния на екипи.
5. **Документация**: Всички детайли за структурата са описани в `docs/` (архитектурата, списък със blueprints и принтер модула).

## Бърз старт

```powershell
.\\setup.ps1              # създава .venv, инсталира зависимостите
.\\setup.ps1 -Run         # стартира dev сървъра
```

```bash
./setup.sh                # Linux/macOS/Git Bash
./setup.sh run            # стартира dev сървъра
```

Алтернатива: `python -m venv .venv && ./.venv/Scripts/activate && pip install -r requirements.txt && python app.py`

## Зависимости

- `Flask==3.0.3`
- `Flask-Login==0.6.3`
- `Flask-WTF==1.2.1`
- `SQLAlchemy==2.0.23`
- `qrcode==7.4.2`
- `Pillow==10.1.0`
- `reportlab==4.0.8`

## Конфигурационни променливи

| Променлива | Стойност по подразбиране | Описание |
|------------|---------------------------|----------|
| `ERP_DEMO_SECRET_KEY` | `change-me` | Секрет за сесии и CSRF. |
| `ERP_DEMO_DATABASE_URL` | `sqlite:///erp_demo.db` | Път към SQLite файл или друг SQLAlchemy URL. |
| `ERP_DEMO_DEFAULT_PASSWORD` | `demo1234` | Парола за seed потребителите (`planner`, `builder`, …). |
| `ERP_DEMO_MAX_UPLOAD_MB` | `4` | Максимален размер на CSV импорти (MB). |
| `ERP_DEMO_SIGNATURE_MAX_KB` | `512` | Максимален размер на PNG подпис (PPP). |

## Структура на проекта

- `app/__init__.py`: фабрика (`create_app`) и регистрация на всички blueprints и прило-жни услуги (`printer_service`, csrf, login).
- `app/blueprints/`: отделни модули по домейн (admin/catalog/orders/etc.). Всеки има собствени шаблони, данни и helper функции. Допълнително `catalog_sync.py`/`catalog_utils.py` предоставят нормализация и registry към каталозите.
- `app/services/order_tasks.py`: споделени logics (статуси на поръчки, scan tasks, ERP payloads).
- `constants.py` / `gstroy_constants.py`: дефиниции на статуси, типове, CSV полета, PDF шрифтове.
- `database.py`: миграции/seed-ове, helper-и за уникални кодове (LST/PLT/TRF) и дефинирани FK зависимости.
- `printer_service/`: API за принтери; препраща към вътрешен label server (`config -> PRINTER_SERVER_URL`).
- `templates/` / `static/`: интерфейсните страници и стилове.

## Документация

- `docs/architecture.md`: високо ниво архитектура и как компонентите работят заедно.
- `docs/blueprints.md`: списък на blueprints, тяхната отговорност и основни маршрути.
- `docs/printer-module.md`: описание на принтер хъба, сигурността и взаимодействието със сървъра.

## Работа с проекта

1. Запазете seed данни: `python -m flask run --reload` в dev среда и проверете `/admin`, `/stock-orders`, `/scan-tasks`.
2. Админ потребители: `planner`, `builder`, `furniture`, `laminate`, `shop` (парола `demo1234`), `admin` (ако е добавен).
3. Принтер хъб: изисква `assigned_warehouse` на потребителя; за всеки принтер се настройва IP/URL (`templates/product_detail.html` и `templates/pallet_detail.html` използват `/printer-hub/print-*`).

## Пускане в продукция

- Използвайте WSGI сървър (Gunicorn/Hypercorn) и настройте `ERP_DEMO_DATABASE_URL` към PostgreSQL/MySQL.
- Осигурете SSL, дефинирайте webhook за ERP (в `app/services/order_tasks.py`) и синхронизирайте `PPP_STATIC_DIR`.
- Инвестирайте в документация на `docs/` и измервайте логовете от `printer_service` (timeout). Зависимостите са минимални и лесни за поддръжка.

---

Когато сте готови за GitHub, добавете `docs/`, `templates/` и `static/` файлове и опишете blueprint структурата в същия README или отделен файл.
