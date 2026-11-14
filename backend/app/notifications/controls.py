"""Persistent operator controls; only mock provider configuration and callbacks."""

import os, stat, json, hashlib, hmac
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.config import settings


class NotificationControl(Base):
    __tablename__ = "notification_control"
    id: Mapped[int] = mapped_column(primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class NotificationProviderConfig(Base):
    __tablename__ = "notification_provider_config"
    channel: Mapped[str] = mapped_column(String(20), primary_key=True)
    adapter: Mapped[str] = mapped_column(String(30), default="mock")
    sender: Mapped[str | None] = mapped_column(String(80), nullable=True)
    account_reference: Mapped[str | None] = mapped_column(String(40), nullable=True)
    registration_reference: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    callback_secret_ciphertext: Mapped[str | None] = mapped_column(
        String(8192), nullable=True
    )
    last_health_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health: Mapped[str] = mapped_column(String(30), default="not_tested")


class NotificationCallback(Base):
    __tablename__ = "notification_callbacks"
    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("notification_deliveries.id"))
    status: Mapped[str] = mapped_column(String(30))
    body_hash: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


def latch_path():
    return Path(settings.rf_scheduler_spool_dir) / "notifications.disabled"


def emergency_disabled():
    try:
        latch_path().lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def emergency_disable():
    p = latch_path()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = p.parent.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise ValueError("Private owned notification control directory required")
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return
    try:
        os.write(fd, b'{"disabled":true}\n')
        os.fsync(fd)
    finally:
        os.close(fd)
    directory = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def control_status(db):
    row = db.get(NotificationControl, 1)
    return {
        "paused": bool(row and row.paused),
        "emergency_disabled": emergency_disabled(),
        "external_delivery_enabled": False,
    }


def require_processing(db):
    s = control_status(db)
    if s["paused"] or s["emergency_disabled"]:
        raise ValueError(
            "Notification processing paused or emergency-disabled; no delivery attempted"
        )
