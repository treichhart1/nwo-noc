import socket
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from app.models.entities import Device, MonitorCheck, MonitorEvent
from app.core.config import settings

UP = "up"
DOWN = "down"
UNKNOWN = "unknown"

def _target(device: Device) -> tuple[str | None, int]:
    return (device.monitor_host or device.ip_address or device.hostname, device.monitor_port or 443)

def tcp_check(host: str, port: int, timeout: int) -> tuple[bool, float | None, str | None]:
    if settings.offline_mode and (host, port) != ("redis", 6379):
        return False, None, "Offline mode permits only the synthetic Redis monitoring target"
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = round((time.perf_counter() - started) * 1000, 2)
            return True, latency, None
    except OSError as exc:
        return False, None, str(exc)[:500]

def check_device(db: Session, device: Device, force: bool = False) -> MonitorCheck:
    now = datetime.now(timezone.utc)
    if not force and device.monitor_last_checked_at:
        due = device.monitor_last_checked_at + timedelta(seconds=max(device.monitor_interval_seconds, 15))
        if due > now:
            return MonitorCheck(device_id=device.id, status=device.monitor_status, latency_ms=device.monitor_latency_ms, checked_at=device.monitor_last_checked_at)
    host, port = _target(device)
    old = device.monitor_status or UNKNOWN
    if not host:
        ok, latency, error = False, None, "No monitoring host or device IP configured"
    else:
        ok, latency, error = tcp_check(host, port, max(device.monitor_timeout_seconds, 1))
    failures = 0 if ok else device.monitor_consecutive_failures + 1
    # Two failures are required before declaring a device down to reduce one-off false alarms.
    new = UP if ok else (DOWN if failures >= 2 else old if old in (UP, DOWN) else UNKNOWN)
    check = MonitorCheck(device_id=device.id, status=UP if ok else DOWN, latency_ms=latency, error=error, checked_at=now)
    db.add(check)
    device.monitor_last_checked_at = now
    device.monitor_latency_ms = latency
    device.monitor_consecutive_failures = failures
    if ok:
        device.monitor_last_up_at = now
        device.last_seen = now
    elif new == DOWN:
        device.monitor_last_down_at = now
    device.monitor_status = new
    if new != old and new in (UP, DOWN):
        event_type = "device_recovered" if new == UP else "device_down"
        db.add(MonitorEvent(device_id=device.id, event_type=event_type, from_status=old, to_status=new,
            message=f"{device.name} changed from {old} to {new}."))
    db.commit(); db.refresh(check)
    return check

def run_due_checks(db: Session) -> dict:
    devices = list(db.scalars(select(Device).where(Device.monitor_enabled.is_(True), Device.enabled.is_(True))))
    checked = 0
    for device in devices:
        before = device.monitor_last_checked_at
        check_device(db, device)
        if device.monitor_last_checked_at != before:
            checked += 1
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    db.execute(delete(MonitorCheck).where(MonitorCheck.checked_at < cutoff))
    db.commit()
    return {"enabled": len(devices), "checked": checked}
