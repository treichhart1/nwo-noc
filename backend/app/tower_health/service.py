"""PostgreSQL health history and local maintenance records. No network equipment IO."""

import hashlib, json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, text, delete, or_, any_, bindparam, Integer
from sqlalchemy.dialects.postgresql import ARRAY
from app.core.config import settings
from app.models.entities import Site, Device, Sector, NetworkLink, AuditLog
from app.customers.models import CustomerAccount, CustomerService, CustomerSyncState
from app.customers.impact import reachable
from .contracts import Observation, Policy, Power
from .models import (
    TowerEvent,
    TowerSample,
    TowerDaily,
    TowerCurrent,
    TowerHistory,
    TowerFinding,
    TowerNote,
    TowerPolicyRow,
)
from .analytics import (
    current,
    flat,
    capacity,
    power,
    rule_decisions,
    daily_update,
    forecast,
)


def clock():
    return datetime.now(timezone.utc)


def lock(db):
    db.execute(text("SET LOCAL statement_timeout='10000ms'"))
    db.execute(text("SELECT pg_advisory_xact_lock(980098)"))


def policy(db):
    r = db.get(TowerPolicyRow, 1)
    return Policy.model_validate(r.config if r else settings.tower_health_policy)


def device_policy(p, ident):
    v = p.device_voltage_thresholds.get(ident)
    return p.model_copy(update=v.model_dump() if v else {})


def audit(db, action, user=None, finding=None, **data):
    db.add(
        AuditLog(
            user_id=user,
            action="tower_health." + action,
            entity_type="tower_finding",
            entity_id=str(finding.id) if finding else None,
            details=data,
        )
    )


def scope(o):
    return (
        f"link:{o.link_id}"
        if o.link_id
        else f"sector:{o.sector_id}" if o.sector_id else f"device:{o.device_id}"
    )


def ingest(db, o: Observation, *, now=None, emit_power=True):
    now = now or clock()
    p = policy(db)
    lock(db)
    if not now - timedelta(days=p.aggregate_retention_days) <= o.observed_at <= now:
        raise ValueError("Observation time outside retained history or in the future")
    device = db.get(Device, o.device_id)
    if not device or device.wisp_site_id != o.site_id or not db.get(Site, o.site_id):
        raise ValueError("Device and tower must match registered inventory")
    if o.sector_id:
        s = db.get(Sector, o.sector_id)
        if not s or s.site_id != o.site_id or s.device_id != o.device_id:
            raise ValueError(
                "Sector association must match its registered device and tower"
            )
    if o.link_id:
        l = db.get(NetworkLink, o.link_id)
        if not l or not (
            (o.device_id == l.a_device_id and o.site_id == l.a_site_id)
            or (o.device_id == l.z_device_id and o.site_id == l.z_site_id)
        ):
            raise ValueError("Backhaul requires a registered endpoint device and site")
    data = o.model_dump(mode="json", exclude_unset=True)
    data["metrics"] = o.metrics.model_dump(exclude_unset=True)
    data["power"] = o.power.model_dump(exclude_unset=True)
    data["unavailable"] = o.unavailable
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prior = db.get(TowerEvent, o.event_id)
    if prior:
        if prior.body_hash != digest:
            raise ValueError("Conflicting tower event identifier")
        return {"duplicate": True, "event_id": o.event_id}
    db.add(TowerEvent(event_id=o.event_id, body_hash=digest, observed_at=o.observed_at))
    db.flush()
    row = TowerSample(
        event_id=o.event_id,
        device_id=o.device_id,
        site_id=o.site_id,
        sector_id=o.sector_id,
        link_id=o.link_id,
        scope_key=scope(o),
        source=o.source,
        observed_at=o.observed_at,
        data=data,
    )
    db.add(row)
    db.flush()
    # Preserve same-time conflicts, but never count the same observation time twice in peaks/coverage.
    other = db.scalar(
        select(TowerSample)
        .where(
            TowerSample.device_id == o.device_id,
            TowerSample.site_id == o.site_id,
            TowerSample.scope_key == row.scope_key,
            TowerSample.observed_at == o.observed_at,
            TowerSample.id != row.id,
        )
        .limit(1)
    )
    ident = {
        "site_id": o.site_id,
        "scope_key": row.scope_key,
        "day": o.observed_at.date(),
    }
    daily = db.get(TowerDaily, ident)
    if not daily:
        daily = TowerDaily(**ident, samples=0, values={})
        db.add(daily)
    if not other:
        daily.samples += 1
    daily.values = daily_update(daily.values, o, p)
    if other:
        prior_values = flat(other)
        new_values = flat(row)
        if any(
            prior_values[k] != new_values[k]
            for k in prior_values.keys() & new_values.keys()
        ):
            daily.values = {**daily.values, "conflicting_observation_times": True}
    if (
        emit_power
        and ("ac_present" in o.power.model_fields_set or "ac_present" in o.unavailable)
        and now - o.observed_at <= timedelta(seconds=p.max_age_seconds)
    ):
        from app.down_detector.contracts import Observation as DownObservation
        from app.down_detector.engine import (
            ingest as down_ingest,
            policy as down_policy,
        )

        if now - o.observed_at <= timedelta(seconds=down_policy().stale_seconds):
            signals = {
                "commercial_power": o.power.ac_present,
                "collector": o.metrics.collector_ok,
                "power_supply": o.power.supply_ok,
            }
            if o.power.ups_state in {"online", "on_battery"}:
                signals["ups"] = True
            elif o.power.ups_state in {"fault", "low_battery"}:
                signals["ups"] = False
            effective = device_policy(p, o.device_id)
            if (
                o.power.battery_voltage is not None
                and effective.low_battery_voltage is not None
            ):
                if o.power.battery_voltage < effective.low_battery_voltage:
                    signals["battery"] = False
                elif (
                    o.power.battery_voltage
                    >= effective.low_battery_voltage + effective.voltage_hysteresis
                ):
                    signals["battery"] = True
            down_ingest(
                db,
                DownObservation(
                    event_id=f"tower-power:{row.id}",
                    kind="site",
                    target_id=o.site_id,
                    source="monitoring",
                    observed_at=o.observed_at,
                    signals=signals,
                    voltage=o.power.dc_voltage,
                ),
                now=now,
            )
    return {
        "sample_id": row.id,
        "scope_key": row.scope_key,
        "duplicate": bool(other),
        "event_id": o.event_id,
    }


def inventory(db, site_ids=None):
    sq = select(Site)
    if site_ids is not None:
        sq = sq.where(Site.id.in_(site_ids))
    sites = list(db.scalars(sq.order_by(Site.id).limit(501)))
    if len(sites) > 500:
        raise ValueError("Tower inventory exceeds500-site query bound")
    ids = [s.id for s in sites]
    devices = list(
        db.scalars(
            select(Device)
            .where(Device.enabled.is_(True), Device.wisp_site_id.in_(ids))
            .order_by(Device.id)
            .limit(2001)
        )
    )
    if len(devices) > 2000:
        raise ValueError("Narrow tower scope; validated device bound is2000")
    sectors = list(
        db.scalars(select(Sector).where(Sector.site_id.in_(ids)).limit(5001))
    )
    links = list(db.scalars(select(NetworkLink).limit(5001)))
    if len(sectors) > 5000 or len(links) > 5000:
        raise ValueError("Topology exceeds bounded assessment scope")
    return sites, devices, sectors, links


def customer_data(db, now, p):
    rows = list(
        db.execute(
            select(CustomerService, CustomerAccount)
            .join(CustomerAccount, CustomerAccount.id == CustomerService.customer_id)
            .where(
                CustomerService.status == "active",
                CustomerService.source_deleted_at.is_(None),
                CustomerAccount.source_deleted_at.is_(None),
            )
            .limit(20001)
        )
    )
    if len(rows) > 20000:
        raise ValueError("Active customer services exceed validated20000-service scope")
    devices = {d.id: d for d in db.scalars(select(Device).limit(10001))}
    sectors = {s.id: s for s in db.scalars(select(Sector).limit(10001))}
    valid = []
    unknown = 0
    for service, account in rows:
        d = devices.get(service.device_id)
        s = sectors.get(service.sector_id)
        okay = (
            service.mapping_state == "verified"
            and service.site_id is not None
            and timedelta(0)
            <= now - service.last_synced_at
            <= timedelta(hours=p.customer_max_age_hours)
            and timedelta(0)
            <= now - account.last_synced_at
            <= timedelta(hours=p.customer_max_age_hours)
        )
        if service.device_id and (not d or d.wisp_site_id != service.site_id):
            okay = False
        if service.sector_id and (
            not s
            or s.site_id != service.site_id
            or s.device_id
            and s.device_id != service.device_id
        ):
            okay = False
        if okay:
            valid.append(service)
        else:
            unknown += 1
    return valid, unknown


def customers(services):
    down = [s.download_mbps for s in services if s.download_mbps is not None]
    up = [s.upload_mbps for s in services if s.upload_mbps is not None]
    plans = defaultdict(int)
    for s in services:
        plans[s.plan or "unavailable"] += 1
    return {
        "known_active_customers": len({s.customer_id for s in services}),
        "known_active_services": len(services),
        "plan_distribution_by_service": dict(sorted(plans.items())),
        "known_provisioned_down_mbps": sum(down) if down else None,
        "known_provisioned_up_mbps": sum(up) if up else None,
        "down_plan_coverage": len(down),
        "up_plan_coverage": len(up),
        "provisioned_is_measured_demand": False,
        "basis": "Current reviewed, fresh NOC customer assignments; not complete source enrollment",
    }


CAP_FIELDS = {
    "tx_mbps",
    "rx_mbps",
    "tx_capacity_mbps",
    "rx_capacity_mbps",
    "reported_capacity_mbps",
    "subscriber_down_capacity_mbps",
    "utilization_percent",
    "airtime_percent",
}


def paired_values(rows, states, now, p, keys):
    candidates = [
        r
        for r in rows
        if keys & (flat(r).keys() | r.data.get("unavailable", {}).keys())
    ]
    if not candidates:
        return {}
    last = max(candidates, key=lambda r: (r.observed_at, r.id))
    if now - last.observed_at > timedelta(seconds=p.max_age_seconds):
        return {}
    v = flat(last)
    return {
        k: value
        for k, value in v.items()
        if k in keys
        and states[k]["state"] == "available"
        and states[k]["observed_at"] == last.observed_at.isoformat()
    }


def paired_capacity(rows, states, now, p):
    return capacity(paired_values(rows, states, now, p, CAP_FIELDS))


def paired_power(rows, states, now, p):
    return power(paired_values(rows, states, now, p, set(Power.model_fields)))


def history_rows(db, device_ids, now, limit=24):
    ranked = (
        select(
            TowerSample.id.label("sample_id"),
            func.row_number()
            .over(
                partition_by=(TowerSample.device_id, TowerSample.scope_key),
                order_by=(TowerSample.observed_at.desc(), TowerSample.id.desc()),
            )
            .label("rn"),
            func.row_number()
            .over(
                partition_by=(
                    TowerSample.device_id,
                    TowerSample.scope_key,
                    func.date_trunc("hour", TowerSample.observed_at),
                ),
                order_by=(TowerSample.observed_at.desc(), TowerSample.id.desc()),
            )
            .label("hour_rn"),
        )
        .join(Device, Device.id == TowerSample.device_id)
        .where(
            TowerSample.device_id
            == any_(
                bindparam("tower_device_ids", value=device_ids, type_=ARRAY(Integer))
            ),
            TowerSample.site_id == Device.wisp_site_id,
            TowerSample.observed_at >= now - timedelta(hours=24),
            TowerSample.observed_at <= now,
        )
        .subquery()
    )
    rows = list(
        db.scalars(
            select(TowerSample)
            .join(ranked, ranked.c.sample_id == TowerSample.id)
            .where(or_(ranked.c.rn <= limit, ranked.c.hour_rn == 1))
            .order_by(TowerSample.observed_at, TowerSample.id)
            .limit(50001)
        )
    )
    if len(rows) > 50000:
        raise ValueError(
            "Narrow health scope; maximum50000 recent observations per assessment"
        )
    return rows


EQUIPMENT_RULES = {
    "temperature_high",
    "temperature_critical",
    "cpu_pressure",
    "memory_pressure",
    "management_unreachable",
    "collector_failure",
    "interface_down",
    "commercial_power_lost",
    "ups_warning",
    "supply_warning",
    "low_dc_voltage",
    "low_battery_voltage",
    "battery_deterioration_indicator",
    "repeated_uptime_resets",
    "power_instability",
    "rising_temperature",
    "increasing_interface_errors",
}


def assess(db, *, site_ids=None, now=None, persist=False):
    now = now or clock()
    p = policy(db)
    if persist:
        lock(db)
    sites, devices, sectors, links = inventory(db, site_ids)
    services, unknown_customers = customer_data(db, now, p)
    rows = history_rows(db, [d.id for d in devices], now)
    by_device = defaultdict(list)
    by_scope = defaultdict(list)
    for r in rows:
        by_device[r.device_id].append(r)
        by_scope[(r.device_id, r.scope_key)].append(r)
    roots = set(db.scalars(select(Site.id).where(Site.site_type == "core-pop")))
    before = reachable(roots, links)
    exposure = {}
    for link in links:
        after = reachable(roots, links, excluded_links={link.id})
        possible = reachable(roots, links, excluded_links={link.id}, allow_unknown=True)
        exposure[link.id] = {
            "potential_customers": len(
                {
                    s.customer_id
                    for s in services
                    if s.fiber_link_id == link.id
                    or s.site_id in before
                    and s.site_id not in after
                    and s.site_id not in possible
                }
            ),
            "uncertain_path_services": sum(
                s.site_id in before and s.site_id not in after and s.site_id in possible
                for s in services
            ),
            "basis": "Conditional recorded dependency exposure; surviving recorded alternate paths excluded, not confirmed customer impact",
        }
    decisions = []
    equipment = {}
    resources = {}
    for d in devices:
        rs = by_device[d.id]
        effective = device_policy(p, d.id)
        values = current(rs, now, effective)
        v = {k: x["value"] for k, x in values.items()}
        known = [
            k
            for k, x in values.items()
            if x["state"] == "available"
            and k
            in {
                "reachable",
                "rf_ok",
                "cpu_percent",
                "memory_percent",
                "temperature_c",
                "ac_present",
                "dc_voltage",
                "battery_voltage",
                "ups_state",
                "supply_ok",
                "utilization_percent",
                "airtime_percent",
                "rssi_dbm",
                "snr_db",
            }
        ]
        known = [k for k in known if v.get(k) != "unknown"]
        fresh = bool(known) and v.get("collector_ok") is not False
        equipment[d.id] = {
            "device_id": d.id,
            "site_id": d.wisp_site_id,
            "name": d.name,
            "vendor": d.vendor,
            "metric_states": values,
            "fresh_usable": fresh,
            "usable_until": (
                (
                    max(datetime.fromisoformat(values[k]["observed_at"]) for k in known)
                    + timedelta(seconds=p.max_age_seconds)
                ).isoformat()
                if fresh
                else None
            ),
            "last_successful_monitoring_at": max(
                (r.observed_at for r in rs if flat(r).get("collector_ok") is True),
                default=None,
            ),
            "power": {
                **paired_power(rs, values, now, p),
                **{
                    k: v.get(k)
                    for k in [
                        "ac_present",
                        "dc_voltage",
                        "battery_voltage",
                        "ups_state",
                        "charge_state",
                    ]
                },
            },
            "voltage_thresholds": {
                "low_dc_voltage": effective.low_dc_voltage,
                "low_battery_voltage": effective.low_battery_voltage,
                "basis": (
                    "explicit_configuration"
                    if effective.low_dc_voltage is not None
                    or effective.low_battery_voltage is not None
                    else "unconfigured"
                ),
            },
        }
        for rule, result in rule_decisions(rs, now, effective).items():
            if rule in EQUIPMENT_RULES:
                decisions.append((d, f"device:{d.id}", rule, result))
    for (device_id, key), rs in by_scope.items():
        d = next(d for d in devices if d.id == device_id)
        values = current(rs, now, p)
        v = {k: x["value"] for k, x in values.items()}
        last = rs[-1]
        scoped = last.sector_id or last.link_id
        cap = paired_capacity(rs, values, now, p)
        mapped = (
            [s for s in services if s.sector_id == last.sector_id]
            if last.sector_id
            else []
        )
        provisioned = customers(mapped) if last.sector_id else None
        ratio = (
            provisioned["known_provisioned_down_mbps"]
            / cap["subscriber_down_capacity_mbps"]
            if provisioned
            and provisioned["known_provisioned_down_mbps"] is not None
            and cap["subscriber_down_capacity_mbps"]
            else None
        )
        resources[(device_id, key)] = {
            "scope_key": key,
            "device_id": device_id,
            "site_id": d.wisp_site_id,
            "sector_id": last.sector_id,
            "link_id": last.link_id,
            "capacity": cap,
            "configured_reference_capacity_mbps": next(
                (l.capacity_mbps for l in links if l.id == last.link_id), None
            ),
            "metric_states": {
                k: values[k]
                for k in [
                    "rssi_dbm",
                    "snr_db",
                    "subscribers",
                    "airtime_percent",
                    "packet_loss_percent",
                    "latency_ms",
                    "modulation_bits",
                ]
            },
            "customers": provisioned,
            "known_provisioned_down_to_reported_subscriber_capacity_ratio": ratio,
            "ratio_is_actual_congestion": False,
            "exposure": exposure.get(last.link_id) if last.link_id else None,
        }
        for rule, result in rule_decisions(
            rs, now, device_policy(p, device_id)
        ).items():
            if rule not in EQUIPMENT_RULES:
                decisions.append((d, key, rule, result))
        if ratio is not None and ratio >= p.provisioned_ratio_warning:
            decisions.append(
                (
                    d,
                    key,
                    "provisioned_capacity_review",
                    {
                        "state": "warning",
                        "priority": "medium",
                        "confidence": "provisioning_indicator",
                        "action": "Review provisioned plans against explicit subscriber downlink capacity; this does not establish measured overload",
                        "ratio": ratio,
                        "sample_events": [last.event_id],
                        "observed_at": last.observed_at.isoformat(),
                    },
                )
            )
    existing = list(
        db.scalars(
            select(TowerFinding).where(TowerFinding.site_id.in_([s.id for s in sites]))
        )
    )
    findings = {(f.site_id, f.device_id, f.scope_key, f.rule): f for f in existing}
    for d, key, rule, result in decisions:
        f = findings.get((d.wisp_site_id, d.id, key, rule))
        if result["state"] == "warning":
            if persist:
                evidence = {k: v for k, v in result.items() if k != "state"}
                if not f:
                    f = TowerFinding(
                        site_id=d.wisp_site_id,
                        device_id=d.id,
                        scope_key=key,
                        rule=rule,
                        priority=result["priority"],
                        confidence=result["confidence"],
                        first_seen=now,
                        last_seen=now,
                        evidence=evidence,
                    )
                    db.add(f)
                    db.flush()
                    existing.append(f)
                    findings[(d.wisp_site_id, d.id, key, rule)] = f
                    audit(
                        db,
                        "finding_opened",
                        finding=f,
                        rule=rule,
                        site_id=d.wisp_site_id,
                    )
                elif f.state in {"cleared", "closed_by_operator"} and f.evidence.get(
                    "observed_at"
                ) != evidence.get("observed_at"):
                    previous_incident = f.incident_id
                    f.incident_id = None
                    f.state = "active"
                    f.cleared_at = None
                    f.first_seen = now
                    audit(
                        db,
                        "finding_reopened",
                        finding=f,
                        rule=rule,
                        previous_incident_id=previous_incident,
                    )
                if f.state != "closed_by_operator":
                    f.last_seen = now
                    f.priority = result["priority"]
                    f.confidence = result["confidence"]
                    f.evidence = evidence
        elif (
            result["state"] == "normal"
            and f
            and persist
            and f.state in {"active", "acknowledged"}
        ):
            f.state = "cleared"
            f.cleared_at = now
            f.last_seen = now
            if f.incident_id:
                from app.models.entities import Incident

                incident = db.get(Incident, f.incident_id)
                if incident and incident.status not in {"resolved", "closed"}:
                    incident.status = "resolved"
                    incident.resolved_at = datetime.fromisoformat(result["observed_at"])
                    incident.revision += 1
            audit(
                db,
                "finding_cleared",
                finding=f,
                rule=rule,
                observed_at=result.get("observed_at"),
            )
    reports = []
    customer_source = db.get(CustomerSyncState, "powercode")
    source_ready = bool(
        customer_source
        and customer_source.status == "healthy"
        and customer_source.last_success_at
        and timedelta(0)
        <= now - customer_source.last_success_at
        <= timedelta(hours=p.customer_max_age_hours)
    )
    from app.down_detector.models import OutageState
    from app.down_detector.engine import policy as down_policy

    outages = list(
        db.scalars(
            select(OutageState).where(
                OutageState.state == "down",
                OutageState.last_seen
                >= now
                - timedelta(
                    seconds=min(p.max_age_seconds, down_policy().stale_seconds)
                ),
            )
        )
    )
    for site in sites:
        ds = [d for d in devices if d.wisp_site_id == site.id]
        active = [
            f
            for f in existing
            if f.site_id == site.id and f.state in {"active", "acknowledged"}
        ]
        fresh = sum(equipment[d.id]["fresh_usable"] for d in ds)
        covered = bool(ds) and fresh == len(ds)
        pending = sum(
            bool(result.get("pending_warning"))
            for d, key, rule, result in decisions
            if d.wisp_site_id == site.id
        )
        known_down = any(s.site_id == site.id and s.kind == "site" for s in outages)
        state = (
            "critical"
            if known_down or any(f.priority == "critical" for f in active)
            else (
                "warning"
                if active
                else (
                    "pending_confirmation"
                    if pending
                    else "observed_normal" if covered else "unknown"
                )
            )
        )
        penalties = {}
        for f in active:
            family = (
                "temperature"
                if f.rule
                in {"temperature_high", "temperature_critical", "rising_temperature"}
                else (
                    "rf_quality"
                    if f.rule in {"rf_degraded", "low_rssi", "low_snr"}
                    else f.rule
                )
            )
            key = (f.device_id, family)
            weight = (
                35 if f.priority == "critical" else 15 if f.priority == "high" else 5
            )
            penalties[key] = max(penalties.get(key, 0), weight)
        score = (
            max(0, 100 - sum(penalties.values()) - (35 if known_down else 0))
            if covered and not pending
            else None
        )
        own = [s for s in services if s.site_id == site.id]
        report = {
            "site_id": site.id,
            "name": site.name,
            "assessed_at": now.isoformat(),
            "state": state,
            "known_metric_score": score,
            "score_basis": "Heuristic penalties on observed checks only; not an availability percentage or proof of unsupported metrics",
            "coverage": {
                "registered_enabled_devices": len(ds),
                "fresh_usable_devices": fresh,
                "unknown_or_stale_devices": len(ds) - fresh,
            },
            "customers": customers(own),
            "customer_source_ready": source_ready,
            "unassigned_or_unverified_active_services_fleet": unknown_customers,
            "equipment": [equipment[d.id] for d in ds],
            "resources": [r for r in resources.values() if r["site_id"] == site.id],
            "registered_sector_ids": [s.id for s in sectors if s.site_id == site.id],
            "backhaul_link_ids": [
                l.id for l in links if site.id in {l.a_site_id, l.z_site_id}
            ],
            "findings": [finding_view(f, now, p) for f in active],
            "pending_confirmation_count": pending,
            "network_outage_confirmed": known_down,
            "data_expires_at": min(
                (
                    equipment[d.id]["usable_until"]
                    for d in ds
                    if equipment[d.id]["usable_until"]
                ),
                default=None,
            ),
            "automatic_actions_enabled": False,
            "external_delivery_enabled": False,
        }
        # Serialize datetime values once; no customer contacts enter a health snapshot.
        report = json.loads(json.dumps(report, default=lambda v: v.isoformat()))
        reports.append(report)
        if persist:
            cur = db.get(TowerCurrent, site.id)
            if not cur:
                cur = TowerCurrent(site_id=site.id, assessed_at=now, report=report)
                db.add(cur)
            else:
                cur.assessed_at = now
                cur.report = report
            bucket = now.replace(minute=0, second=0, microsecond=0)
            h = db.get(TowerHistory, {"site_id": site.id, "bucket": bucket})
            compact = {
                "unknown_active_services": unknown_customers,
                "customer_source_ready": source_ready,
                "customers": report["customers"],
                "sector_customers": {
                    r["scope_key"]: r["customers"]
                    for r in report["resources"]
                    if r["customers"] is not None
                },
                "state": state,
                "coverage": report["coverage"],
            }
            if not h:
                db.add(TowerHistory(site_id=site.id, bucket=bucket, report=compact))
            else:
                h.report = compact
    if persist:
        db.flush()
    return reports


def finding_view(f, now, p):
    return {
        "id": f.id,
        "site_id": f.site_id,
        "device_id": f.device_id,
        "scope_key": f.scope_key,
        "rule": f.rule,
        "state": f.state,
        "priority": f.priority,
        "confidence": f.confidence,
        "first_seen": f.first_seen.isoformat(),
        "last_seen": f.last_seen.isoformat(),
        "evidence": f.evidence,
        "incident_id": f.incident_id,
        "current_verification": (
            "stale_or_unverified"
            if not f.evidence.get("observed_at")
            or now - datetime.fromisoformat(f.evidence["observed_at"])
            > timedelta(seconds=p.max_age_seconds)
            else "recent_observation"
        ),
    }


def trend_report(db, site_id, key, days, *, now=None):
    now = now or clock()
    p = policy(db)
    rows = list(
        db.scalars(
            select(TowerDaily)
            .where(
                TowerDaily.site_id == site_id,
                TowerDaily.scope_key == key,
                TowerDaily.day >= now.date() - timedelta(days=days),
            )
            .order_by(TowerDaily.day)
            .limit(92)
        )
    )
    latest = db.scalar(
        select(TowerSample)
        .where(
            TowerSample.site_id == site_id,
            TowerSample.scope_key == key,
            TowerSample.observed_at <= now,
            TowerSample.observed_at >= now - timedelta(seconds=p.max_age_seconds),
        )
        .order_by(TowerSample.observed_at.desc(), TowerSample.id.desc())
        .limit(1)
    )
    recent = list(
        db.scalars(
            select(TowerSample)
            .where(
                TowerSample.site_id == site_id,
                TowerSample.scope_key == key,
                TowerSample.observed_at <= now,
                TowerSample.observed_at >= now - timedelta(seconds=p.max_age_seconds),
            )
            .order_by(TowerSample.observed_at.desc(), TowerSample.id.desc())
            .limit(96)
        )
    )
    recent.sort(key=lambda r: (r.observed_at, r.id))
    cap = paired_capacity(recent, current(recent, now, p), now, p).get(
        "tx_capacity_mbps"
    )
    weeks = {}
    for r in rows:
        k = r.day.isocalendar()
        w = f"{k.year}-W{k.week:02d}"
        v = weeks.setdefault(w, {})
        for field in ["tx_peak_mbps", "rx_peak_mbps", "util_peak_percent"]:
            if r.values.get(field) is not None:
                v[field] = max(v.get(field, r.values[field]), r.values[field])
    history = list(
        db.scalars(
            select(TowerHistory)
            .where(
                TowerHistory.site_id == site_id,
                TowerHistory.bucket >= now - timedelta(days=days),
            )
            .order_by(TowerHistory.bucket)
            .limit(2200)
        )
    )
    growth = None
    if (
        history
        and (history[-1].bucket - history[0].bucket).days >= days - 2
        and all(
            h.report.get("unknown_active_services", 1) == 0
            and h.report.get("customer_source_ready", False)
            for h in history
        )
    ):
        growth = {
            "observed_customer_delta": history[-1].report["customers"][
                "known_active_customers"
            ]
            - history[0].report["customers"]["known_active_customers"],
            "first_observed_at": history[0].bucket.isoformat(),
            "last_observed_at": history[-1].bucket.isoformat(),
            "basis": "Changes in recorded reviewed assignments, not inferred enrollment dates",
        }
    return {
        "site_id": site_id,
        "scope_key": key,
        "days": days,
        "daily": [
            {"day": r.day.isoformat(), "samples": r.samples, **r.values} for r in rows
        ],
        "weekly_observed_peaks": weeks,
        "forecast": forecast(rows, days, now, p, cap),
        "customer_growth": growth,
        "timezone": p.timezone,
        "history_is_complete_coverage": False,
    }


def prune(db, *, now=None, limit=5000):
    now = now or clock()
    p = policy(db)
    lock(db)
    if not 1 <= limit <= 10000:
        raise ValueError("Bounded retention batch required")
    result = {}
    for model, column, cutoff in [
        (
            TowerSample,
            TowerSample.observed_at,
            now - timedelta(days=p.raw_retention_days),
        ),
        (
            TowerHistory,
            TowerHistory.bucket,
            now - timedelta(days=p.aggregate_retention_days),
        ),
    ]:
        rows = list(db.scalars(select(model).where(column < cutoff).limit(limit)))
        for r in rows:
            db.delete(r)
        db.flush()
        result[model.__tablename__] = len(rows)
    old = list(
        db.scalars(
            select(TowerDaily)
            .where(
                TowerDaily.day
                < (now - timedelta(days=p.aggregate_retention_days)).date()
            )
            .limit(limit)
        )
    )
    for r in old:
        db.delete(r)
    # Event ledger remains until all retained history/raw rows are outside the accepted import window.
    db.flush()
    result["daily"] = len(old)
    events = list(
        db.scalars(
            select(TowerEvent)
            .where(
                TowerEvent.observed_at
                < now - timedelta(days=p.aggregate_retention_days),
                ~select(TowerSample.id)
                .where(TowerSample.event_id == TowerEvent.event_id)
                .exists(),
            )
            .limit(limit)
        )
    )
    for e in events:
        db.delete(e)
    result["event_keys"] = len(events)
    if any(result.values()):
        audit(db, "retention", counts=result)
    return result
