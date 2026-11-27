from datetime import datetime, date
from sqlalchemy import (
    String,
    Text,
    Integer,
    Boolean,
    DateTime,
    Date,
    ForeignKey,
    JSON,
    Numeric,
    UniqueConstraint,
    Index,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.field.models import now


class Reason(Base):
    __tablename__ = "retention_reasons"
    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    voluntary: Mapped[str] = mapped_column(String(20), default="unknown")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Policy(Base):
    __tablename__ = "retention_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    text: Mapped[str] = mapped_column(String(4000))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=365)
    unresolved_days: Mapped[int] = mapped_column(Integer, default=7)
    tower_churn_threshold: Mapped[int] = mapped_column(Integer, default=3)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        Index(
            "uq_retention_active_policy",
            "active",
            unique=True,
            postgresql_where=sql_text("active"),
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Case(Base):
    __tablename__ = "retention_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(45), unique=True)
    submission_key: Mapped[str] = mapped_column(String(36), unique=True)
    digest: Mapped[str] = mapped_column(String(64))
    receipt_hash: Mapped[str] = mapped_column(String(64), unique=True)
    wrapped_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    pii_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_accounts.id"), nullable=True, index=True
    )
    requested_date: Mapped[date] = mapped_column(Date)
    reported_reason: Mapped[str] = mapped_column(ForeignKey("retention_reasons.code"))
    operator_reason: Mapped[str | None] = mapped_column(
        ForeignKey("retention_reasons.code"), nullable=True
    )
    policy_id: Mapped[int] = mapped_column(ForeignKey("retention_policies.id"))
    policy_ack_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    equipment_ack: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(40), default="pending_verification")
    retention_state: Mapped[str] = mapped_column(String(40), default="pending_review")
    competitor_id: Mapped[int | None] = mapped_column(
        ForeignKey("retention_competitors.id"), nullable=True
    )
    billing_disposition: Mapped[str] = mapped_column(String(30), default="unreviewed")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(30), default="public_request")
    import_key: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    verified_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    redacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        Index("ix_retention_case_state_age", "status", "created_at", "id"),
    )


class CaseService(Base):
    __tablename__ = "retention_case_services"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("retention_cases.id"), index=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("customer_services.id"), index=True
    )
    snapshot: Mapped[dict] = mapped_column(JSON)
    actual_disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_history_id: Mapped[int | None] = mapped_column(
        ForeignKey("powercode_service_history.id"), nullable=True
    )
    equipment_return_required: Mapped[bool] = mapped_column(Boolean, default=False)
    equipment_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    equipment_returned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    __table_args__ = (
        UniqueConstraint("case_id", "service_id", name="uq_retention_case_service"),
    )


class CaseEvent(Base):
    __tablename__ = "retention_case_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("retention_cases.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    evidence: Mapped[dict] = mapped_column(JSON)
    notes_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Competitor(Base):
    __tablename__ = "retention_competitors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    technology: Mapped[str] = mapped_column(String(30), default="unknown")
    service_areas: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Observation(Base):
    __tablename__ = "retention_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(
        ForeignKey("retention_competitors.id"), index=True
    )
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("retention_cases.id"), nullable=True
    )
    offer: Mapped[dict] = mapped_column(JSON)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_reference: Mapped[str] = mapped_column(String(200))
    observed_on: Mapped[date] = mapped_column(Date)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Offer(Base):
    __tablename__ = "retention_offers"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("retention_cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    terms_ciphertext: Mapped[str] = mapped_column(Text)
    monthly_cost: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    one_time_cost: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    eligibility: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    acceptance_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Cohort(Base):
    __tablename__ = "retention_cohorts"
    id: Mapped[int] = mapped_column(primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, unique=True)
    complete: Mapped[bool] = mapped_column(Boolean)
    members: Mapped[list] = mapped_column(JSON)
    source_reference: Mapped[str] = mapped_column(String(120))
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ImportRun(Base):
    __tablename__ = "retention_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    rows: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
