"""Actual synthetic40-POP ingestion/assessment measurements; no network probes."""

import json, time, resource
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, event
from app.core.database import SessionLocal
from app.core.config import settings
from app.models.entities import Site, Device, Sector, NetworkLink, TelemetrySample
from app.customers.models import CustomerAccount, CustomerService, CustomerSyncState
from .models import TowerSample, TowerPolicyRow, TowerFinding
from .contracts import Policy
from .worker import consume
from .service import assess


def measure(db, count):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit isolated synthetic benchmark required")
    if db.scalar(select(func.count(TowerSample.id))) or db.scalar(
        select(func.count(CustomerAccount.id))
    ):
        raise ValueError("Empty isolated health/customer fixture required")
    now = datetime.now(timezone.utc)
    sites = [
        Site(
            name=f"NOC98 scale {count} POP {i:02}",
            site_type="core-pop" if i == 0 else "tower",
        )
        for i in range(40)
    ]
    db.add_all(sites)
    db.flush()
    vendors = ["MikroTik", "Ubiquiti", "Cambium", "Racom", "Aviat", "Synthetic UPS"]
    devices = [
        Device(
            source="synthetic",
            external_id=f"noc98-scale-{count}-{i}",
            name=f"NOC98 Synthetic {i}",
            vendor=vendors[i % 6],
            role=(
                "power"
                if i % 6 == 5
                else (
                    "access-point"
                    if i % 6 in {1, 2}
                    else "backhaul" if i % 6 in {3, 4} else "router"
                )
            ),
            wisp_site_id=sites[i % 40].id,
        )
        for i in range(count)
    ]
    db.add_all(devices)
    db.flush()
    sectors = {}
    for i, d in enumerate(devices):
        if i % 6 in {1, 2}:
            s = Sector(
                name=f"NOC98 scale {count} sector {i}",
                site_id=d.wisp_site_id,
                device_id=d.id,
            )
            db.add(s)
            db.flush()
            sectors[d.id] = s
    links = []
    for i in range(1, 40):
        a = next(d for d in devices if d.wisp_site_id == sites[0].id)
        z = next(d for d in devices if d.wisp_site_id == sites[i].id)
        l = NetworkLink(
            name=f"NOC98 scale {count} link {i}",
            a_site_id=sites[0].id,
            z_site_id=sites[i].id,
            a_device_id=a.id,
            z_device_id=z.id,
            capacity_mbps=1000.0,
            link_type="licensed-microwave",
        )
        db.add(l)
        links.append(l)
    db.add(
        CustomerSyncState(provider="powercode", status="healthy", last_success_at=now)
    )
    profiles = {
        d.id: {"low_battery_voltage": 44.0, "low_dc_voltage": 44.0}
        for i, d in enumerate(devices)
        if i % 6 == 5
    }
    policy_row = TowerPolicyRow(
        id=1,
        config=Policy(
            device_voltage_thresholds=profiles, provisioned_ratio_warning=25
        ).model_dump(mode="json"),
    )
    db.add(policy_row)
    for d in devices:
        if d.id not in sectors:
            continue
        for n in range(5):
            account = CustomerAccount(
                external_id=f"NOC98-{count}-{d.id}-{n}",
                source_updated_at=now,
                last_synced_at=now,
            )
            db.add(account)
            db.flush()
            db.add(
                CustomerService(
                    external_id=f"NOC98-S-{count}-{d.id}-{n}",
                    customer_id=account.id,
                    status="active",
                    service_type="wireless",
                    site_id=d.wisp_site_id,
                    device_id=d.id,
                    sector_id=sectors[d.id].id,
                    mapping_state="verified",
                    source_refs={},
                    source_updated_at=now,
                    last_synced_at=now,
                    download_mbps=100.0,
                    upload_mbps=20.0,
                    plan="Synthetic100/20",
                )
            )
    for sec in [-120, -60, 0]:
        for i, d in enumerate(devices):
            warning = (i // 6) % 10 == 0
            metrics = {"collector_ok": True}
            power = {}
            data = {}
            if i % 6 == 5:
                power = {
                    "ac_present": True,
                    "dc_voltage": 54.0,
                    "battery_voltage": 43.0 if warning else 52.0,
                    "ups_state": "low_battery" if warning else "online",
                    "charge_state": "idle",
                    "measured_full_capacity_wh": 3000.0 if warning else 4600.0,
                    "rated_capacity_wh": 4800.0,
                }
            else:
                metrics.update(
                    reachable=True,
                    cpu_percent=25.0,
                    memory_percent=40.0,
                    temperature_c=95.0 if warning else 45.0,
                    uptime_seconds=10000.0 + sec,
                    interface_up=True,
                    rf_ok=True,
                    rssi_dbm=-65.0,
                    snr_db=25.0,
                )
            if d.id in sectors:
                metrics.update(
                    tx_mbps=95.0 if warning else 30.0,
                    rx_mbps=20.0,
                    tx_capacity_mbps=100.0,
                    rx_capacity_mbps=100.0,
                    subscriber_down_capacity_mbps=100.0,
                    airtime_percent=95.0 if warning else 40.0,
                    subscribers=25,
                )
                data["sector_id"] = sectors[d.id].id
            data.update(metrics=metrics, power=power)
            db.add(
                TelemetrySample(
                    device_id=d.id,
                    sampled_at=now + timedelta(seconds=sec),
                    raw_data={"tower_health_v1": data},
                )
            )
    db.flush()
    counter = {"selects": 0, "statements": 0}
    connection = db.connection()

    def measured(conn, cursor, statement, params, context, many):
        counter["statements"] += 1
        if statement.lstrip().upper().startswith("SELECT"):
            counter["selects"] += 1

    event.listen(connection, "before_cursor_execute", measured)
    try:
        start = time.monotonic()
        processed = 0
        while True:
            result = consume(db, now=now, limit=200, assess_changed=False)
            processed += result["processed"]
            if result["processed"] == 0:
                break
        ingestion = (time.monotonic() - start) * 1000
        ingestion_queries = dict(counter)
        counter.update(selects=0, statements=0)
        at = time.monotonic()
        reports = assess(db, now=now, persist=True)
        assessment = (time.monotonic() - at) * 1000
        assessment_queries = dict(counter)
        assert (
            len(reports) == 40
            and sum(r["coverage"]["registered_enabled_devices"] for r in reports)
            == count
        )
        before = db.scalar(select(func.count(TowerFinding.id)))
        at = time.monotonic()
        again = consume(db, now=now, assess_changed=False)
        assess(db, now=now, persist=True)
        replay = (time.monotonic() - at) * 1000
        assert (
            again["processed"] == 0
            and db.scalar(select(func.count(TowerFinding.id))) == before
        )
        return {
            "devices": count,
            "sites": 40,
            "source_samples": processed,
            "normalized_samples": db.scalar(select(func.count(TowerSample.id))),
            "ingestion_ms": round(ingestion, 2),
            "assessment_ms": round(assessment, 2),
            "ingestion_queries": ingestion_queries,
            "assessment_queries": assessment_queries,
            "repeat_assessment_ms": round(replay, 2),
            "maintenance_findings": before,
            "duplicate_findings": 0,
            "customer_services": sum(
                r["customers"]["known_active_services"] for r in reports
            ),
            "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "external_calls": 0,
            "limitations": "Three synthetic frames/device and reviewed synthetic assignments; not dense90-day history, native polling throughput or production sizing",
        }
    finally:
        event.remove(connection, "before_cursor_execute", measured)


if __name__ == "__main__":
    for count in [250, 500, 1000]:
        with SessionLocal() as db:
            try:
                print(json.dumps(measure(db, count)), flush=True)
            finally:
                db.rollback()
