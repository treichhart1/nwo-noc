from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Numeric,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.field.models import now


class Plan(Base):
    __tablename__ = "signup_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    audience: Mapped[str] = mapped_column(String(20))
    technology: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    public: Mapped[bool] = mapped_column(Boolean, default=False)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    monthly: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    installation: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    download_mbps: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    upload_mbps: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    promotion: Mapped[str | None] = mapped_column(String(500), nullable=True)
    eligibility: Mapped[str] = mapped_column(
        String(1000), default="Subject to approved qualification"
    )
    equipment: Mapped[list] = mapped_column(JSON, default=list)
    powercode_plan_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Area(Base):
    __tablename__ = "signup_coverage_areas"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    technology: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    polygon: Mapped[list] = mapped_column(JSON)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    sector_id: Mapped[int | None] = mapped_column(
        ForeignKey("sectors.id"), nullable=True
    )
    fiber_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("network_links.id"), nullable=True
    )
    evidence: Mapped[str] = mapped_column(String(1000))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Application(Base):
    __tablename__ = "signup_applications"
    __table_args__ = (
        Index("ix_signup_state_age", "state", "created_at", "id"),
        Index("ix_signup_assignee_age", "assigned_to", "created_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True)
    submission_key: Mapped[str] = mapped_column(String(36), unique=True)
    digest: Mapped[str] = mapped_column(String(64))
    lookup_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    wrapped_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    pii_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    phone_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    address_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    audience: Mapped[str] = mapped_column(String(20))
    request_kind: Mapped[str] = mapped_column(String(30))
    technology: Mapped[str] = mapped_column(String(20))
    plan_id: Mapped[int] = mapped_column(ForeignKey("signup_plans.id"), index=True)
    plan_snapshot: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(40), default="submitted")
    assigned_to: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    matched_customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_accounts.id"), nullable=True
    )
    proposed_site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id"), nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    redacted_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Event(Base):
    __tablename__ = "signup_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("signup_applications.id"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    reason_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class PublicBudget(Base):
    __tablename__ = "signup_public_budgets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window: Mapped[object] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class CaptchaUse(Base):
    __tablename__ = "signup_captcha_uses"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    submission_key: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class Qualification(Base):
    __tablename__ = "signup_qualifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("signup_applications.id"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class AppWork(Base):
    __tablename__ = "signup_work_orders"
    __table_args__ = (
        UniqueConstraint("application_id", "kind", name="uq_signup_work_kind"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("signup_applications.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    work_order_id: Mapped[int] = mapped_column(
        ForeignKey("field_work_orders.id"), unique=True
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Survey(Base):
    __tablename__ = "signup_surveys"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("signup_applications.id"), index=True
    )
    app_work_id: Mapped[int] = mapped_column(ForeignKey("signup_work_orders.id"))
    technician_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    surveyed_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(30))
    details_ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Quote(Base):
    __tablename__ = "signup_quotes"
    __table_args__ = (
        UniqueConstraint("application_id", "number", name="uq_signup_quote_number"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("signup_applications.id"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    plan_id: Mapped[int] = mapped_column(ForeignKey("signup_plans.id"))
    snapshot: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    issued_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    access_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    access_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    acceptance_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class ProvisionDraft(Base):
    __tablename__ = "signup_provision_drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("signup_applications.id"), unique=True
    )
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(30), default="documentation_only")


class NotificationDraft(Base):
    __tablename__ = "signup_notification_drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("signup_applications.id"), index=True
    )
    template: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(20))
    eligible: Mapped[bool] = mapped_column(Boolean)
    policy: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Photo(Base):
    __tablename__ = "signup_survey_photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("signup_applications.id"), index=True
    )
    app_work_id: Mapped[int] = mapped_column(ForeignKey("signup_work_orders.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(120))
    media_type: Mapped[str] = mapped_column(String(40))
    storage_key: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    scan_state: Mapped[str] = mapped_column(String(30))
    scan_source: Mapped[str] = mapped_column(String(30))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class QuoteResponse(Base):
    __tablename__ = "signup_quote_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("signup_quotes.id"), unique=True)
    decision: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
