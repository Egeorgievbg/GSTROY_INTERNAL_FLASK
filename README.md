# GSTROY Internal ERP Demo

Това е моето лично Flask демо, в което съм сложил всички логики, които искам да покажа на девовете как да ги прехвърлят в Django. Нямам достъп до техния проект, затова си направих свои примери с PickPoint потоци, ERP payload-и, принтер хъб и всичко между тях - така мога да обясня кое къде влиза, без да им ровя в репото.

> Говорим като приятели, а не като спецификация, така че очаквай жаргон, усмивки и намигания. :-)

## Какво ще видиш тук

- **Flask + blueprints**: `app/blueprints/` държи модулите `admin`, `catalog`, `logistics`, `orders`, `products`, `scanning` и `main`. Всеки е с отделни view функции, шаблони и helpers, така че лесно може да се превърне в Django app/urls.
- **Service слой**: `app/services/order_tasks.py` съдържа логики за scan tasks, ERP payload-и, генерация на PDF/CSV, планиране на stock orders и комуникация с PickPoint. Може да се раздели в Django services или Celery jobs.
- **Модели и БД**: `database.py` и `models.py` са написани с SQLAlchemy. Има seed данни, генератори за кодове (`LST/PLT/TRF`) и helpers за FK, така че може да ги преведете директно в Django models + data migrations.
- **Printer service**: `printer_service/` е отделен blueprint, който слуша `/printer-hub`. Тук симулирам label server и права за изпращане на PDF/label към физически принтери (`docs/printer-module.md` описва всички детайли). Django екипът може да пренапише това като самостоятелен app.
- **UI + static assets**: `templates/` и `static/` са напълно Bootstrap 5 и responsive. Има готови fragment-и за `stock_orders_*`, `scanner`, `ppp_documents`, `pallets` и QR helpers.
- **Docs и notes**: `docs/architecture.md`, `docs/blueprints.md` и `docs/printer-module.md` описват съставните части, отделните маршрути и какво да очаквате от принтер модула.

## Как да го стартираш

```powershell
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
python -m flask run --reload
```

Или използвай helper скриптовете:

```powershell
.\setup.ps1           # създава .venv и инсталира зависимостите
.\setup.ps1 -Run      # run dev сървър
```

```bash
./setup.sh            # Linux/macOS/Git Bash
./setup.sh run        # run dev сървър
```

След логин можеш да разгледаш `/`, `/admin`, `/stock-orders`, `/scan-tasks`, `/printer-hub`, `/products` и `/catalog`.

## Конфигурация (env или config)

| Променлива | Стойност по подразбиране | За какво служи |
|------------|---------------------------|----------------|
| `ERP_DEMO_SECRET_KEY` | `change-me` | Сесиите, CSRF и всичко, което се крие зад login-а. Смени я преди да покажеш демото.
| `ERP_DEMO_DATABASE_URL` | `sqlite:///erp_demo.db` | Свързване към SQLite, но става и Postgres/MySQL.
| `ERP_DEMO_DEFAULT_PASSWORD` | `demo1234` | Seed паролата за `planner`, `builder`, `admin` и другите потребители.
| `ERP_DEMO_MAX_UPLOAD_MB` | `4` | Максимален размер на CSV файлове при `catalog_sync`.
| `ERP_DEMO_SIGNATURE_MAX_KB` | `512` | Лимит за png подписите в PPP.
| `PRINTER_SERVER_URL` | `http://localhost:5000/printer-hub/print` | Къде се пращат заявките за принтиране.

## Работа с проекта

1. `app/blueprints/` съдържа UX логиката, разбита по домейни: `/admin`, `/catalog`, `/orders`, `/scanning`, `/products`, `/stock-orders` (prepare, handover, dashboard). Всеки blueprint има свои шаблони и helpers.
2. `app/services/order_tasks.py` показва как се подготвят ERP payload-и, как се работи със сканирани задачи и как се генерират PPP PDF-ове.
3. `catalog_sync.py` + `catalog_utils.py` нормализират CSV данни и регистрират продуктите - идеален пример за background job.
4. `printer_service/__init__.py` валидира подписите, рендира PDF и задейства принтера.
5. Templates + static файловете са написани с Jinja и Bootstrap, готови да се пресъздадат в Django templates/staticfiles.

## Хубави практики преди прехвърлянето към Django

- Seed логиката от `database.py` може да се движи в Django fixtures/migrations.
- `app/__init__.py` показва как се регистрират blueprints и services (CSRF, login, printer hub). В Django това е job за `apps.py` + `ready()`.
- `constants.py` и `gstroy_constants.py` са домашната база от статуси, типове, шрифтове и CSV дефиниции. Може да се превърнат в Django enums или settings.
- `docs/` папката служи като справочен материал за Django екипа - гайд за всяка част от flow-а.

## Следващи крачки (ако искаш още)

1. Добави `.gitignore` с `__pycache__/`, `.pyc`, `.db`, `.env` и други временни файлове.
2. Ако искаш, пиша кратък Django README, който описва същите домейни и как да ги реализирате тук.
3. Може да напишеш unit тестове (Pytest/Flask) за helper-ите и services.
4. Готов съм да ти драсна секция за миграции и миграционни бележки в `docs/`.

Ако искаш, мога да пратя и кратък текст за презентация или да сложа снимки/GIF-ове - кажи просто "+1" и го добавям. :-)
