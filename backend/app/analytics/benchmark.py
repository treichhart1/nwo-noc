"""Rollback-only synthetic analytics load; no polling or external network access."""

import json, time, resource
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import insert, text
from app.core.database import SessionLocal
from app.models.entities import Site, Device, TelemetrySample, User
from . import service as s
from .contracts import RangeInput, RollupInput


def main():
    for size in (250, 500, 1000):
        with SessionLocal() as db:
            actor = User(
                username="analytics-bench-" + uuid4().hex,
                email="analytics-bench@example.invalid",
                role="admin",
                is_active=True,
                password_hash="unusable",
            )
            db.add(actor)
            db.flush()
            sites = [
                Site(name="Synthetic analytics POP " + uuid4().hex) for _ in range(40)
            ]
            db.add_all(sites)
            db.flush()
            devices = [
                Device(
                    source="synthetic",
                    external_id=uuid4().hex,
                    name="Synthetic benchmark",
                    wisp_site_id=sites[i % 40].id,
                    enabled=True,
                )
                for i in range(size)
            ]
            db.add_all(devices)
            db.flush()
            at = s.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
            rows = [
                {
                    "device_id": d.id,
                    "sampled_at": at + timedelta(minutes=j * 5),
                    "cpu_percent": 30 + j,
                    "ram_percent": 50,
                    "raw_data": {},
                }
                for d in devices
                for j in range(12)
            ]
            begin = time.perf_counter()
            db.execute(insert(TelemetrySample), rows)
            db.flush()
            ingest = time.perf_counter() - begin
            db.execute(text("SET LOCAL statement_timeout='30000ms'"))
            lat = []
            for _ in range(3):
                begin = time.perf_counter()
                r = s.series(
                    db,
                    RangeInput(
                        preset="24h", metric="cpu_percent", group="site", max_points=60
                    ),
                )
                lat.append((time.perf_counter() - begin) * 1000)
            begin = time.perf_counter()
            rr = s.rollup(
                db,
                actor,
                RollupInput(
                    operation_key=uuid4(),
                    reason="Rollback-only measured synthetic analytics benchmark",
                    start=at,
                    end=at + timedelta(hours=1),
                ),
            )
            roll = time.perf_counter() - begin
            print(
                json.dumps(
                    {
                        "pops": 40,
                        "devices": size,
                        "samples": len(rows),
                        "ingest_seconds": round(ingest, 3),
                        "report_ms": [round(x, 2) for x in lat],
                        "returned_points": len(r["points"]),
                        "rollup_seconds": round(roll, 3),
                        "rollup_rows": rr["rows"],
                        "max_rss_kib": resource.getrusage(
                            resource.RUSAGE_SELF
                        ).ru_maxrss,
                        "device_polls": 0,
                        "transaction": "rolled_back",
                    }
                ),
                flush=True,
            )
            db.rollback()


if __name__ == "__main__":
    main()
