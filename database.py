import os
from datetime import datetime

from sqlalchemy import create_engine, or_
from sqlalchemy.orm import scoped_session, sessionmaker
from werkzeug.security import generate_password_hash

from models import (
    AcademyCategory,
    AcademyContentType,
    Base,
    Brand,
    Category,
    ContentItem,
    Product,
    ProductList,
    ServicePoint,
    StockOrder,
    StockOrderAssignment,
    StockOrderItem,
    TransferDocument,
    User,
    UserContentProgress,
    Warehouse,
)


DATABASE_URL = os.environ.get("ERP_DEMO_DATABASE_URL", "sqlite:///erp_demo.db")
DEFAULT_USER_PASSWORD = os.environ.get("ERP_DEMO_DEFAULT_PASSWORD", "demo1234")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = scoped_session(sessionmaker(bind=engine))


def _default_password_hash():
    return generate_password_hash(DEFAULT_USER_PASSWORD)


def ensure_column(table: str, column: str, ddl: str):
    """Add a column to the SQLite table if it is missing."""
    with engine.connect() as connection:
        result = connection.exec_driver_sql(f'PRAGMA table_info("{table}")')
        columns = {row[1] for row in result.fetchall()}
        if column not in columns:
            connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN {column} {ddl}')


def upsert_product(session, data: dict):
    product = session.query(Product).filter_by(item_number=data["item_number"]).first()
    if product:
        for key, value in data.items():
            setattr(product, key, value)
    else:
        session.add(Product(**data))


def init_db():
    """Create tables, ensure schema columns, and seed demo records."""
    Base.metadata.create_all(bind=engine)
    ensure_column("products", "storage_location", "VARCHAR(128)")
    ensure_column("products", "sell_with_barcode", "BOOLEAN DEFAULT 1")
    ensure_column("products", "inventory_with_barcode", "BOOLEAN DEFAULT 1")
    ensure_column("products", "primary_group", "VARCHAR(128)")
    ensure_column("products", "secondary_group", "VARCHAR(128)")
    ensure_column("products", "tertiary_group", "VARCHAR(128)")
    ensure_column("products", "quaternary_group", "VARCHAR(128)")
    ensure_column("products", "secondary_unit", "VARCHAR(32)")
    ensure_column("products", "unit_conversion_ratio", "FLOAT")
    ensure_column("products", "weight_unit_1", "FLOAT")
    ensure_column("products", "image_url", "VARCHAR(255)")
    ensure_column("products", "brand_id", "INTEGER")
    ensure_column("products", "category_id", "INTEGER")
    ensure_column("products", "is_special_offer", "BOOLEAN DEFAULT 0")
    ensure_column("products", "show_in_special_carousel", "BOOLEAN DEFAULT 0")
    ensure_column("products", "landing_page_accent", "BOOLEAN DEFAULT 0")
    ensure_column("products", "fb_category", "VARCHAR(128)")
    ensure_column("products", "google_category", "VARCHAR(128)")
    ensure_column("products", "fb_ads_tag", "VARCHAR(128)")
    ensure_column("products", "versus_id", "VARCHAR(128)")
    ensure_column("products", "catalog_number", "VARCHAR(128)")
    ensure_column("products", "price_unit_1", "FLOAT")
    ensure_column("products", "price_unit_2", "FLOAT")
    ensure_column("products", "promo_price_unit_1", "FLOAT")
    ensure_column("products", "promo_price_unit_2", "FLOAT")
    ensure_column("products", "visible_price_unit_1", "FLOAT")
    ensure_column("products", "visible_price_unit_2", "FLOAT")
    ensure_column("products", "show_add_to_cart_button", "BOOLEAN DEFAULT 1")
    ensure_column("products", "show_request_button", "BOOLEAN DEFAULT 0")
    ensure_column("products", "allow_two_unit_sales", "BOOLEAN DEFAULT 0")
    ensure_column("products", "in_brochure", "BOOLEAN DEFAULT 0")
    ensure_column("products", "is_most_viewed", "BOOLEAN DEFAULT 0")
    ensure_column("products", "is_active", "BOOLEAN DEFAULT 1")
    ensure_column("products", "is_oversized", "BOOLEAN DEFAULT 0")
    ensure_column("products", "check_availability_in_versus", "BOOLEAN DEFAULT 0")
    ensure_column("products", "variation_parent_sku", "VARCHAR(64)")
    ensure_column("products", "variation_color_code", "VARCHAR(64)")
    ensure_column("products", "variation_color_name", "VARCHAR(128)")
    ensure_column("products", "option2_name", "VARCHAR(128)")
    ensure_column("products", "option2_value", "VARCHAR(128)")
    ensure_column("products", "option2_keyword", "VARCHAR(128)")
    ensure_column("products", "short_description", "TEXT")
    ensure_column("products", "long_description", "TEXT")
    ensure_column("products", "meta_title", "VARCHAR(255)")
    ensure_column("products", "meta_description", "VARCHAR(512)")
    ensure_column("product_lists", "storage_location", "VARCHAR(128)")
    ensure_column("product_lists", "is_light", "BOOLEAN DEFAULT 0")
    ensure_column("product_lists", "created_by_id", "INTEGER")
    ensure_column("warehouses", "printer_server_url", "VARCHAR(255)")
    ensure_column("products", "service_point_id", "INTEGER")
    ensure_column("printers", "server_url", "VARCHAR(255)")
    ensure_column("users", "password_hash", "VARCHAR(255) DEFAULT ''")
    ensure_column("users", "is_admin", "BOOLEAN DEFAULT 0")
    ensure_column("users", "can_view_competitor_prices", "BOOLEAN DEFAULT 0")
    ensure_column("users", "default_warehouse_id", "INTEGER")
    ensure_column("transfer_documents", "code", "VARCHAR(64)")
    ensure_column("scan_tasks", "warehouse_id", "INTEGER")
    ensure_column("scan_tasks", "stock_order_id", "INTEGER")
    ensure_column("scan_tasks", "service_point_id", "INTEGER")
    ensure_column("scan_tasks", "created_by_id", "INTEGER")
    ensure_column("ppp_documents", "signature_image", "VARCHAR(255)")
    ensure_column("stock_orders", "last_handover_at", "DATETIME")
    ensure_column("stock_orders", "last_handover_by_id", "INTEGER")
    ensure_column("stock_orders", "delivered_at", "DATETIME")
    ensure_column("stock_orders", "delivered_by_id", "INTEGER")

    session = SessionLocal()

    academy_category_defaults = ["Security", "HR", "Features", "Operations", "Logistics"]
    if session.query(AcademyCategory).count() == 0:
        session.add_all(AcademyCategory(name=name) for name in academy_category_defaults)

    academy_content_type_defaults = ["NEWS", "GUIDE", "STORY"]
    if session.query(AcademyContentType).count() == 0:
        session.add_all(AcademyContentType(name=name) for name in academy_content_type_defaults)

    sample_products = [
        {
            "item_number": "MAT-0001",
            "name": "Cement BuildMaster 32.5R 25kg",
            "manufacturer_name": "BuildMaster",
            "brand": "BuildMaster",
            "category": "Сухи смеси",
            "group": "Цимент",
            "subgroup": "Портланд",
            "main_unit": "торба",
            "weight_kg": 25.0,
            "barcode": "3800000000010",
            "storage_location": "A1-01",
        },
        {
            "item_number": "MAT-0002",
            "name": "ThermoBlock 25x25x39",
            "manufacturer_name": "ThermoBrick",
            "brand": "ThermoBrick",
            "category": "Зидария",
            "group": "Блокчета",
            "subgroup": "Керамични",
            "main_unit": "бр.",
            "weight_kg": 5.2,
            "width_cm": 25,
            "height_cm": 25,
            "depth_cm": 39,
            "barcode": "3800000000027",
            "storage_location": "A1-05",
        },
        {
            "item_number": "MAT-0003",
            "name": "Rebar Ø12 B500",
            "manufacturer_name": "SteelWorks",
            "brand": "SteelWorks",
            "category": "Метали",
            "group": "Арматура",
            "subgroup": "Прут",
            "main_unit": "м",
            "weight_kg": 0.89,
            "storage_location": "MT-02",
        },
        {
            "item_number": "MAT-0004",
            "name": "Gypsum Board GKB 12.5mm",
            "manufacturer_name": "PlasterPro",
            "brand": "PlasterPro",
            "category": "Сухо строителство",
            "group": "Гипсокартон",
            "subgroup": "GKB",
            "main_unit": "бр.",
            "weight_kg": 7.8,
            "width_cm": 120,
            "height_cm": 200,
            "depth_cm": 1.25,
            "barcode": "3800000000041",
            "storage_location": "B2-10",
        },
        {
            "item_number": "MAT-0005",
            "name": "PolyWrap Stretch Film 23µ",
            "manufacturer_name": "PolyWrap",
            "brand": "PolyWrap",
            "category": "Опаковки",
            "group": "Фолио",
            "subgroup": "Стреч",
            "main_unit": "ролка",
            "weight_kg": 2.4,
            "barcode": "3800000000058",
            "storage_location": "PK-01",
        },
        {
            "item_number": "MAT-0006",
            "name": "Facade Paint AquaShield 15L",
            "manufacturer_name": "ColorMax",
            "brand": "AquaShield",
            "category": "Бои",
            "group": "Фасадни",
            "subgroup": "Силиконова",
            "main_unit": "кофа",
            "weight_kg": 20.0,
            "barcode": "3800000000065",
            "storage_location": "PL-05",
        },
        {
            "item_number": "32300030031",
            "name": "КЛИМАТИК HISENSE EXPERT SMART 12000 BTU",
            "brand": "БГ Терм",
            "category": "КАМИНИ,ОТОПЛЕНИЕ И ОХЛАЖДАНЕ",
            "main_unit": "бр.",
            "barcode": "6926597709970, 6926597709987",
        },
        {
            "item_number": "32300040065",
            "name": "ЕЛ. КОНВЕКТОР RIDER 2Kw LED RD-PH03 ЧЕРНО СТЪКЛО",
            "brand": "Евромастер",
            "category": "КАМИНИ,ОТОПЛЕНИЕ И ОХЛАЖДАНЕ",
            "main_unit": "бр.",
            "barcode": "3800972027992",
        },
        {
            "item_number": "32300190156",
            "name": "ЛИРА ЗА БАНЯ 400/770 584W БЯЛА",
            "brand": "Термолаб",
            "category": "КАМИНИ,ОТОПЛЕНИЕ И ОХЛАЖДАНЕ",
            "main_unit": "бр.",
            "barcode": "3800236936732",
        },
        {
            "item_number": "32300190178",
            "name": "ЛИРА ЗА БАНЯ DP 385/720 350W AL ЧЕРЕН МАТ",
            "brand": "Термолаб",
            "category": "КАМИНИ,ОТОПЛЕНИЕ И ОХЛАЖДАНЕ",
            "main_unit": "бр.",
            "barcode": "3800236938897",
        },
        {
            "item_number": "32300020034",
            "name": "ЕЛ. КАМИНА TORONTO CLASSIC FIRE 2000W СТЕННА 81.2x22x41см",
            "brand": "EDCO",
            "category": "КАМИНИ,ОТОПЛЕНИЕ И ОХЛАЖДАНЕ",
            "main_unit": "бр.",
            "barcode": "8711252249438",
        },
        {
            "item_number": "32300020032",
            "name": "ЕЛ.КАМИНА GENEVA LED ALPINA 1800W MDF 70х22х71см",
            "brand": "Edco",
            "category": "КАМИНИ,ОТОПЛЕНИЕ И ОХЛАЖДАНЕ",
            "main_unit": "бр.",
            "barcode": "8711252178363",
        },
        {
            "item_number": "31500170258",
            "name": "ЛИНЕЕН ПОДОВ СИФОН SLIM ПЛАСТ. 500мм ф50",
            "brand": "Термолаб",
            "category": "ВиК",
            "main_unit": "бр.",
            "barcode": "3800236935827",
            "sell_with_barcode": False,
            "inventory_with_barcode": False,
        },
        {
            "item_number": "31500170324",
            "name": "ЛИНЕЕН ПОДОВ СИФОН VENISIO SODA 900мм",
            "brand": "АКВАСТО",
            "category": "ВиК",
            "main_unit": "бр.",
            "barcode": "3375537237883",
        },
        {
            "item_number": "31300030146",
            "name": "ВЕНТИЛАТОР ЗА БАНЯ PAX NORTE ЧЕРЕН",
            "brand": "АРИНОР",
            "category": "ВЕНТИЛАЦИОННИ ПРОДУКТИ",
            "main_unit": "бр.",
            "barcode": "7391477156110",
        },
        {
            "item_number": "31300030226",
            "name": "ВЕНТИЛАТОР BOSCH FAN 1500 W DH 100 СЕНЗОР ЗА ВЛАЖНОСТ И ТАЙМЕР БЯЛ",
            "brand": "Бош",
            "category": "ВЕНТИЛАЦИОННИ ПРОДУКТИ",
            "main_unit": "бр.",
            "barcode": "4062321172329",
        },
        {
            "item_number": "10100070057",
            "name": "Шина 80х6 6м",
            "brand": "MetalPro",
            "category": "Метали",
            "main_unit": "kg",
            "weight_kg": 6.0,
            "storage_location": "DOB-STEEL",
            "barcode": "3801010007007",
        },
        {
            "item_number": "20700031008",
            "name": "34318 AT Термо плот Бор Ларами 2600x600x28",
            "brand": "AT",
            "category": "Мебелни плотове",
            "main_unit": "бр.",
            "storage_location": "VAR-MAG",
            "barcode": "3802070003108",
        },
        {
            "item_number": "20700030505",
            "name": "K5413 AW Термо плот Дъб Коняк 4100x635x38",
            "brand": "AT",
            "category": "Мебелни плотове",
            "main_unit": "lm",
            "storage_location": "VAR-MAG",
            "barcode": "3802070003050",
        },
        {
            "item_number": "10700270007",
            "name": "Мазилка Кнауф MR75L 30кг",
            "brand": "Knauf",
            "category": "Сухи смеси",
            "main_unit": "бр.",
            "weight_kg": 30.0,
            "barcode": "3801070027002",
            "storage_location": "VAR-A1",
        },
        {
            "item_number": "21301250005",
            "name": "Водач скрит с плавно затваряне 450мм",
            "brand": "HardwarePro",
            "category": "Мебелен обков",
            "main_unit": "бр.",
            "barcode": "3802130125005",
            "storage_location": "VAR-SHOP",
        },
        {
            "item_number": "21301020002",
            "name": "Тръбодържач О-образен с винт",
            "brand": "HardwarePro",
            "category": "Мебелен обков",
            "main_unit": "бр.",
            "barcode": "3802130102002",
            "storage_location": "VAR-SHOP",
        },
        {
            "item_number": "31400050001",
            "name": "Тапа за основа CORDA 61.5mm",
            "brand": "Corda",
            "category": "Декор",
            "main_unit": "бр.",
            "barcode": "3803140005001",
            "storage_location": "VAR-SHOP",
        },
        {
            "item_number": "21300960100",
            "name": "Водач телескопичен 250мм H-35",
            "brand": "HardwarePro",
            "category": "Мебелен обков",
            "main_unit": "бр.",
            "barcode": "3802130096012",
            "storage_location": "VAR-SHOP",
        },
        {
            "item_number": "21301020001",
            "name": "Тръбодържач кръгла скоба",
            "brand": "HardwarePro",
            "category": "Мебелен обков",
            "main_unit": "бр.",
            "barcode": "3802130102001",
            "storage_location": "VAR-SHOP",
        },
        {
            "item_number": "21300660004",
            "name": "Ъглова сглобка бук",
            "brand": "HardwarePro",
            "category": "Мебелен обков",
            "main_unit": "бр.",
            "barcode": "3802130066004",
            "storage_location": "VAR-SHOP",
        },
        {
            "item_number": "21300630005",
            "name": "Чехълче - дъб",
            "brand": "HardwarePro",
            "category": "Мебелен обков",
            "main_unit": "бр.",
            "barcode": "3802130063005",
            "storage_location": "VAR-SHOP",
        },
        {
            "item_number": "21300630002",
            "name": "Чехълче - крем",
            "brand": "HardwarePro",
            "category": "Мебелен обков",
            "main_unit": "бр.",
            "barcode": "3802130063002",
            "storage_location": "VAR-SHOP",
        },
        {
            "item_number": "31900970047",
            "name": "Скара RUBINO с капак 1600W",
            "brand": "Rubino",
            "category": "Електроуреди",
            "main_unit": "бр.",
            "barcode": "3803190097047",
            "storage_location": "SHU-EL",
        },
        {
            "item_number": "30100630004",
            "name": "Държач за хавлия Скара 50см",
            "brand": "Momo",
            "category": "Баня",
            "main_unit": "бр.",
            "barcode": "3803010063004",
            "storage_location": "SHU-BATH",
        },
        {
            "item_number": "31200030110",
            "name": "Гранитогрес Моринга Бежова 15.5x60.5",
            "brand": "Moringa",
            "category": "Настилки",
            "main_unit": "m2",
            "barcode": "3803120003011",
            "storage_location": "SOF-TILES",
        },
    ]

    for product_data in sample_products:
        product_data.setdefault("image_url", "images/no_image.png")
        upsert_product(session, product_data)

    if session.query(Warehouse).count() == 0:
        warehouses = [
            ("Варна", "VAR"),
            ("Добрич", "DOB"),
            ("Каварна", "KAV"),
            ("Лог Склад", "LOG"),
            ("София", "SOF"),
            ("Шумен", "SHU"),
            ("WEB", "WEB"),
        ]
        for name, code in warehouses:
            session.add(Warehouse(name=name, code=code))

    service_points_seed = [
        ("SP1", "1 - Строителен склад"),
        ("SP2", "2 - Мебелен склад"),
        ("SP3", "3 - Магазин"),
        ("SP4", "4 - Склад Ламиниран Паркет"),
    ]
    existing_sp = {sp.code: sp for sp in session.query(ServicePoint).all()}
    for code, name in service_points_seed:
        if code not in existing_sp:
            sp = ServicePoint(code=code, name=name)
            session.add(sp)
            existing_sp[code] = sp
    session.flush()

    assignment_map = {
        "SP1": [
            "MAT-0001",
            "MAT-0002",
            "MAT-0003",
            "MAT-0004",
            "10100070057",
            "10700270007",
            "31200030110",
        ],
        "SP2": [
            "32300040065",
            "32300190156",
            "32300190178",
            "32300020034",
            "32300020032",
            "20700031008",
            "20700030505",
        ],
        "SP3": [
            "31500170258",
            "31500170324",
            "21301250005",
            "21301020002",
            "31400050001",
            "21300960100",
            "21301020001",
            "21300660004",
            "21300630005",
            "21300630002",
            "31900970047",
            "30100630004",
        ],
        "SP4": ["31300030146", "31300030226", "MAT-0005", "MAT-0006"],
    }
    for code, products_codes in assignment_map.items():
        service_point = existing_sp.get(code)
        if not service_point:
            continue
        for item_number in products_codes:
            product = session.query(Product).filter_by(item_number=item_number).first()
            if product:
                product.service_point_id = service_point.id

    if session.query(User).count() == 0:
        planner = User(
            username="planner",
            full_name="Разпределител",
            can_assign_orders=True,
            can_prepare_orders=False,
        )
        planner.service_points = list(existing_sp.values())
        builder = User(
            username="builder",
            full_name="Склад Строителен",
            can_assign_orders=False,
            can_prepare_orders=True,
        )
        builder.service_points = [existing_sp["SP1"]]
        furniture = User(
            username="furniture",
            full_name="Склад Мебелен",
            can_assign_orders=False,
            can_prepare_orders=True,
        )
        furniture.service_points = [existing_sp["SP2"]]
        laminate = User(
            username="laminate",
            full_name="Склад Ламинат",
            can_assign_orders=False,
            can_prepare_orders=True,
        )
        laminate.service_points = [existing_sp["SP4"]]
        shop = User(
            username="shop",
            full_name="Склад Магазин",
            can_assign_orders=False,
            can_prepare_orders=True,
        )
        shop.service_points = [existing_sp["SP3"]]
        session.add_all([planner, builder, furniture, laminate, shop])
        for seeded_user in (planner, builder, furniture, laminate, shop):
            if not seeded_user.password_hash:
                seeded_user.password_hash = _default_password_hash()
        session.flush()
    else:
        shop_assigned = session.query(User).filter_by(username="shop").first()
        if not shop_assigned:
            shop_assigned = User(
                username="shop",
                full_name="Склад Магазин",
                can_assign_orders=False,
                can_prepare_orders=True,
            )
            shop_assigned.service_points = [existing_sp["SP3"]]
            if not shop_assigned.password_hash:
                shop_assigned.password_hash = _default_password_hash()
            session.add(shop_assigned)
            session.flush()

    admin_user = session.query(User).filter_by(username="admin").first()
    if not admin_user:
        admin_user = User(
            username="admin",
            full_name="Администратор",
            can_assign_orders=True,
            can_prepare_orders=True,
            is_admin=True,
            password_hash=_default_password_hash(),
        )
        admin_user.service_points = list(existing_sp.values())
        session.add(admin_user)
        session.flush()

    if session.query(StockOrder).count() == 0:
        warehouse_varna = session.query(Warehouse).filter_by(code="VAR").first()
        warehouse_dobrich = session.query(Warehouse).filter_by(code="DOB").first()
        orders_seed = [
            {
                "external_id": "SO-1001",
                "warehouse": warehouse_varna,
                "type": "A",
                "client_name": "Инвест Строй ООД",
                "client_address": "Варна, бул. Владислав 20",
                "client_phone": "+359888123456",
                "delivery_date": datetime.utcnow().date(),
                "delivery_time": datetime.utcnow().time().replace(hour=10, minute=30, second=0, microsecond=0),
                "recipient_name": "Иван Иванов",
                "recipient_phone": "+359888000111",
                "delivery_address": "Строителен обект 5",
                "note": "Приоритетна доставка",
                "items": [
                    ("MAT-0001", 10),
                    ("MAT-0002", 50),
                    ("MAT-0003", 120),
                ],
            },
            {
                "external_id": "SO-1002",
                "warehouse": warehouse_dobrich or warehouse_varna,
                "type": "B",
                "client_name": "Design Home ЕООД",
                "client_address": "Добрич, ул. Свобода 11",
                "client_phone": "+359889123456",
                "delivery_date": datetime.utcnow().date(),
                "delivery_time": datetime.utcnow().time().replace(hour=14, minute=0, second=0, microsecond=0),
                "recipient_name": "Мария Петрова",
                "recipient_phone": "+359887654321",
                "delivery_address": "Мебелен салон Добрич",
                "note": "Изисква внимателно товарене",
                "items": [
                    ("32300040065", 5),
                    ("32300190156", 8),
                    ("31500170258", 15),
                ],
            },
            {
                "external_id": "SO-1003",
                "warehouse": warehouse_varna,
                "type": "C",
                "client_name": "Retail Park",
                "client_address": "Варна, ул. Примерна 10",
                "client_phone": "+359886222333",
                "delivery_date": datetime.utcnow().date(),
                "delivery_time": datetime.utcnow().time().replace(hour=16, minute=0, second=0, microsecond=0),
                "recipient_name": "Георги Георгиев",
                "recipient_phone": "+359888333444",
                "delivery_address": "Магазин 3",
                "note": "Частична доставка възможна",
                "items": [
                    ("MAT-0005", 20),
                    ("MAT-0006", 12),
                    ("31300030146", 6),
                ],
            },
            {
                "external_id": "SO-1004",
                "warehouse": warehouse_varna,
                "type": "A",
                "client_name": "Дом Мебел",
                "client_address": "Варна, бул. Цар Освободител 99",
                "client_phone": "+359882223344",
                "delivery_date": datetime.utcnow().date(),
                "delivery_time": datetime.utcnow().time().replace(hour=11, minute=45, second=0, microsecond=0),
                "recipient_name": "Петър Петров",
                "recipient_phone": "+359888777666",
                "delivery_address": "Склад мебелен",
                "note": "Нужно е двойно опаковане",
                "items": [
                    ("32300190156", 10),
                    ("32300190178", 4),
                    ("31500170324", 20),
                ],
            },
            {
                "external_id": "SO-1005",
                "warehouse": warehouse_dobrich or warehouse_varna,
                "type": "B",
                "client_name": "Градински Център",
                "client_address": "Добрич, пром. зона",
                "client_phone": "+359885111222",
                "delivery_date": datetime.utcnow().date(),
                "delivery_time": datetime.utcnow().time().replace(hour=9, minute=15, second=0, microsecond=0),
                "recipient_name": "Стефан Стефанов",
                "recipient_phone": "+359884999888",
                "delivery_address": "Обект 3",
                "note": "Частична доставка допустима",
                "items": [
                    ("MAT-0001", 30),
                    ("MAT-0004", 12),
                    ("31300030146", 8),
                ],
            },
        ]

        orders_seed.extend(
            [
                {
                    "external_id": "1101245314",
                    "warehouse": warehouse_dobrich,
                    "type": "A",
                    "client_name": "ФОЛИАРТ ООД",
                    "client_address": "Добрич, Индустриална зона",
                    "client_phone": "+359889000111",
                    "delivery_date": datetime(2025, 11, 18).date(),
                    "delivery_time": datetime(2025, 11, 18, 9, 0).time(),
                    "recipient_name": "Отговорник склад",
                    "recipient_phone": "+359889000111",
                    "delivery_address": "Добрич, ул. Промишлена 5",
                    "note": "х 2м.",
                    "items": [
                        ("10100070057", 45.24),
                    ],
                },
                {
                    "external_id": "2200923771",
                    "warehouse": warehouse_varna,
                    "type": "B",
                    "client_name": "СТОЯН ТОДОРОВ СТОЯНОВ",
                    "client_address": "Варна, кв. Виница",
                    "client_phone": "+359888777111",
                    "delivery_date": datetime(2025, 11, 18).date(),
                    "delivery_time": datetime(2025, 11, 18, 11, 30).time(),
                    "recipient_name": "Стоян Тодоров",
                    "recipient_phone": "+359888777111",
                    "delivery_address": "Варна, офис 84362",
                    "note": "84362 - на място",
                    "items": [
                        ("20700031008", 1),
                        ("20700030505", 4.1),
                    ],
                },
                {
                    "external_id": "2200923775",
                    "warehouse": warehouse_varna,
                    "type": "A",
                    "client_name": "БИЛД 2004 ООД",
                    "client_address": "Варна, Западна промишлена зона",
                    "client_phone": "+359888444555",
                    "delivery_date": datetime(2025, 11, 18).date(),
                    "delivery_time": datetime(2025, 11, 18, 14, 0).time(),
                    "recipient_name": "Логистика Билд",
                    "recipient_phone": "+359888444555",
                    "delivery_address": "Строителен обект - Варна",
                    "note": "Цял палет",
                    "items": [
                        ("10700270007", 490),
                    ],
                },
                {
                    "external_id": "2200923777",
                    "warehouse": warehouse_varna,
                    "type": "B",
                    "client_name": "КАЛОЯН ПЕШЕВ",
                    "client_address": "Варна, ул. Примерна 10",
                    "client_phone": "+359887654123",
                    "delivery_date": datetime(2025, 11, 18).date(),
                    "delivery_time": datetime(2025, 11, 18, 16, 0).time(),
                    "recipient_name": "Калоян Пешев",
                    "recipient_phone": "+359887654123",
                    "delivery_address": "ЕКОНТ 85104",
                    "note": "85104 - Еконт",
                    "items": [
                        ("21301250005", 1),
                        ("21301020002", 4),
                        ("31400050001", 4),
                        ("21300960100", 4),
                        ("21301020001", 6),
                        ("21300660004", 20),
                        ("21300630005", 50),
                        ("21300630002", 50),
                    ],
                },
                {
                    "external_id": "4401011773",
                    "warehouse": session.query(Warehouse).filter_by(code="SHU").first(),
                    "type": "C",
                    "client_name": "НИКОЛАЙ ГЕОРГИЕВ",
                    "client_address": "София, Лозенец",
                    "client_phone": "+359882223355",
                    "delivery_date": datetime(2025, 11, 18).date(),
                    "delivery_time": datetime(2025, 11, 18, 10, 15).time(),
                    "recipient_name": "Николай Георгиев",
                    "recipient_phone": "+359882223355",
                    "delivery_address": "ЕКОНТ 85184",
                    "note": "85184 Еконт",
                    "items": [
                        ("31900970047", 1),
                    ],
                },
                {
                    "external_id": "4401011785",
                    "warehouse": session.query(Warehouse).filter_by(code="SHU").first(),
                    "type": "C",
                    "client_name": "ЕЛЕОНОРА ДАСКАЛОВА",
                    "client_address": "София, център",
                    "client_phone": "+359883334455",
                    "delivery_date": datetime(2025, 11, 18).date(),
                    "delivery_time": datetime(2025, 11, 18, 13, 45).time(),
                    "recipient_name": "Елеонора Даскалова",
                    "recipient_phone": "+359883334455",
                    "delivery_address": "СПИДИ 85140",
                    "note": "85140 Спиди",
                    "items": [
                        ("30100630004", 1),
                    ],
                },
                {
                    "external_id": "5500713922",
                    "warehouse": session.query(Warehouse).filter_by(code="SOF").first(),
                    "type": "A",
                    "client_name": "ГРАЖДАНИ",
                    "client_address": "София, ж.к. Люлин",
                    "client_phone": "+359889773807",
                    "delivery_date": datetime(2025, 11, 18).date(),
                    "delivery_time": datetime(2025, 11, 18, 15, 30).time(),
                    "recipient_name": "Георги Христов",
                    "recipient_phone": "+359889773807",
                    "delivery_address": "София, ул. Пример 12",
                    "note": "Георги Христов 0898773807",
                    "items": [
                        ("31200030110", 10.31525),
                    ],
                },
            ]
        )

        for order_data in orders_seed:
            order = StockOrder(
                external_id=order_data["external_id"],
                warehouse_id=order_data["warehouse"].id if order_data["warehouse"] else None,
                type=order_data["type"],
                client_name=order_data["client_name"],
                client_address=order_data["client_address"],
                client_phone=order_data["client_phone"],
                delivery_date=order_data["delivery_date"],
                delivery_time=order_data["delivery_time"],
                recipient_name=order_data["recipient_name"],
                recipient_phone=order_data["recipient_phone"],
                delivery_address=order_data["delivery_address"],
                note=order_data["note"],
            )
            session.add(order)
            session.flush()
            for item_number, qty in order_data["items"]:
                product = session.query(Product).filter_by(item_number=item_number).first()
                if not product:
                    continue
                item = StockOrderItem(
                    stock_order_id=order.id,
                    product_id=product.id,
                    service_point_id=product.service_point_id,
                    unit=product.main_unit,
                    quantity_ordered=qty,
                )
                order.items.append(item)

        session.flush()

        planner_user = session.query(User).filter_by(username="planner").first()
        builder_user = session.query(User).filter_by(username="builder").first()
        furniture_user = session.query(User).filter_by(username="furniture").first()
        laminate_user = session.query(User).filter_by(username="laminate").first()
        shop_user = session.query(User).filter_by(username="shop").first()
        order_two = session.query(StockOrder).filter_by(external_id="SO-1002").first()
        if order_two and furniture_user:
            for sp_id in {item.service_point_id for item in order_two.items if item.service_point_id}:
                session.add(
                    StockOrderAssignment(
                        stock_order_id=order_two.id,
                        service_point_id=sp_id,
                        user_id=furniture_user.id,
                    )
                )
            order_two.status = "assigned"
        order_three = session.query(StockOrder).filter_by(external_id="SO-1003").first()
        if order_three and laminate_user:
            for item in order_three.items:
                if item.service_point_id == existing_sp["SP4"].id:
                    item.quantity_prepared = item.quantity_ordered
            session.add(
                StockOrderAssignment(
                    stock_order_id=order_three.id,
                    service_point_id=existing_sp["SP4"].id,
                    user_id=laminate_user.id,
                )
            )
            order_three.status = "ready_for_handover"
        if shop_user:
            sp3_id = existing_sp["SP3"].id
            shop_orders = (
                session.query(StockOrder)
                .filter(StockOrder.external_id.in_(["2200923777", "4401011773", "4401011785"]))
                .all()
            )
            for order in shop_orders:
                if not any(item.service_point_id == sp3_id for item in order.items):
                    continue
                session.add(
                    StockOrderAssignment(
                        stock_order_id=order.id,
                        service_point_id=sp3_id,
                        user_id=shop_user.id,
                    )
                )
                if order.status == "new":
                    order.status = "assigned"

    for product_list in session.query(ProductList).filter(ProductList.is_light.is_(None)):
        product_list.is_light = False

    default_warehouse = session.query(Warehouse).order_by(Warehouse.id).first()
    if default_warehouse:
        for user in session.query(User).filter(User.default_warehouse_id.is_(None)):
            user.default_warehouse_id = default_warehouse.id

    for user in session.query(User).filter(or_(User.password_hash.is_(None), User.password_hash == "")):
        user.password_hash = _default_password_hash()

    for transfer in session.query(TransferDocument).filter(TransferDocument.code.is_(None)):
        year = transfer.created_at.year if transfer.created_at else datetime.utcnow().year
        transfer.code = f"TRF-{year}-{transfer.id:06d}"

    session.commit()
    session.close()
