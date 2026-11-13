import socket
from ipaddress import ip_address, ip_network
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select, text
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.orm import Session
from app.models.entities import Device, MonitorCheck, MonitorEvent
from app.core.config import settings

UP = "up"
DOWN = "down"
UNKNOWN = "unknown"

def _target(device: Device) -> tuple[str | None, int]:
    return (device.monitor_host or device.ip_address or device.hostname, device.monitor_port or 443)

def tcp_check(host: str, port: int, timeout: int) -> tuple[bool, float | None, str | None]:
    if settings.synthetic_monitoring_enabled:
        try:
            synthetic = ip_address(host) in ip_network("198.18.0.0/15")
        except ValueError:
            synthetic = False
        if synthetic:
            time.sleep(.001)
            return True, 1.0, None  # Explicit synthetic success, never a socket call.
    if settings.offline_mode and (host, port) != ("redis", 6379):
        return False, None, "Offline mode permits only the synthetic Redis monitoring target"
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = round((time.perf_counter() - started) * 1000, 2)
            return True, latency, None
    except OSError as exc:
        return False, None, str(exc)[:500]

POLL_LOCK = 850085


def check_device(db: Session, device: Device, force: bool = False, *, now=None,
                 probe_result=None, commit=True) -> MonitorCheck:
    now = now or datetime.now(timezone.utc)
    if commit and db.get_bind().dialect.name == "postgresql":
        # Serialize manual checks with worker batches; refresh state after lock.
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": POLL_LOCK})
        db.refresh(device)
    if not force and device.monitor_last_checked_at and device.monitor_last_checked_at <= now:
        due = device.monitor_last_checked_at + timedelta(seconds=max(device.monitor_interval_seconds, 15))
        if due > now:
            return MonitorCheck(device_id=device.id, status=device.monitor_status, latency_ms=device.monitor_latency_ms, checked_at=device.monitor_last_checked_at)
    host, port = _target(device)
    old = device.monitor_status or UNKNOWN
    if probe_result is not None:
        ok, latency, error = probe_result
    elif not host:
        ok, latency, error = False, None, "No monitoring target configured"
    else:
        ok, latency, error = tcp_check(host, port, min(max(device.monitor_timeout_seconds, 1), 10))
    failures = 0 if ok else device.monitor_consecutive_failures + 1
    new = UP if ok else (DOWN if failures >= 2 else old if old in (UP, DOWN) else UNKNOWN)
    check = MonitorCheck(device_id=device.id, status=UP if ok else DOWN, latency_ms=latency, error=error, checked_at=now)
    db.add(check)
    device.monitor_last_checked_at = now
    device.monitor_latency_ms = latency
    device.monitor_consecutive_failures = failures
    if ok:
        device.monitor_last_up_at = now
        device.last_seen = now
    elif new == DOWN and old != DOWN:
        device.monitor_last_down_at = now
    device.monitor_status = new
    if (new == DOWN and old != DOWN) or (new == UP and old == DOWN):
        event_type = "device_recovered" if new == UP else "device_down"
        db.add(MonitorEvent(device_id=device.id, event_type=event_type, from_status=old, to_status=new,
            message=f"{device.name} changed from {old} to {new}.", created_at=now))
    if commit:
        db.commit(); db.refresh(check)
    return check


def run_due_checks(db: Session, *, now=None, probe=None, progress=None) -> dict:
    """One transaction per batch; bounded concurrent probes never share a Session.

    PostgreSQL transaction lock prevents overlapping workers/manual checks from
    creating duplicate transitions. A crash rolls back the complete state batch.
    """
    started = time.perf_counter()
    frozen_clock = now is not None
    now = now or datetime.now(timezone.utc)
    if db.get_bind().dialect.name == "postgresql":
        acquired = db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": POLL_LOCK})
        if not acquired:
            db.rollback()
            return {"enabled": 0, "checked": 0, "overlap_skipped": True}
    devices = list(db.scalars(select(Device).where(Device.monitor_enabled.is_(True), Device.enabled.is_(True)).order_by(Device.id)))
    due = [d for d in devices if d.monitor_last_checked_at is None or d.monitor_last_checked_at > now
           or d.monitor_last_checked_at + timedelta(seconds=max(d.monitor_interval_seconds, 15)) <= now]
    targets = [(*_target(d), min(max(d.monitor_timeout_seconds, 1), 10)) for d in due]
    probe = probe or tcp_check
    def measure(target):
        host, port, timeout = target
        if not host:
            return False, None, "No monitoring target configured"
        try:
            return probe(host, port, timeout)
        except OSError:
            return False, None, "Monitoring timeout or connection failure"
    with ThreadPoolExecutor(max_workers=16) as executor:
        results = []
        for result in executor.map(measure, targets):
            results.append((result, now if frozen_clock else datetime.now(timezone.utc)))
            if progress is not None:
                progress()
    for device, (result, observed_at) in zip(due, results):
        check_device(db, device, now=observed_at, probe_result=result, commit=False)
    db.execute(delete(MonitorCheck).where(MonitorCheck.checked_at < now - timedelta(days=30)))
    db.commit()
    return {"enabled": len(devices), "checked": len(due), "overlap_skipped": False,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2)}
