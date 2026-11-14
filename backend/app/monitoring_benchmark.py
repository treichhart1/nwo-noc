"""Real PostgreSQL/concurrent polling benchmark; synthetic transport only."""

import json, time, uuid, tempfile
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, text, select, func, update
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.core.database import Base
from app.models.entities import (
    Device,
    Site,
    NetworkLink,
    MonitoringJob,
    MonitoringAlert,
    MonitoringReceipt,
    TelemetrySample,
)
from app.services.monitoring_scheduler import run_batch
from app.services.monitoring_health import health
from app.services.process_resources import snapshot, elapsed as resource_elapsed


def measure(size):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
        and settings.rf_scheduler_enabled
    ):
        raise ValueError("Explicit isolated synthetic scheduler required")
    if size not in (250, 500, 1000):
        raise ValueError("Unsupported benchmark size")
    engine = create_engine(settings.database_url, pool_size=10, max_overflow=0)
    with engine.connect() as c:
        if c.scalar(text("SELECT current_database()")) != "nwo_noc":
            raise ValueError("Disposable nwo_noc database required")
    schema = "noc92_bench_" + uuid.uuid4().hex[:12]
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    factory = sessionmaker(scoped)
    base = datetime.now(timezone.utc)
    rounds = []
    try:
        Base.metadata.create_all(scoped)
        with factory() as db:
            sites = [
                Site(name=f"Synthetic POP {i}", site_type="pop") for i in range(40)
            ]
            db.add_all(sites)
            db.flush()
            vendors = ["Ubiquiti", "Cambium", "Racom", "Aviat", "MikroTik"]
            devices = [
                Device(
                    source="synthetic",
                    external_id=f"noc92-{i}",
                    name=f"Synthetic device {i}",
                    vendor=vendors[i % 5],
                    wisp_site_id=sites[i % 40].id,
                    enabled=True,
                )
                for i in range(size)
            ]
            db.add_all(devices)
            db.flush()
            for i, d in enumerate(devices):
                if i % 5 in (2, 3):
                    db.add(
                        NetworkLink(
                            name=f"Synthetic microwave {i}",
                            a_device_id=d.id,
                            z_device_id=devices[(i + 1) % size].id,
                            a_site_id=sites[i % 40].id,
                            z_site_id=sites[(i + 1) % 40].id,
                        )
                    )
                db.add(
                    MonitoringJob(
                        key=f"synthetic:{d.id}",
                        device_id=d.id,
                        kind="synthetic",
                        interval_seconds=15,
                        timeout_seconds=1,
                        lease_seconds=120,
                        next_due_at=base,
                        fixture={"mode": "weak", "latency_ms": 2},
                    )
                )
            db.commit()
        with tempfile.TemporaryDirectory(prefix="noc92-spool-") as root:
            for n in range(6):
                at = base + timedelta(seconds=n * 30)
                if n == 3:
                    with factory() as db:
                        db.execute(
                            update(MonitoringJob).values(
                                fixture={"mode": "normal", "latency_ms": 2}
                            )
                        )
                        db.commit()
                resources_start = snapshot()
                started = time.perf_counter()
                accepted = 0
                while True:
                    r = run_batch(
                        worker_id="benchmark",
                        session_factory=factory,
                        now=at,
                        spool_dir=root,
                    )
                    accepted += r.get("success", 0)
                    if not r["claimed"]:
                        break
                    assert r["success"] == r["claimed"], r
                elapsed = time.perf_counter() - started
                assert accepted == size
                with factory() as db:
                    active = db.scalar(
                        select(func.count(MonitoringAlert.id)).where(
                            MonitoringAlert.state == "active"
                        )
                    )
                if n == 2:
                    assert active > 0
                if n == 5:
                    assert active == 0
                rounds.append(
                    {
                        "round": n + 1,
                        "samples": accepted,
                        "elapsed_seconds": round(elapsed, 3),
                        "polls_per_second": round(size / elapsed, 2),
                        "active_alerts": active,
                        "resources": resource_elapsed(resources_start, elapsed),
                    }
                )
        with factory() as db:
            started = time.perf_counter()
            snapshot = health(db, now=base + timedelta(seconds=150))
            health_ms = (time.perf_counter() - started) * 1000
            assert db.scalar(select(func.count(MonitoringReceipt.id))) == size * 6
            assert db.scalar(select(func.count(TelemetrySample.id))) == size * 6
            assert snapshot["failures_24h"] == 0
            latency = db.execute(
                text(
                    f'SELECT avg(duration_ms),max(duration_ms) FROM "{schema}".monitoring_attempts'
                )
            ).one()
            return {
                "devices": size,
                "sites": 40,
                "concurrency": settings.rf_scheduler_concurrency,
                "rounds": rounds,
                "samples_and_unique_receipts": size * 6,
                "health_query_ms": round(health_ms, 2),
                "source_history_bytes": int(
                    db.scalar(
                        text(
                            "SELECT sum(pg_total_relation_size(quote_ident(:schema)||'.'||quote_ident(tablename))) FROM pg_tables WHERE schemaname=:schema AND tablename IN ('telemetry_samples','monitoring_jobs','monitoring_attempts','monitoring_alerts','monitoring_receipts')"
                        ),
                        {"schema": schema},
                    )
                    or 0
                ),
                "collection_mean_ms": round(latency[0], 2),
                "collection_max_ms": round(latency[1], 2),
                "recovery_validation": "three distinct normal samples clear RF condition; virtual 30-second schedule, actual concurrent PostgreSQL processing",
                "transport": "synthetic 2ms fixture; not real-device capacity or production SLA",
            }
    finally:
        with engine.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


if __name__ == "__main__":
    for size in (250, 500, 1000):
        print(json.dumps(measure(size)), flush=True)
