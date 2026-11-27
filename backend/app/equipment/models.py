"""Physical assets extend existing NOC Device/Site identities; stock is separate."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4, UUID
from sqlalchemy import (
    String,
    Integer,
    Date,
    DateTime,
    Numeric,
    Boolean,
    JSON,
    ForeignKey,
    Index,
    UniqueConstraint,
    CheckConstraint,
    LargeBinary,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def now():
    return datetime.now(timezone.utc)


class Category(Base):
    __tablename__ = "equipment_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(120))


class Manufacturer(Base):
    __tablename__ = "equipment_manufacturers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(120), unique=True)


class Product(Base):
    __tablename__ = "equipment_products"
    __table_args__ = (
        UniqueConstraint(
            "manufacturer_id",
            "model_key",
            "hardware_revision",
            name="uq_equipment_product_model",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    manufacturer_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_manufacturers.id"), index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_categories.id"), index=True
    )
    model: Mapped[str] = mapped_column(String(160))
    model_key: Mapped[str] = mapped_column(String(160))
    hardware_revision: Mapped[str] = mapped_column(String(80), default="")
    individually_tracked: Mapped[bool] = mapped_column(Boolean, default=True)
    unit: Mapped[str] = mapped_column(String(20), default="each")
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    planned_retirement_date: Mapped[datetime | None] = mapped_column(
        Date, nullable=True
    )
    lifecycle_source: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Location(Base):
    __tablename__ = "equipment_locations"
    application_id: Mapped[int | None] = mapped_column(ForeignKey("signup_applications.id"), nullable=True, index=True)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(30))
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id"), nullable=True, index=True
    )
    technician_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_services.id"), nullable=True
    )


class Asset(Base):
    __tablename__ = "equipment_assets"
    __table_args__ = (
        UniqueConstraint(
            "manufacturer_id", "serial_key", name="uq_equipment_manufacturer_serial"
        ),
        CheckConstraint(
            "state IN ('ordered','received','available','reserved','installed','in_service','under_maintenance','failed','removed','returned','retired')",
            name="ck_equipment_asset_state",
        ),
        CheckConstraint("revision>=1", name="ck_equipment_asset_revision"),
        Index("ix_equipment_asset_location_state", "location_id", "state", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[UUID] = mapped_column(Uuid, unique=True, default=uuid4)
    asset_tag: Mapped[str] = mapped_column(String(80), unique=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_products.id"), index=True
    )
    manufacturer_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_manufacturers.id")
    )
    serial_number: Mapped[str | None] = mapped_column(String(160), nullable=True)
    serial_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mac_address: Mapped[str | None] = mapped_column(
        String(17), nullable=True, unique=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True, unique=True
    )
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_locations.id"), nullable=True, index=True
    )
    technician_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(30), default="ordered", index=True)
    condition: Mapped[str] = mapped_column(String(20), default="unknown")
    documented_operational_status: Mapped[str] = mapped_column(
        String(20), default="unknown"
    )
    recorded_firmware: Mapped[str | None] = mapped_column(String(120), nullable=True)
    hardware_revision: Mapped[str | None] = mapped_column(String(80), nullable=True)
    purchase_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(160), nullable=True)
    warranty_end: Mapped[datetime | None] = mapped_column(
        Date, nullable=True, index=True
    )
    warranty_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retirement_disposition: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    replacement_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_assets.id"), nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DeviceBinding(Base):
    __tablename__ = "equipment_device_bindings"
    __table_args__ = (
        Index(
            "uq_equipment_current_device_binding",
            "device_id",
            unique=True,
            postgresql_where=__import__("sqlalchemy").text("ended_at IS NULL"),
        ),
        Index(
            "uq_equipment_current_asset_binding",
            "asset_id",
            unique=True,
            postgresql_where=__import__("sqlalchemy").text("ended_at IS NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("equipment_assets.id"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Movement(Base):
    __tablename__ = "equipment_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("equipment_assets.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(1000))
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class BulkBalance(Base):
    __tablename__ = "equipment_bulk_balances"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "location_id", "state", name="uq_equipment_bulk_bucket"
        ),
        CheckConstraint("quantity>=0", name="ck_equipment_bulk_nonnegative"),
        CheckConstraint(
            "state IN ('available','reserved','installed','repair','retired')",
            name="ck_equipment_bulk_state",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_products.id"), index=True
    )
    location_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_locations.id"), index=True
    )
    state: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=Decimal(0))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class BulkMovement(Base):
    __tablename__ = "equipment_bulk_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(120), unique=True)
    digest: Mapped[str] = mapped_column(String(64))
    product_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_products.id"), index=True
    )
    from_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_locations.id"), nullable=True
    )
    to_location_id: Mapped[int] = mapped_column(ForeignKey("equipment_locations.id"))
    from_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_state: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    action: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(1000))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class Maintenance(Base):
    __tablename__ = "equipment_maintenance"
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("equipment_assets.id"), index=True)
    inspected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    technician_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20))
    condition: Mapped[str] = mapped_column(String(20))
    work_performed: Mapped[str] = mapped_column(String(4000))
    fault_description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    repair_actions: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    parts: Mapped[list] = mapped_column(JSON, default=list)
    follow_up: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    tower_finding_id: Mapped[int | None] = mapped_column(
        ForeignKey("tower_health_findings.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Attachment(Base):
    __tablename__ = "equipment_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    maintenance_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_maintenance.id"), index=True
    )
    filename: Mapped[str] = mapped_column(String(120))
    media_type: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    restricted_financial: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Financial(Base):
    __tablename__ = "equipment_financials"
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_assets.id"), primary_key=True
    )
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    purchase_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    replacement_estimate: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    installation_labor_estimate: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    salvage_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    depreciation_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    useful_life_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class CostEntry(Base):
    __tablename__ = "equipment_cost_entries"
    __table_args__ = (
        CheckConstraint("amount>=0", name="ck_equipment_cost_nonnegative"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_assets.id"), nullable=True, index=True
    )
    maintenance_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_maintenance.id"), nullable=True, unique=True
    )
    bulk_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_bulk_movements.id"), nullable=True, unique=True
    )
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_locations.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StockPolicy(Base):
    __tablename__ = "equipment_stock_policies"
    __table_args__ = (
        CheckConstraint(
            "(product_id IS NULL) <> (category_id IS NULL)",
            name="ck_equipment_stock_scope",
        ),
        CheckConstraint("minimum_available>=0", name="ck_equipment_stock_minimum"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_products.id"), nullable=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_categories.id"), nullable=True
    )
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_locations.id"), nullable=True
    )
    unit: Mapped[str] = mapped_column(String(20))
    minimum_available: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    warranty_warning_days: Mapped[int] = mapped_column(Integer, default=60)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)


class ImportBatch(Base):
    __tablename__ = "equipment_import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(30))
    digest: Mapped[str] = mapped_column(String(64))
    rows: Mapped[list] = mapped_column(JSON)
    errors: Mapped[list] = mapped_column(JSON)
    applied_ids: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(20), default="preview")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RequestBudget(Base):
    __tablename__ = "equipment_request_budgets"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)


class FinancialChange(Base):
    __tablename__ = "equipment_financial_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("equipment_assets.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(1000))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
