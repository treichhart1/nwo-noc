"""Opt-in RF health PostgreSQL benchmark, never run on production databases.

Uses a disposable migrated database named nwo_noc and rolls back all inserted
synthetic inventory. Prints timing measurements, not a hard-coded performance
pass/fail threshold that would be unreliable on shared CI runners.
"""
import os
from datetime import datetime, timedelta, timezone
from statistics import median
from time import perf_counter

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.entities import Device, TelemetrySample
from app.services.rf_collection_health import get_rf_collection_health


def benchmark(*, devices=500, history=20, repeats=5):
    if not 1 <= devices <= 5000 or not 1 <= history <= 100 or not 1 <= repeats <= 20:
        raise ValueError("Benchmark dimensions exceed safety bounds")
    url = os.getenv("RF_INTEGRATION_TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Requires explicit disposable PostgreSQL test URL")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            name = connection.exec_driver_sql("SELECT current_database()").scalar_one()
            if name != "nwo_noc":
                raise RuntimeError("Refusing to benchmark unexpected database name")
            transaction = connection.begin()
            try:
                with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
                    now = datetime.now(timezone.utc)
                    radios = [
                        Device(source="uisp", external_id=f"rf-benchmark-{i}",
                               name=f"Benchmark radio {i}", enabled=True)
                        for i in range(devices)
                    ]
                    db.add_all(radios)
                    db.flush()
                    db.add_all([
                        TelemetrySample(
                            device_id=radio.id,
                            sampled_at=now - timedelta(minutes=sample),
                            signal_dbm=-65,
                            raw_data={"rf_metrics_v1": {"rssi_dbm": -65.0}},
                        )
                        for radio in radios for sample in range(history)
                    ])
                    db.flush()
                    # Statistics must reflect the synthetic data for useful query plans.
                    connection.exec_driver_sql("ANALYZE devices")
                    connection.exec_driver_sql("ANALYZE telemetry_samples")
                    durations = []
                    for _ in range(repeats):
                        start = perf_counter()
                        result = get_rf_collection_health(db, now=now)
                        durations.append((perf_counter() - start) * 1000)
                        assert result["rf_devices_fresh"] >= devices
                    return {
                        "devices": devices,
                        "history_per_device": history,
                        "synthetic_samples": devices * history,
                        "repeats": repeats,
                        "median_ms": round(median(durations), 2),
                        "max_ms": round(max(durations), 2),
                    }
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--devices", type=int, default=500)
    parser.add_argument("--history", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(benchmark(devices=args.devices, history=args.history, repeats=args.repeats), sort_keys=True))
