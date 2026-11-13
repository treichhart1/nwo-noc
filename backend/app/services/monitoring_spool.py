"""Private, fsynced, atomic normalized-telemetry outbox; no credentials/config paths."""

import hashlib, json, os, stat, tempfile
from pathlib import Path
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.services.monitoring_collectors import Envelope


class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_key: str = Field(
        pattern=r"^(synthetic|radio|microwave|mikrotik):[1-9][0-9]{0,10}$"
    )
    slot_at: datetime
    observation: Envelope


def directory(root):
    p = Path(root)
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = p.lstat()
    if (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_mode & 0o077
        and not any(p.iterdir())
    ):
        p.chmod(0o700)
        info = p.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Monitoring spool requires a private owned directory")
    return p


def target(root, key, slot):
    return directory(root) / (
        hashlib.sha256((key + "|" + slot.isoformat()).encode()).hexdigest() + ".json"
    )


def read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as f:
        st = os.fstat(f.fileno())
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.geteuid()
            or st.st_mode & 0o077
            or st.st_size > 262144
        ):
            raise ValueError("Invalid private spool record")
        return Delivery.model_validate_json(f.read(262145))


def pending(root, key, slot):
    p = target(root, key, slot)
    if not p.exists():
        return None
    delivery = read(p)
    if delivery.job_key != key or delivery.slot_at != slot:
        raise ValueError("Spool identity mismatch")
    return delivery


def put(root, delivery):
    p = target(root, delivery.job_key, delivery.slot_at)
    data = delivery.model_dump_json().encode()
    if len(data) > 262144:
        raise ValueError("Spool record exceeds bounded size")
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(name, p)
        except FileExistsError:
            pass
        sync = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(sync)
        finally:
            os.close(sync)
    finally:
        os.unlink(name)
    return pending(root, delivery.job_key, delivery.slot_at)


def acknowledge(root, key, slot):
    p = target(root, key, slot)
    try:
        p.unlink()
    except FileNotFoundError:
        return
    fd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def count(root):
    return sum(1 for _ in directory(root).glob("*.json"))


def reconcile(root, db, *, limit=100):
    """Only remove outbox entries whose exact slot already has a committed receipt."""
    from sqlalchemy import select
    from app.models.entities import MonitoringReceipt, TelemetrySample

    removed = 0
    for p in sorted(directory(root).glob("*.json"))[:limit]:
        try:
            d = read(p)
        except (ValueError, OSError):
            continue  # Retain damaged delivery for operator review; isolate other jobs.
        receipt = db.scalar(
            select(MonitoringReceipt).where(
                MonitoringReceipt.job_key == d.job_key,
                MonitoringReceipt.slot_at == d.slot_at,
            )
        )
        if receipt:
            sample = db.get(TelemetrySample, receipt.sample_id)
            if (
                sample
                and sample.device_id == d.observation.device_id
                and sample.sampled_at == d.observation.sampled_at
            ):
                acknowledge(root, d.job_key, d.slot_at)
                removed += 1
    return removed
