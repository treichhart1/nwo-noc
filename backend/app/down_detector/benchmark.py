"""Explicit synthetic PostgreSQL correlation benchmark; rolls fixture back."""

import json, time, resource
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from app.core.database import SessionLocal
from app.core.config import settings
from app.models.entities import Site, Device, NetworkLink
from .models import OutageEpisode
from .engine import ingest, reconcile
from .contracts import Observation


def measure(db, count):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit offline synthetic benchmark required")
    if db.scalar(select(func.count(OutageEpisode.id))):
        raise ValueError("Isolated empty detector fixture required")
    now = datetime.now(timezone.utc)
    sites = [
        Site(
            name=f"NOC97 scale {count} POP {i}",
            site_type="core-pop" if i == 0 else "tower",
        )
        for i in range(40)
    ]
    db.add_all(sites)
    db.flush()
    links = [
        NetworkLink(
            name=f"NOC97 scale {count} link {i}",
            a_site_id=sites[i - 1].id,
            z_site_id=sites[i].id,
            status="active",
        )
        for i in range(1, 40)
    ]
    db.add_all(links)
    devices = [
        Device(
            name=f"NOC97 synthetic {i}",
            source="synthetic",
            external_id=f"noc97-{count}-{i}",
            wisp_site_id=sites[1 + i % 39].id,
            vendor=["MikroTik", "Ubiquiti", "Cambium", "Racom", "Aviat"][i % 5],
        )
        for i in range(count)
    ]
    db.add_all(devices)
    db.flush()
    start = time.monotonic()
    for second in [-90, -60, -30]:
        for kind, ident in [("link", links[0].id)] + [
            ("device", d.id) for d in devices
        ]:
            ingest(
                db,
                Observation(
                    event_id=f"SYN-SCALE:{kind}:{ident}:{second}",
                    source="synthetic",
                    kind=kind,
                    target_id=ident,
                    observed_at=now + timedelta(seconds=second),
                    signals={"icmp": False, "snmp": False, "collector": True},
                ),
                now=now,
            )
    ingestion = (time.monotonic() - start) * 1000
    at = time.monotonic()
    result = reconcile(db, now=now)
    correlation = (time.monotonic() - at) * 1000
    at = time.monotonic()
    again = reconcile(db, now=now)
    replay = (time.monotonic() - at) * 1000
    assert (
        result["primary_incidents"] == 1
        and result["symptoms"] == count
        and again == result
    )
    return {
        "devices": count,
        "sites": 40,
        "observations": 3 * (count + 1),
        "ingestion_ms": round(ingestion, 2),
        "correlation_ms": round(correlation, 2),
        "reconcile_replay_ms": round(replay, 2),
        "primary_incidents": 1,
        "symptoms": count,
        "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "external_calls": 0,
        "limitations": "Synthetic recorded signals in one rolled-back transaction; not probe throughput or production SLA",
    }


if __name__ == "__main__":
    for count in [250, 500, 1000]:
        with SessionLocal() as db:
            try:
                print(json.dumps(measure(db, count)), flush=True)
            finally:
                db.rollback()
