"""Read-only adapters over existing stored telemetry. No endpoint discovery or IO."""

import hashlib, json
from datetime import timedelta
from sqlalchemy import select, case
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device, Sector, TelemetrySample
from .contracts import Observation, Metrics, Power
from .models import TowerReceipt
from .service import _ingest_locked, assess, clock, policy, lock


def normalize(row, device, sectors):
    raw = row.raw_data if isinstance(row.raw_data, dict) else {}
    m = {}
    power = {}
    unavailable = {}
    extra = {}
    source = "recorded_legacy"
    link_id = None
    sector_id = None
    if not isinstance(row.raw_data, dict):
        raise ValueError("Malformed stored observation")
    if "tower_health_v1" in raw:
        data = raw["tower_health_v1"]
        if not isinstance(data, dict) or set(data) - {
            "metrics",
            "power",
            "unavailable",
            "counter_scope",
            "link_id",
            "sector_id",
        }:
            raise ValueError("Closed normalized power/health contract required")
        m = data.get("metrics", {})
        power = data.get("power", {})
        unavailable = data.get("unavailable", {})
        extra = {"counter_scope": data.get("counter_scope")}
        link_id = data.get("link_id")
        sector_id = data.get("sector_id")
        source = "recorded_power"
    elif "rf_radio_v2" in raw:
        from app.services.radio_observation import RadioObservation
        from app.services.microwave_model import MODULATIONS

        r = RadioObservation.model_validate(raw["rf_radio_v2"])
        source = "recorded_radio"
        if r.observed_at != row.sampled_at:
            raise ValueError("Mismatched RF sample timestamp")
        fields = {
            "rssi_dbm": "rssi_dbm",
            "snr_db": "snr_db",
            "temperature_c": "temperature_c",
            "uptime_seconds": "uptime_seconds",
            "link_capacity_mbps": "reported_capacity_mbps",
            "tx_throughput_mbps": "tx_mbps",
            "rx_throughput_mbps": "rx_mbps",
            "subscriber_count": "subscribers",
            "utilization_percent": "utilization_percent",
        }
        for old, new in fields.items():
            value = getattr(r.measurements, old, None)
            if value is not None:
                m[new] = value
            elif old in r.unavailable:
                unavailable[new] = r.unavailable[old]
        if r.radio_status in {"up", "down", "degraded"}:
            m["rf_ok"] = r.radio_status == "up"
        if r.modulation in MODULATIONS:
            m["modulation_bits"] = MODULATIONS[r.modulation]
        m["collector_ok"] = True
        candidates = [s.id for s in sectors if s.device_id == device.id]
        if len(candidates) == 1:
            sector_id = candidates[0]
    elif "rf_microwave_v1" in raw:
        from app.services.microwave_model import MicrowaveObservation, MODULATIONS
        from app.services.microwave_history import validate_association
        from app.models.entities import NetworkLink

        r = MicrowaveObservation.model_validate(raw["rf_microwave_v1"])
        link_id = raw.get("microwave_link_id")
        # SQLAlchemy object_session avoids private/implicit transport construction.
        from sqlalchemy.orm import object_session

        link = object_session(row).get(NetworkLink, link_id)
        validate_association(device, link, r.profile)
        if r.observed_at != row.sampled_at:
            raise ValueError("Mismatched microwave timestamp")
        source = "recorded_microwave"
        m = {"collector_ok": True}
        for old, new in {
            "reported_capacity_mbps": "reported_capacity_mbps",
            "tx_throughput_mbps": "tx_mbps",
            "rx_throughput_mbps": "rx_mbps",
            "utilization_percent": "utilization_percent",
            "uptime_seconds": "uptime_seconds",
        }.items():
            value = getattr(r.measurements, old)
            if value is not None:
                m[new] = value
            elif old in r.unavailable:
                unavailable[new] = r.unavailable[old]
        if r.ethernet_status in {"up", "down"}:
            m["interface_up"] = r.ethernet_status == "up"
        carriers = list(r.carriers.values())
        statuses = [c.radio_status for c in carriers]
        if all(v == "up" for v in statuses):
            m["rf_ok"] = True
        elif any(v in {"down", "degraded"} for v in statuses):
            m["rf_ok"] = False
        # Lowest measured carrier values are explicitly labeled observations, never sums of capacity.
        for field, key in [("local_rsl_dbm", "rssi_dbm"), ("snr_db", "snr_db")]:
            values = [
                getattr(c.measurements, field)
                for c in carriers
                if getattr(c.measurements, field) is not None
            ]
            if values:
                m[key] = min(values)
        # A modulation trend is valid only for a single carrier; multicarrier history remains in RF diagnostics.
        if len(carriers) == 1 and carriers[0].modulation in MODULATIONS:
            m["modulation_bits"] = MODULATIONS[carriers[0].modulation]
    elif "mikrotik_v1" in raw:
        from app.services.monitoring_collectors import RouterSnapshot, Interface

        data = raw["mikrotik_v1"]
        if not isinstance(data, dict):
            raise ValueError("Malformed router snapshot")
        filtered = {k: v for k, v in data.items() if k in RouterSnapshot.model_fields}
        filtered["interfaces"] = [
            {k: v for k, v in i.items() if k in Interface.model_fields}
            for i in data.get("interfaces", [])
            if isinstance(i, dict)
        ]
        r = RouterSnapshot.model_validate(filtered)
        source = "recorded_router"
        m = {"collector_ok": True, "reachable": True}
        for old, new in {
            "cpu_percent": "cpu_percent",
            "ram_percent": "memory_percent",
            "temperature_c": "temperature_c",
            "uptime_seconds": "uptime_seconds",
        }.items():
            value = getattr(r, old)
            if value is not None:
                m[new] = value
        interfaces = [i for i in r.interfaces if i.disabled is False]
        states = [i.running for i in interfaces]
        if states and all(v is not None for v in states):
            m["interface_up"] = all(states)
        counts = [
            i.rx_errors + i.tx_errors
            for i in interfaces
            if i.rx_errors is not None and i.tx_errors is not None
        ]
        if interfaces and len(counts) == len(interfaces):
            m["interface_error_count"] = sum(counts)
            extra["counter_scope"] = hashlib.sha256(
                json.dumps(sorted(i.id for i in interfaces)).encode()
            ).hexdigest()
        # Traffic totals/rates are not guessed from interfaces or counter deltas without an explicit interface role.
    elif "rf_observation_v1" in raw:
        from app.services.rf_observation import NormalizedRFTelemetry

        r = NormalizedRFTelemetry.model_validate(raw["rf_observation_v1"])
        source = "recorded_radio"
        if r.observed_at != row.sampled_at:
            raise ValueError("Mismatched foundation timestamp")
        for old, new in {
            "rssi_dbm": "rssi_dbm",
            "snr_db": "snr_db",
            "temperature_c": "temperature_c",
            "uptime_seconds": "uptime_seconds",
            "link_capacity_mbps": "reported_capacity_mbps",
            "tx_rate_mbps": "tx_phy_rate_mbps",
            "rx_rate_mbps": "rx_phy_rate_mbps",
        }.items():
            value = getattr(r.measurements, old)
            if value is not None:
                m[new] = value
        if r.radio_status in {"up", "down", "degraded"}:
            m["rf_ok"] = r.radio_status == "up"
        m["collector_ok"] = True
    else:
        for old, new in {
            "cpu_percent": "cpu_percent",
            "ram_percent": "memory_percent",
            "temperature_c": "temperature_c",
            "signal_dbm": "rssi_dbm",
        }.items():
            value = getattr(row, old, None)
            if value is not None:
                m[new] = value
    return Observation(
        event_id=f"tower-sample:{row.id}",
        site_id=device.wisp_site_id,
        device_id=device.id,
        source=source,
        observed_at=row.sampled_at,
        link_id=link_id,
        sector_id=sector_id,
        metrics=Metrics.model_validate(m),
        power=Power.model_validate(power),
        unavailable=unavailable,
        **extra,
    )


def consume(db, *, now=None, limit=200, assess_changed=True):
    if not 1 <= limit <= 200:
        raise ValueError("At most200 recorded samples per transaction batch")
    now = now or clock()
    p = policy(db)
    lock(db)
    rows = list(
        db.scalars(
            select(TelemetrySample)
            .outerjoin(TowerReceipt, TowerReceipt.sample_id == TelemetrySample.id)
            .where(
                TowerReceipt.sample_id.is_(None),
                TelemetrySample.sampled_at <= now,
                TelemetrySample.sampled_at
                >= now - timedelta(days=p.raw_retention_days),
            )
            .order_by(
                case(
                    (
                        TelemetrySample.sampled_at
                        >= now - timedelta(seconds=p.max_age_seconds),
                        0,
                    ),
                    else_=1,
                ),
                TelemetrySample.sampled_at,
                TelemetrySample.id,
            )
            .limit(limit)
        )
    )
    devices = {
        d.id: d
        for d in db.scalars(
            select(Device).where(Device.id.in_([r.device_id for r in rows]))
        )
    }
    sectors = list(db.scalars(select(Sector).where(Sector.device_id.in_(devices))))
    changed = set()
    invalid = 0
    for row in rows:
        d = devices.get(row.device_id)
        if not d or not d.enabled or not d.wisp_site_id:
            db.add(TowerReceipt(sample_id=row.id, result="unmapped"))
            continue
        try:
            o = normalize(row, d, sectors)
        except (ValueError, TypeError, KeyError, AttributeError):
            invalid += 1
            o = Observation(
                event_id=f"tower-sample:{row.id}",
                site_id=d.wisp_site_id,
                device_id=d.id,
                source="recorded_legacy",
                observed_at=row.sampled_at,
                unavailable={
                    k: "invalid"
                    for k in set(Metrics.model_fields) | set(Power.model_fields)
                },
            )
        # A malformed association is isolated under a savepoint without dropping later queued rows.
        try:
            with db.begin_nested():
                _ingest_locked(db, o, p, now=now)
            result = "processed"
            changed.add(d.wisp_site_id)
        except (ValueError, LookupError):
            invalid += 1
            result = "invalid_association"
        db.add(TowerReceipt(sample_id=row.id, result=result))
    db.flush()
    if changed and assess_changed:
        assess(db, site_ids=sorted(changed), now=now, persist=True)
    from app.down_detector.engine import reconcile

    if changed and assess_changed:
        reconcile(db, now=now)
    return {
        "processed": len(rows),
        "invalid": invalid,
        "sites_assessed": len(changed) if assess_changed else 0,
        "changed_site_ids": sorted(changed),
        "external_calls": 0,
    }


def tick():
    if not settings.tower_health_enabled:
        return {"enabled": False}
    total = 0
    sites = set()
    with SessionLocal() as db:
        for _ in range(5):
            result = consume(db, assess_changed=False)
            total += result["processed"]
            sites.update(result["changed_site_ids"])
            if result["processed"] < 200:
                break
        if sites:
            assess(db, site_ids=sorted(sites), persist=True)
            from app.down_detector.engine import reconcile

            reconcile(db)
        from .service import prune

        retention = prune(db, limit=500)
        db.commit()
    return {
        "processed": total,
        "sites_assessed": len(sites),
        "retention": retention,
        "external_calls": 0,
    }
