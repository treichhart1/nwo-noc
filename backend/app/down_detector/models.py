from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Integer,
    Float,
    DateTime,
    JSON,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def now():
    return datetime.now(timezone.utc)


class OutageObservation(Base):
    __tablename__ = "outage_observations"
    __table_args__ = (
        Index("ix_outage_observation_key_time", "key", "observed_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(120), unique=True)
    key: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(20))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    body_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)


class OutageState(Base):
    __tablename__ = "outage_states"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int] = mapped_column(Integer)
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(30), default="unknown")
    classification: Mapped[str] = mapped_column(String(40), default="unknown")
    bad_count: Mapped[int] = mapped_column(Integer, default=0)
    good_count: Mapped[int] = mapped_column(Integer, default=0)
    bad_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    good_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_uptime: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class OutageEpisode(Base):
    __tablename__ = "outage_episodes"
    __table_args__ = (
        Index("ix_outage_episode_key_started", "key", "started_at", "id"),
        Index("ix_outage_episode_status_started", "status", "started_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(ForeignKey("outage_states.key"))
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("outage_episodes.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="active")
    classification: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence: Mapped[dict] = mapped_column(JSON)
    override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class OutageTimeline(Base):
    __tablename__ = "outage_timeline"
    __table_args__ = (Index("ix_outage_timeline_episode_cursor", "episode_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("outage_episodes.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    action: Mapped[str] = mapped_column(String(60))
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
