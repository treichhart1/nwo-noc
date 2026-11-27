"""NOC-owned integration controls, not a second customer or billing database."""

from datetime import datetime
from sqlalchemy import (
    String,
    Text,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Numeric,
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.field.models import now


class PowercodeConnection(Base):
    __tablename__ = "powercode_connection"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    allowed_origin: Mapped[str | None] = mapped_column(String(500), nullable=True)
    auth_method: Mapped[str] = mapped_column(String(40), default="unverified")
    emergency_disabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("id=1", name="ck_powercode_single_connection"),)


class PowercodePackage(Base):
    __tablename__ = "powercode_packages"
    external_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    technology: Mapped[str] = mapped_column(String(20))
    audience: Mapped[str] = mapped_column(String(20))
    available: Mapped[bool] = mapped_column(Boolean)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    monthly: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    installation: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    download_mbps: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    upload_mbps: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now
    )


class PowercodePackageMapping(Base):
    __tablename__ = "powercode_package_mappings"
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("signup_plans.id"), primary_key=True
    )
    package_id: Mapped[str] = mapped_column(
        ForeignKey("powercode_packages.external_id")
    )
    package_digest: Mapped[str] = mapped_column(String(64))
    plan_revision: Mapped[int] = mapped_column(Integer)
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PowercodeServiceHistory(Base):
    __tablename__ = "powercode_service_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("customer_services.id"), index=True
    )
    previous_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    snapshot_id: Mapped[str] = mapped_column(String(120))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PowercodeProvision(Base):
    __tablename__ = "powercode_provisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("signup_applications.id"), unique=True
    )
    status: Mapped[str] = mapped_column(String(40), default="pending_review")
    preview_digest: Mapped[str] = mapped_column(String(64))
    preview_ciphertext: Mapped[str] = mapped_column(Text)
    application_revision: Mapped[int] = mapped_column(Integer)
    quote_id: Mapped[int] = mapped_column(ForeignKey("signup_quotes.id"))
    mapping_digest: Mapped[str] = mapped_column(String(64))
    attestations: Mapped[dict] = mapped_column(JSON)
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_account_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_service_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    activation_approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    activation_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_review','approved','queued','submitted','account_created','service_assigned','activation_pending','completed','failed','reconciliation_required','cancelled')",
            name="ck_powercode_provision_state",
        ),
        Index("ix_powercode_provision_queue", "status", "id"),
    )


class PowercodeAttempt(Base):
    __tablename__ = "powercode_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    provision_id: Mapped[int] = mapped_column(
        ForeignKey("powercode_provisions.id"), index=True
    )
    operation_key: Mapped[str] = mapped_column(String(36), unique=True)
    operation: Mapped[str] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(40), default="submitted")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PowercodeProvisionEvent(Base):
    __tablename__ = "powercode_provision_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    provision_id: Mapped[int] = mapped_column(
        ForeignKey("powercode_provisions.id"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
