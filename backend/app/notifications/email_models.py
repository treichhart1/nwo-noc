"""Email extensions of canonical plans/outbox; no duplicate queue or customer registry."""

from datetime import datetime
from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    JSON,
    Text,
    ForeignKey,
    UniqueConstraint,
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.notifications.models import now


class EmailProvider(Base):
    __tablename__ = "notification_email_providers"
    name: Mapped[str] = mapped_column(String(20), primary_key=True)
    config: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    live_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    authorization_reference: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    webhook_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    health: Mapped[str] = mapped_column(String(40), default="not_verified")
    last_submission_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_callback_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EmailRoutingRow(Base):
    __tablename__ = "notification_email_routing"
    id: Mapped[int] = mapped_column(primary_key=True)
    config: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class EmailPreference(Base):
    __tablename__ = "notification_email_preferences"
    __table_args__ = (
        UniqueConstraint("subject_key", "purpose", name="uq_email_subject_purpose"),
        CheckConstraint(
            "(customer_id IS NULL) <> (application_id IS NULL)",
            name="ck_email_preference_subject",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_key: Mapped[str] = mapped_column(String(60))
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_accounts.id"), nullable=True
    )
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("signup_applications.id"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(30))
    contact_hash: Mapped[str] = mapped_column(String(64), index=True)
    granted: Mapped[bool] = mapped_column(Boolean, default=False)
    reference: Mapped[str] = mapped_column(String(120))
    verified_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailSuppression(Base):
    __tablename__ = "notification_email_suppressions"
    contact_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(30))
    evidence: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailTemplate(Base):
    __tablename__ = "notification_email_templates"
    __table_args__ = (
        UniqueConstraint("key", "revision", name="uq_email_template_revision"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(60))
    revision: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailEnvelope(Base):
    __tablename__ = "notification_email_envelopes"
    delivery_id: Mapped[int] = mapped_column(
        ForeignKey("notification_deliveries.id"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(
        ForeignKey("notification_email_providers.name")
    )
    source: Mapped[dict] = mapped_column(JSON)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("notification_email_templates.id")
    )
    preference_id: Mapped[int] = mapped_column(
        ForeignKey("notification_email_preferences.id")
    )
    contact_hash: Mapped[str] = mapped_column(String(64), index=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    authorization: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    correlation: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )


class EmailEvent(Base):
    __tablename__ = "notification_email_events"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    delivery_id: Mapped[int] = mapped_column(
        ForeignKey("notification_deliveries.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    body_hash: Mapped[str] = mapped_column(String(64))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailPreferenceEvent(Base):
    __tablename__ = "notification_email_preference_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    preference_id: Mapped[int] = mapped_column(
        ForeignKey("notification_email_preferences.id"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    granted: Mapped[bool] = mapped_column(Boolean)
    contact_hash: Mapped[str] = mapped_column(String(64))
    reference: Mapped[str] = mapped_column(String(120))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
