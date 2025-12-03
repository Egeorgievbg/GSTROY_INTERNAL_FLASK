from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    Text,
    Time,
)
from sqlalchemy.orm import declarative_base, relationship
from flask_login import UserMixin


Base = declarative_base()


user_service_points = Table(
    "user_service_points",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("service_point_id", Integer, ForeignKey("service_points.id"), primary_key=True),
)

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
)

access_window_roles = Table(
    "access_window_roles",
    Base.metadata,
    Column("window_id", Integer, ForeignKey("access_windows.id"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
)

access_window_users = Table(
    "access_window_users",
    Base.metadata,
    Column("window_id", Integer, ForeignKey("access_windows.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
)

access_window_warehouses = Table(
    "access_window_warehouses",
    Base.metadata,
    Column("window_id", Integer, ForeignKey("access_windows.id"), primary_key=True),
    Column("warehouse_id", Integer, ForeignKey("warehouses.id"), primary_key=True),
)


class Brand(Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    slug = Column(String(255), unique=True)
    description = Column(Text)
    image_url = Column(String(255))

    products = relationship("Product", back_populates="brand_entity")


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    slug = Column(String(128), unique=True)
    description = Column(Text)
    is_active = Column(Boolean, default=True)

    users = relationship("User", secondary=user_roles, back_populates="roles")
    access_windows = relationship("AccessWindow", secondary=access_window_roles, back_populates="roles")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True)
    description = Column(Text)
    image_url = Column(String(255))
    meta_title = Column(String(255))
    meta_description = Column(String(512))
    canonical_url = Column(String(255))
    level = Column(Integer, default=0)
    address = Column(String(1024))
    parent_id = Column(Integer, ForeignKey("categories.id"))

    parent = relationship("Category", remote_side=[id], back_populates="children")
    children = relationship(
        "Category",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    products = relationship("Product", back_populates="category_entity")

    @property
    def full_address(self):
        parts = []
        node = self
        while node:
            parts.append(node.name)
            node = node.parent
        return " / ".join(reversed(parts)) if parts else ""


class AccessWindow(Base):
    __tablename__ = "access_windows"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    days = Column(String(64), default="")
    is_active = Column(Boolean, default=True)

    roles = relationship("Role", secondary=access_window_roles, back_populates="access_windows")
    users = relationship("User", secondary=access_window_users, back_populates="access_windows")
    warehouses = relationship("Warehouse", secondary=access_window_warehouses, back_populates="access_windows")

    @property
    def days_list(self):
        return [day for day in (self.days or "").split(",") if day]

    @days_list.setter
    def days_list(self, values):
        self.days = ",".join(values)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    item_number = Column(String(64), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    storage_location = Column(String(128))
    manufacturer_name = Column(String(255))
    brand = Column(String(128))
    brand_id = Column(Integer, ForeignKey("brands.id"))
    category = Column(String(128))
    category_id = Column(Integer, ForeignKey("categories.id"))
    group = Column(String(128))
    subgroup = Column(String(128))
    primary_group = Column(String(128))
    secondary_group = Column(String(128))
    tertiary_group = Column(String(128))
    quaternary_group = Column(String(128))
    main_unit = Column(String(32), nullable=False, default="pcs")
    secondary_unit = Column(String(32))
    unit_conversion_ratio = Column(Float)
    weight_kg = Column(Float)
    weight_unit_1 = Column(Float)
    width_cm = Column(Float)
    height_cm = Column(Float)
    depth_cm = Column(Float)
    image_url = Column(String(255))
    barcode = Column(String(128))
    sell_with_barcode = Column(Boolean, default=True)
    inventory_with_barcode = Column(Boolean, default=True)
    is_pallet = Column(Boolean, default=False)
    is_special_offer = Column(Boolean, default=False)
    show_in_special_carousel = Column(Boolean, default=False)
    landing_page_accent = Column(Boolean, default=False)
    fb_category = Column(String(128))
    google_category = Column(String(128))
    fb_ads_tag = Column(String(128))
    versus_id = Column(String(128))
    catalog_number = Column(String(128))
    price_unit_1 = Column(Float)
    price_unit_2 = Column(Float)
    promo_price_unit_1 = Column(Float)
    promo_price_unit_2 = Column(Float)
    visible_price_unit_1 = Column(Float)
    visible_price_unit_2 = Column(Float)
    show_add_to_cart_button = Column(Boolean, default=True)
    show_request_button = Column(Boolean, default=False)
    allow_two_unit_sales = Column(Boolean, default=False)
    in_brochure = Column(Boolean, default=False)
    is_most_viewed = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    is_oversized = Column(Boolean, default=False)
    check_availability_in_versus = Column(Boolean, default=False)
    variation_parent_sku = Column(String(64))
    variation_color_code = Column(String(64))
    variation_color_name = Column(String(128))
    option2_name = Column(String(128))
    option2_value = Column(String(128))
    option2_keyword = Column(String(128))
    short_description = Column(Text)
    long_description = Column(Text)
    meta_title = Column(String(255))
    meta_description = Column(String(512))
    service_point_id = Column(Integer, ForeignKey("service_points.id"))

    service_point = relationship("ServicePoint", back_populates="products")
    brand_entity = relationship("Brand", back_populates="products")
    category_entity = relationship("Category", back_populates="products")


class Warehouse(Base):
    __tablename__ = "warehouses"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    code = Column(String(32), unique=True, nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    printer_server_url = Column(String(255))

    outgoing_lists = relationship(
        "ProductList",
        foreign_keys="ProductList.current_warehouse_id",
        back_populates="current_warehouse",
    )
    incoming_targets = relationship(
        "ProductList",
        foreign_keys="ProductList.target_warehouse_id",
        back_populates="target_warehouse",
    )
    assigned_staff = relationship(
        "User",
        back_populates="assigned_warehouse",
        foreign_keys="User.assigned_warehouse_id",
    )
    access_windows = relationship("AccessWindow", secondary=access_window_warehouses, back_populates="warehouses")
    locations = relationship("Location", back_populates="warehouse", cascade="all, delete-orphan")
    printers = relationship("Printer", back_populates="warehouse", cascade="all, delete-orphan")


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("warehouse_id", "code", name="uq_warehouse_location_code"),)

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("locations.id"))
    name = Column(String(128), nullable=False)
    code = Column(String(32), nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    warehouse = relationship("Warehouse", back_populates="locations")
    parent = relationship("Location", remote_side=[id], back_populates="children")
    children = relationship("Location", back_populates="parent", cascade="all, delete-orphan")


class Printer(Base):
    __tablename__ = "printers"
    __table_args__ = (UniqueConstraint("warehouse_id", "ip_address", name="uq_warehouse_printer_ip"),)

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    name = Column(String(128))
    ip_address = Column(String(64), nullable=False)
    server_url = Column(String(255))
    description = Column(Text)
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False, onupdate=datetime.utcnow)

    warehouse = relationship("Warehouse", back_populates="printers")


class ProductList(Base):
    __tablename__ = "product_lists"

    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    title = Column(String(255), nullable=False, default="Product list")
    storage_location = Column(String(128))
    current_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    target_warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    status = Column(String(32), default="draft", nullable=False)
    is_pallet = Column(Boolean, default=False)
    is_light = Column(Boolean, default=False, nullable=False)
    pallet_code = Column(String(64))
    created_by_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    current_warehouse = relationship(
        "Warehouse", foreign_keys=[current_warehouse_id], back_populates="outgoing_lists"
    )
    target_warehouse = relationship(
        "Warehouse", foreign_keys=[target_warehouse_id], back_populates="incoming_targets"
    )
    items = relationship("ProductListItem", back_populates="product_list", cascade="all, delete-orphan")
    transfers = relationship(
        "TransferDocument",
        back_populates="product_list",
        cascade="all, delete-orphan",
        order_by="desc(TransferDocument.created_at)",
    )
    created_by = relationship("User", back_populates="authored_lists")


class ProductListItem(Base):
    __tablename__ = "product_list_items"

    id = Column(Integer, primary_key=True)
    list_id = Column(Integer, ForeignKey("product_lists.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Float, nullable=False)
    unit = Column(String(32), nullable=False)

    product_list = relationship("ProductList", back_populates="items")
    product = relationship("Product")



class TransferDocument(Base):
    __tablename__ = "transfer_documents"

    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    list_id = Column(Integer, ForeignKey("product_lists.id"), nullable=False)
    from_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    to_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    status = Column(String(32), default="planned", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    shipped_at = Column(DateTime)
    received_at = Column(DateTime)

    product_list = relationship("ProductList", back_populates="transfers")
    from_warehouse = relationship("Warehouse", foreign_keys=[from_warehouse_id])
    to_warehouse = relationship("Warehouse", foreign_keys=[to_warehouse_id])


class ScanTask(Base):
    __tablename__ = "scan_tasks"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    type = Column(String(64), nullable=False, default="inventory")
    status = Column(String(32), nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    stock_order_id = Column(Integer, ForeignKey("stock_orders.id"))
    service_point_id = Column(Integer, ForeignKey("service_points.id"))
    created_by_id = Column(Integer, ForeignKey("users.id"))

    warehouse = relationship("Warehouse")
    stock_order = relationship("StockOrder", back_populates="scan_tasks")
    service_point = relationship("ServicePoint")
    created_by = relationship("User")
    items = relationship("ScanTaskItem", back_populates="task", cascade="all, delete-orphan")
    events = relationship("ScanEvent", back_populates="task", cascade="all, delete-orphan", order_by="desc(ScanEvent.created_at)")
    movements = relationship("InventoryMovement", back_populates="task", cascade="all, delete-orphan")

    @property
    def completed_items(self):
        return sum(1 for item in self.items if item.is_completed)

    @property
    def total_items(self):
        return len(self.items)

    @property
    def all_completed(self):
        return self.total_items > 0 and self.completed_items == self.total_items


class ScanTaskItem(Base):
    __tablename__ = "scan_task_items"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("scan_tasks.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"))
    barcode = Column(String(128), nullable=False)
    expected_qty = Column(Float, nullable=False, default=0.0)
    scanned_qty = Column(Float, nullable=False, default=0.0)
    unit = Column(String(32))

    task = relationship("ScanTask", back_populates="items")
    product = relationship("Product")

    @property
    def remaining_qty(self):
        return max(self.expected_qty - self.scanned_qty, 0)

    @property
    def is_completed(self):
        return self.scanned_qty >= self.expected_qty > 0

    @property
    def is_over_scanned(self):
        return self.scanned_qty > self.expected_qty

    @property
    def requires_manual(self):
        return not self.product or not self.product.inventory_with_barcode


class ScanEvent(Base):
    __tablename__ = "scan_events"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("scan_tasks.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("scan_task_items.id"))
    barcode = Column(String(128))
    qty = Column(Float, default=0.0)
    source = Column(String(32), default="scan")  # scan/manual/system/error
    message = Column(String(255))
    is_error = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("ScanTask", back_populates="events")
    item = relationship("ScanTaskItem")


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("scan_tasks.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    movement_type = Column(String(32))
    quantity = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("ScanTask", back_populates="movements")
    product = relationship("Product")
    warehouse = relationship("Warehouse")


class ServicePoint(Base):
    __tablename__ = "service_points"

    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, nullable=False)
    name = Column(String(255), nullable=False)

    products = relationship("Product", back_populates="service_point")
    users = relationship("User", secondary=user_service_points, back_populates="service_points")
    stock_order_items = relationship("StockOrderItem", back_populates="service_point")


class User(UserMixin, Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    full_name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False, default="")
    can_assign_orders = Column(Boolean, default=False)
    can_prepare_orders = Column(Boolean, default=True)
    can_view_competitor_prices = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    default_warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    email = Column(String(255), unique=True)
    phone = Column(String(32))
    employee_number = Column(String(64), unique=True)
    is_staff = Column(Boolean, default=False)
    manager_id = Column(Integer, ForeignKey("users.id"))
    assigned_warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    registered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime)

    service_points = relationship(
        "ServicePoint",
        secondary=user_service_points,
        back_populates="users",
    )
    assignments = relationship("StockOrderAssignment", back_populates="user")
    scan_tasks = relationship("ScanTask", back_populates="created_by")
    authored_lists = relationship("ProductList", back_populates="created_by")
    default_warehouse = relationship("Warehouse", foreign_keys=[default_warehouse_id])
    assigned_warehouse = relationship("Warehouse", foreign_keys=[assigned_warehouse_id], back_populates="assigned_staff")
    manager = relationship("User", remote_side=[id], back_populates="subordinates")
    subordinates = relationship("User", back_populates="manager")
    roles = relationship("Role", secondary=user_roles, back_populates="users")
    access_windows = relationship("AccessWindow", secondary=access_window_users, back_populates="users")
    content_progress = relationship("UserContentProgress", back_populates="user", cascade="all, delete-orphan")


class StockOrder(Base):
    __tablename__ = "stock_orders"

    id = Column(Integer, primary_key=True)
    external_id = Column(String(64))
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    type = Column(String(8), nullable=False, default="A")
    status = Column(String(32), nullable=False, default="new")
    client_name = Column(String(255))
    client_eik = Column(String(32))
    client_address = Column(String(255))
    client_phone = Column(String(32))
    delivery_date = Column(Date)
    delivery_time = Column(Time)
    recipient_name = Column(String(255))
    recipient_phone = Column(String(32))
    delivery_address = Column(String(255))
    delivery_gmaps_link = Column(String(255))
    note = Column(Text)
    versus_status = Column(String(32), default="ok")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_handover_at = Column(DateTime)
    last_handover_by_id = Column(Integer, ForeignKey("users.id"))
    delivered_at = Column(DateTime)
    delivered_by_id = Column(Integer, ForeignKey("users.id"))

    warehouse = relationship("Warehouse")
    items = relationship("StockOrderItem", back_populates="stock_order", cascade="all, delete-orphan")
    assignments = relationship("StockOrderAssignment", back_populates="stock_order", cascade="all, delete-orphan")
    scan_tasks = relationship("ScanTask", back_populates="stock_order")
    ppp_document = relationship("PPPDocument", back_populates="stock_order", uselist=False, cascade="all, delete-orphan")
    last_handover_by = relationship("User", foreign_keys=[last_handover_by_id], backref="handovers")
    delivered_by = relationship("User", foreign_keys=[delivered_by_id], backref="delivered_orders")


class StockOrderItem(Base):
    __tablename__ = "stock_order_items"

    id = Column(Integer, primary_key=True)
    stock_order_id = Column(Integer, ForeignKey("stock_orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    service_point_id = Column(Integer, ForeignKey("service_points.id"))
    unit = Column(String(32))
    quantity_ordered = Column(Float, nullable=False, default=0.0)
    quantity_prepared = Column(Float, nullable=False, default=0.0)
    quantity_delivered = Column(Float, nullable=False, default=0.0)

    stock_order = relationship("StockOrder", back_populates="items")
    product = relationship("Product")
    service_point = relationship("ServicePoint", back_populates="stock_order_items")

    @property
    def remaining_to_prepare(self):
        return max((self.quantity_ordered or 0) - (self.quantity_prepared or 0), 0)

    @property
    def remaining_to_deliver(self):
        return max((self.quantity_prepared or 0) - (self.quantity_delivered or 0), 0)

    @property
    def preparation_status(self):
        if self.quantity_prepared >= self.quantity_ordered > 0:
            return "prepared"
        if 0 < self.quantity_prepared < self.quantity_ordered:
            return "partial"
        return "not_prepared"


class StockOrderAssignment(Base):
    __tablename__ = "stock_order_assignments"

    id = Column(Integer, primary_key=True)
    stock_order_id = Column(Integer, ForeignKey("stock_orders.id"), nullable=False)
    service_point_id = Column(Integer, ForeignKey("service_points.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(32), default="preparer")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    stock_order = relationship("StockOrder", back_populates="assignments")
    service_point = relationship("ServicePoint")
    user = relationship("User", back_populates="assignments")


class PPPDocument(Base):
    __tablename__ = "ppp_documents"

    id = Column(Integer, primary_key=True)
    stock_order_id = Column(Integer, ForeignKey("stock_orders.id"), nullable=False)
    versus_ppp_id = Column(String(64))
    pdf_url = Column(String(255))
    signed_pdf_url = Column(String(255))
    status = Column(String(32), default="generated")
    signature_image = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    stock_order = relationship("StockOrder", back_populates="ppp_document")


class ContentItem(Base):
    __tablename__ = "content_items"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    summary = Column(String(512))
    content_html = Column(Text)
    media_url = Column(String(255))
    content_type = Column(String(32), nullable=False, default="NEWS")
    category = Column(String(64))
    read_time_minutes = Column(Integer, default=0)
    is_published = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    progresses = relationship("UserContentProgress", back_populates="content_item", cascade="all, delete-orphan")


class UserContentProgress(Base):
    __tablename__ = "user_content_progress"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content_item_id = Column(Integer, ForeignKey("content_items.id"), nullable=False)
    is_read = Column(Boolean, default=False)
    reaction = Column(String(32))
    read_at = Column(DateTime)

    user = relationship("User", back_populates="content_progress")
    content_item = relationship("ContentItem", back_populates="progresses")
