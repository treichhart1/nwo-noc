"""Provider extensions to the existing notification/consent/queue records."""

from datetime import datetime
from sqlalchemy import (
    String,
    Text,
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    BigInteger,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.notifications.models import now


class SMSProviderConfig(Base):
    __tablename__ = "notification_sms_providers"
    name: Mapped[str] = mapped_column(String(20), primary_key=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    live_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    authorization_reference: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    webhook_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_submission_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_callback_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    health: Mapped[str] = mapped_column(String(40), default="not_verified")


class SMSRouting(Base):
    __tablename__ = "notification_sms_routing"
    id: Mapped[int] = mapped_column(primary_key=True)
    config: Mapped[dict] = mapped_column(
        JSON, default=lambda: {"primary": "mock", "secondary": None, "failover": False}
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)


class SMSWebhookEvent(Base):
    __tablename__ = "notification_sms_events"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(
        ForeignKey("notification_sms_providers.name"), index=True
    )
    message_id: Mapped[str] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    provider_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    raw_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reported_cost_microunits: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    body_hash: Mapped[str] = mapped_column(String(64))
    contact_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_deliveries.id"), nullable=True, index=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class SMSCallbackBudget(Base):
    __tablename__ = "notification_sms_callback_budget"
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
