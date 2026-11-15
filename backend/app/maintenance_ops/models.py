from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Index,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.field.models import now


class Schedule(Base):
    __tablename__ = "maintenance_schedules"
    __table_args__ = (Index("ix_maintenance_due", "active", "next_due_at", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_assets.id"), nullable=True, index=True
    )
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("field_inspection_templates.id"), nullable=True
    )
    technician_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    maintenance_type: Mapped[str] = mapped_column(String(60), index=True)
    recurrence: Mapped[str] = mapped_column(String(30))
    custom_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    starts_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    next_index: Mapped[int] = mapped_column(Integer, default=0)
    lead_days: Mapped[int] = mapped_column(Integer, default=7)
    due_soon_days: Mapped[int] = mapped_column(Integer, default=7)
    overdue_days: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_equipment: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Occurrence(Base):
    __tablename__ = "maintenance_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id", "occurrence_index", name="uq_maintenance_occurrence"
        ),
        Index("ix_maintenance_occurrence_due", "due_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_schedules.id"), index=True
    )
    occurrence_index: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("field_work_orders.id"), unique=True
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class ServiceRecord(Base):
    __tablename__ = "maintenance_service_records"
    __table_args__ = (
        Index("ix_maintenance_service_asset_time", "asset_id", "serviced_at", "id"),
        Index("ix_maintenance_service_site_time", "site_id", "serviced_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("field_work_orders.id"), index=True
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("equipment_assets.id"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    technician_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    equipment_maintenance_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_maintenance.id"), unique=True
    )
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id"), nullable=True
    )
    serviced_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    diagnostics: Mapped[str] = mapped_column(String(4000))
    labor_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warranty_claim: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Availability(Base):
    __tablename__ = "maintenance_availability"
    id: Mapped[int] = mapped_column(primary_key=True)
    technician_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    starts_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class Reminder(Base):
    __tablename__ = "maintenance_reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_assets.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    evidence: Mapped[dict] = mapped_column(JSON)
    first_seen: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    last_seen: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Category(Base):
    __tablename__ = "document_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(160))


class Document(Base):
    __tablename__ = "maintenance_documents"
    __table_args__ = (
        Index("ix_document_site_category_id", "site_id", "category_id", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_assets.id"), nullable=True, index=True
    )
    category_id: Mapped[int] = mapped_column(ForeignKey("document_categories.id"))
    title: Mapped[str] = mapped_column(String(200))
    visibility: Mapped[str] = mapped_column(String(20), default="technician")
    approved_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=2555)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Version(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "number", name="uq_document_revision"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_documents.id"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(120))
    media_type: Mapped[str] = mapped_column(String(50))
    storage_key: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    scan_state: Mapped[str] = mapped_column(String(30), default="quarantined")
    scan_source: Mapped[str] = mapped_column(String(30), default="disabled")
    scanned_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    change_description: Mapped[str] = mapped_column(String(1000))


class DocumentEvent(Base):
    __tablename__ = "document_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_documents.id"), index=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id"), nullable=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
