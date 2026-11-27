from datetime import datetime, timezone
from sqlalchemy import (
    String,
    DateTime,
    Integer,
    BigInteger,
    Boolean,
    ForeignKey,
    JSON,
    Text,
    UniqueConstraint,
    Index,
    CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def now():
    return datetime.now(timezone.utc)


class NotificationPolicy(Base):
    __tablename__ = "notification_policy"
    id: Mapped[int] = mapped_column(primary_key=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class NotificationMaintenance(Base):
    __tablename__ = "notification_maintenance"
    id: Mapped[int] = mapped_column(primary_key=True)
    operations_window_id: Mapped[int | None] = mapped_column(
        ForeignKey("maintenance_windows.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    title: Mapped[str] = mapped_column(String(160))
    targets: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    advance_notice_minutes: Mapped[list] = mapped_column(
        JSON, default=lambda: [1440, 60]
    )
    target: Mapped[dict] = mapped_column(JSON)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )


class NotificationPlan(Base):
    __tablename__ = "notification_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id"), nullable=True, index=True
    )
    maintenance_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_maintenance.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    target: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text)
    slack_payload: Mapped[dict] = mapped_column(JSON)
    estimated_cost_microunits: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )


class NotificationRecipient(Base):
    __tablename__ = "notification_recipients"
    __table_args__ = (
        UniqueConstraint("plan_id", "customer_id", name="uq_notification_recipient"),
        UniqueConstraint("plan_id", "application_id", name="uq_notification_prospect"),
        Index("ix_notification_recipient_contact", "contact_hash", "id"),
        CheckConstraint("(customer_id IS NULL) <> (application_id IS NULL)", name="ck_notification_recipient_subject"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("notification_plans.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer_accounts.id"), nullable=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("signup_applications.id"), nullable=True)
    service_ids: Mapped[list] = mapped_column(JSON)
    contact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(50))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "plan_id", "channel", "recipient_key", name="uq_notification_delivery"
        ),
        Index("ix_notification_due", "state", "next_attempt_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("notification_plans.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    recipient_key: Mapped[str] = mapped_column(String(80))
    recipient_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_recipients.id", ondelete="CASCADE"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(30), default="queued")
    slack_authorization: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    slack_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sms_provider: Mapped[str | None] = mapped_column(String(20), ForeignKey("notification_sms_providers.name"), nullable=True)
    sms_authorization: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    provider_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_receipt: Mapped[str | None] = mapped_column(String(100), nullable=True)
    budget_charge_microunits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class NotificationAttempt(Base):
    __tablename__ = "notification_attempts"
    __table_args__ = (UniqueConstraint("slack_workspace_id", "slack_channel_id", "slack_ts", name="uq_slack_receipt"), Index("ix_slack_attempt_created", "slack_mode", "created_at"), UniqueConstraint("provider_name", "provider_message_id", name="uq_sms_provider_receipt"), Index("ix_sms_attempt_provider_created", "provider_name", "created_at"), Index("ix_sms_claim_expiry", "acceptance", "lease_expires_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(
        ForeignKey("notification_deliveries.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(40))
    slack_mode: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    slack_workspace_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    slack_channel_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    slack_ts: Mapped[str | None] = mapped_column(String(40), nullable=True)
    slack_thread_ts: Mapped[str | None] = mapped_column(String(40), nullable=True)
    slack_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(20), ForeignKey("notification_sms_providers.name"), nullable=True, index=True)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    provider_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    raw_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    acceptance: Mapped[str | None] = mapped_column(String(30), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_segments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_microunits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reported_cost_microunits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    destination_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NotificationSuppression(Base):
    __tablename__ = "notification_suppressions"
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer_accounts.id"), primary_key=True
    )
    reason: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NotificationCredential(Base):
    __tablename__ = "notification_credentials"
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NotificationContactSuppression(Base):
    __tablename__ = "notification_contact_suppressions"
    contact_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
