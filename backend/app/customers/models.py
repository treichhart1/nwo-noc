from datetime import datetime, timezone
from sqlalchemy import (
    String,
    DateTime,
    Integer,
    Float,
    Boolean,
    ForeignKey,
    JSON,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class CustomerAccount(Base):
    __tablename__ = "customer_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sms_consent: Mapped[str] = mapped_column(String(20), default="unknown")
    sms_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    communication_preference: Mapped[str] = mapped_column(String(20), default="unknown")
    consent_record: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CustomerService(Base):
    __tablename__ = "customer_services"
    __table_args__ = (Index("ix_customer_service_status_site", "status", "site_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(120), unique=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer_accounts.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(20))
    service_type: Mapped[str] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plan: Mapped[str | None] = mapped_column(String(120), nullable=True)
    download_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    upload_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    equipment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sector_id: Mapped[int | None] = mapped_column(
        ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    fiber_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("network_links.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mapping_state: Mapped[str] = mapped_column(String(30), default="unknown")
    mapping_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CustomerSyncState(Base):
    __tablename__ = "customer_sync_state"
    provider: Mapped[str] = mapped_column(String(30), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="not_configured")
    cursor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    watermark: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_count: Mapped[int] = mapped_column(Integer, default=0)
    last_snapshot_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class CustomerSyncRun(Base):
    __tablename__ = "customer_sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    received: Mapped[int] = mapped_column(Integer, default=0)
    reconciled: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IncidentCustomer(Base):
    __tablename__ = "incident_customers"
    __table_args__ = (
        UniqueConstraint(
            "incident_id", "service_id", name="uq_incident_customer_service"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    service_id: Mapped[int] = mapped_column(
        ForeignKey("customer_services.id", ondelete="CASCADE"), index=True
    )
    classification: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[dict] = mapped_column(JSON)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
