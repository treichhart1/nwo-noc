"""Slack metadata extends the canonical NOC notification outbox."""

from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, JSON, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.notifications.models import now


class SlackIntegration(Base):
    __tablename__ = "notification_slack_integration"
    id: Mapped[int] = mapped_column(primary_key=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    live_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    authorization_reference: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    signing_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    health: Mapped[str] = mapped_column(String(40), default="not_verified")
    last_submission_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_callback_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    backoff_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SlackThread(Base):
    __tablename__ = "notification_slack_threads"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(40), index=True)
    topic: Mapped[str] = mapped_column(String(80))
    timestamp: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pending_delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_deliveries.id"), nullable=True
    )


class SlackUserMap(Base):
    __tablename__ = "notification_slack_users"
    slack_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    channels: Mapped[list] = mapped_column(JSON, default=list)


class SlackInteraction(Base):
    __tablename__ = "notification_slack_interactions"
    __table_args__ = (Index("ix_slack_interaction_pending", "state", "received_at"),)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("notification_deliveries.id"))
    slack_user_id: Mapped[str] = mapped_column(
        ForeignKey("notification_slack_users.slack_id")
    )
    workspace: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(40))
    message_ts: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(30))
    body_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="pending")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SlackSource(Base):
    __tablename__ = "notification_slack_sources"
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("notification_plans.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(30), index=True)
    identifier: Mapped[str] = mapped_column(String(80))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id"), nullable=True
    )
    work_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("field_work_orders.id"), nullable=True
    )


class SlackInteractionBudget(Base):
    __tablename__ = "notification_slack_callback_budget"
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
