from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Index,
    CheckConstraint,
    UniqueConstraint,
    LargeBinary,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class QRLabel(Base):
    __tablename__ = "field_qr_labels"
    __table_args__ = (
        CheckConstraint(
            "(site_id IS NULL) <> (asset_id IS NULL)", name="ck_field_qr_target"
        ),
        Index(
            "uq_field_qr_site_active",
            "site_id",
            unique=True,
            postgresql_where=__import__("sqlalchemy").text("revoked_at IS NULL"),
        ),
        Index(
            "uq_field_qr_asset_active",
            "asset_id",
            unique=True,
            postgresql_where=__import__("sqlalchemy").text("revoked_at IS NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    identifier: Mapped[str] = mapped_column(String(36), unique=True, default=uid)
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id"), nullable=True, index=True
    )
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_assets.id"), nullable=True, index=True
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Grant(Base):
    __tablename__ = "field_site_grants"
    __table_args__ = (
        UniqueConstraint("site_id", "technician_id", name="uq_field_site_technician"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    technician_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Template(Base):
    __tablename__ = "field_inspection_templates"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_field_template_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), index=True)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(160))
    items: Mapped[list] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkOrder(Base):
    __tablename__ = "field_work_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','assigned','scheduled','in_progress','waiting_for_parts','blocked','completed','cancelled')",
            name="ck_field_work_status",
        ),
        Index("ix_field_work_assigned_status_id", "technician_id", "status", "id"),
        Index("ix_field_work_site_created", "site_id", "created_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(
        String(40), unique=True, default=lambda: "WO-" + uid()
    )
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_assets.id"), nullable=True, index=True
    )
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id"), nullable=True, index=True
    )
    finding_id: Mapped[int | None] = mapped_column(
        ForeignKey("tower_health_findings.id"), nullable=True
    )
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("field_inspection_templates.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(4000))
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    technician_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="open")
    required_equipment: Mapped[list] = mapped_column(JSON, default=list)
    follow_up: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Event(Base):
    __tablename__ = "field_work_events"
    __table_args__ = (Index("ix_field_event_work_id", "work_order_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("field_work_orders.id"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Inspection(Base):
    __tablename__ = "field_inspections"
    __table_args__ = (Index("ix_field_inspection_work_id", "work_order_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("field_work_orders.id"), index=True
    )
    template_id: Mapped[int] = mapped_column(
        ForeignKey("field_inspection_templates.id")
    )
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_assets.id"), nullable=True, index=True
    )
    technician_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    inspected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    snapshot: Mapped[dict] = mapped_column(JSON)
    results: Mapped[list] = mapped_column(JSON)
    notes: Mapped[str] = mapped_column(String(4000))
    qualified_review: Mapped[bool] = mapped_column(Boolean, default=False)
    equipment_maintenance_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_maintenance.id"), nullable=True
    )


class Receipt(Base):
    __tablename__ = "field_mutation_receipts"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation_key", name="uq_field_actor_operation"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    operation_key: Mapped[str] = mapped_column(String(36))
    digest: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Attachment(Base):
    __tablename__ = "field_attachments"
    __table_args__ = (
        CheckConstraint("size > 0 AND size <= 524288", name="ck_field_attachment_size"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("field_work_orders.id"), index=True
    )
    inspection_id: Mapped[int | None] = mapped_column(
        ForeignKey("field_inspections.id"), nullable=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(120))
    media_type: Mapped[str] = mapped_column(String(40))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
