from datetime import datetime, timezone, date
from sqlalchemy import (
    String,
    Integer,
    DateTime,
    Date,
    JSON,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def now():
    return datetime.now(timezone.utc)


class TowerEvent(Base):
    __tablename__ = "tower_health_events"
    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    body_hash: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TowerSample(Base):
    __tablename__ = "tower_health_samples"
    __table_args__ = (
        Index("ix_tower_sample_device_time", "device_id", "observed_at", "id"),
        Index("ix_tower_sample_site_time", "site_id", "observed_at", "id"),
        Index("ix_tower_sample_scope_time", "scope_key", "observed_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("tower_health_events.event_id"), unique=True
    )
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    sector_id: Mapped[int | None] = mapped_column(
        ForeignKey("sectors.id"), nullable=True
    )
    link_id: Mapped[int | None] = mapped_column(
        ForeignKey("network_links.id"), nullable=True
    )
    scope_key: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(30))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class TowerReceipt(Base):
    __tablename__ = "tower_health_receipts"
    sample_id: Mapped[int] = mapped_column(
        ForeignKey("telemetry_samples.id", ondelete="CASCADE"), primary_key=True
    )
    result: Mapped[str] = mapped_column(String(30))


class TowerDaily(Base):
    __tablename__ = "tower_health_daily"
    scope_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id"), primary_key=True, index=True
    )
    samples: Mapped[int] = mapped_column(Integer, default=0)
    values: Mapped[dict] = mapped_column(JSON, default=dict)


class TowerCurrent(Base):
    __tablename__ = "tower_health_current"
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), primary_key=True)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    report: Mapped[dict] = mapped_column(JSON)


class TowerHistory(Base):
    __tablename__ = "tower_health_history"
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), primary_key=True)
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    report: Mapped[dict] = mapped_column(JSON)


class TowerFinding(Base):
    __tablename__ = "tower_health_findings"
    __table_args__ = (
        UniqueConstraint(
            "site_id",
            "device_id",
            "scope_key",
            "rule",
            name="uq_tower_finding_scope_rule",
        ),
        Index("ix_tower_finding_site_state", "site_id", "state", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    scope_key: Mapped[str] = mapped_column(String(80))
    rule: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(30), default="active")
    priority: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[str] = mapped_column(String(30))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence: Mapped[dict] = mapped_column(JSON)
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )


class TowerNote(Base):
    __tablename__ = "tower_health_notes"
    __table_args__ = (Index("ix_tower_note_finding_cursor", "finding_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("tower_health_findings.id"))
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    action: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(String(1000))


class TowerPolicyRow(Base):
    __tablename__ = "tower_health_policy"
    id: Mapped[int] = mapped_column(primary_key=True)
    config: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
