"""Consecutive/hysteretic RF evidence from stored typed samples. No remediation."""

from collections import defaultdict
from datetime import timedelta
from statistics import median
from sqlalchemy import select
from app.models.entities import TelemetrySample, MonitoringAlert, AuditLog
from app.services.monitoring_collectors import (
    RadioObservation,
    MicrowaveObservation,
    NormalizedRFTelemetry,
    RouterSnapshot,
    MODULATIONS,
)
from app.services.rf_analytics_queries import utc


def vectors(row):
    raw = row.raw_data or {}
    at = utc(row.sampled_at)
    out = []

    def item(series, metrics, *, state=None, context=None, link_id=None, ethernet=None):
        out.append(
            {
                "series": series,
                "at": at,
                "source_id": row.id,
                "metrics": metrics,
                "radio_status": state,
                "context": context,
                "link_id": link_id,
                "ethernet_status": ethernet,
            }
        )

    try:
        if "rf_radio_v2" in raw:
            o = RadioObservation.model_validate(raw["rf_radio_v2"])
            m = o.measurements
            if o.observed_at != at:
                return []
            item(
                "radio",
                {
                    "signal": m.rssi_dbm,
                    "snr": m.snr_db,
                    "noise": m.noise_floor_dbm,
                    "capacity": m.link_capacity_mbps,
                    "modulation": MODULATIONS.get(o.modulation),
                    "utilization": m.utilization_percent,
                    "temperature": m.temperature_c,
                    "uptime": m.uptime_seconds,
                },
                state=o.radio_status,
                context=(o.mapping_digest, m.frequency_mhz, m.channel_width_mhz),
            )
        elif "rf_microwave_v1" in raw:
            o = MicrowaveObservation.model_validate(raw["rf_microwave_v1"])
            link = raw.get("microwave_link_id")
            if (
                o.observed_at != at
                or not isinstance(link, int)
                or isinstance(link, bool)
            ):
                return []
            for key, c in o.carriers.items():
                m = c.measurements
                item(
                    "microwave:" + key,
                    {
                        "signal": m.local_rsl_dbm,
                        "snr": m.snr_db,
                        "capacity": m.capacity_mbps,
                        "modulation": MODULATIONS.get(c.modulation),
                        "utilization": m.utilization_percent,
                        "temperature": m.temperature_c,
                        "uptime": o.measurements.uptime_seconds,
                    },
                    state=c.radio_status,
                    context=(o.mapping_digest, link, m.frequency_mhz, m.bandwidth_mhz),
                    link_id=link,
                )
            states = [c.radio_status for c in o.carriers.values()]
            item(
                "microwave",
                {},
                state=(
                    "down"
                    if states and all(s == "down" for s in states)
                    else "up" if states and all(s == "up" for s in states) else None
                ),
                ethernet=o.ethernet_status,
                context=(o.mapping_digest, link),
                link_id=link,
            )
        elif "rf_observation_v1" in raw:
            o = NormalizedRFTelemetry.model_validate(raw["rf_observation_v1"])
            m = o.measurements
            if o.observed_at != at:
                return []
            item(
                "radio",
                {
                    "signal": m.rssi_dbm,
                    "snr": m.snr_db,
                    "noise": m.noise_floor_dbm,
                    "capacity": m.link_capacity_mbps,
                    "temperature": m.temperature_c,
                    "uptime": m.uptime_seconds,
                },
                state=o.radio_status,
                context=(o.profile, m.frequency_mhz, m.channel_width_mhz),
            )
        elif "mikrotik_v1" in raw:
            # Source strings/identity/addresses are deliberately not diagnostic evidence.
            value = raw["mikrotik_v1"]
            temp = value.get("temperature_c")
            temp = (
                temp
                if isinstance(temp, (int, float))
                and not isinstance(temp, bool)
                and -100 <= temp <= 200
                else None
            )
            interfaces = value.get("interfaces", [])
            states = [
                i.get("running")
                for i in interfaces
                if isinstance(i, dict) and i.get("disabled") is False
            ]
            item(
                "network",
                {
                    "temperature": temp,
                    "uptime": (
                        value.get("uptime_seconds")
                        if isinstance(value.get("uptime_seconds"), (int, float))
                        and not isinstance(value.get("uptime_seconds"), bool)
                        and 0 <= value["uptime_seconds"] <= 315360000
                        else None
                    ),
                },
                ethernet=(
                    "down"
                    if states and any(s is False for s in states)
                    else "up" if states and all(s is True for s in states) else None
                ),
                context=("mikrotik-read-only",),
            )
    except (ValueError, KeyError, TypeError):
        return []
    return out


def rules(points, now, policy):
    specifications = {
        "low_signal": (
            "signal",
            lambda v: v < policy.signal_enter,
            lambda v: v >= policy.signal_clear,
            "rf",
            "warning",
        ),
        "low_snr": (
            "snr",
            lambda v: v < policy.snr_enter,
            lambda v: v >= policy.snr_clear,
            "rf",
            "warning",
        ),
        "low_capacity": (
            "capacity",
            lambda v: v < policy.capacity_enter,
            lambda v: v >= policy.capacity_clear,
            "rf",
            "warning",
        ),
        "high_utilization": (
            "utilization",
            lambda v: v >= policy.utilization_enter,
            lambda v: v <= policy.utilization_clear,
            "rf",
            "warning",
        ),
        "overheating": (
            "temperature",
            lambda v: v > policy.temperature_enter,
            lambda v: v <= policy.temperature_clear,
            "hardware",
            "critical",
        ),
        "wireless_outage": (
            "radio_status",
            lambda v: v == "down",
            lambda v: v == "up",
            "rf",
            "critical",
        ),
        "ethernet_failure": (
            "ethernet_status",
            lambda v: v == "down",
            lambda v: v == "up",
            "ethernet",
            "critical",
        ),
        "rising_noise": (
            "noise_change",
            lambda v: v >= policy.noise_rise_db,
            lambda v: v < policy.noise_rise_db / 2,
            "rf",
            "warning",
        ),
        "modulation_fallback": (
            "modulation_change",
            lambda v: v <= -policy.modulation_drop_bits,
            lambda v: v > -policy.modulation_drop_bits / 2,
            "rf",
            "warning",
        ),
    }
    result = {}
    previous = None
    reference = []
    for point in points:
        reset = previous and (
            point["context"] != previous["context"]
            or (point["at"] - previous["at"]).total_seconds() > policy.max_gap_seconds
            or point["metrics"].get("uptime") is not None
            and previous["metrics"].get("uptime") is not None
            and point["metrics"]["uptime"] < previous["metrics"]["uptime"]
        )
        if reset:
            result = {}
            reference = []
        fresh = 0 <= (now - point["at"]).total_seconds() <= policy.stale_seconds
        for name, (field, enter, clear, category, severity) in specifications.items():
            item = result.setdefault(
                name,
                {
                    "state": "unknown",
                    "enter": 0,
                    "clear": 0,
                    "category": category,
                    "severity": severity,
                },
            )
            value = (
                point.get(field)
                if field in {"radio_status", "ethernet_status"}
                else point["metrics"].get(field)
            )
            if field in {"noise_change", "modulation_change"}:
                metric = "noise" if field == "noise_change" else "modulation"
                # A disjoint preceding four-sample reference, never a guessed vendor baseline.
                prior = [
                    p["metrics"].get(metric)
                    for p in reference[:4]
                    if p["metrics"].get(metric) is not None
                ]
                measured = point["metrics"].get(metric)
                value = (
                    measured - median(prior)
                    if measured is not None and len(prior) >= 4
                    else None
                )
            if not fresh or value is None:
                item.update(state="unknown", enter=0, clear=0)
            elif enter(value):
                item["enter"] += 1
                item["clear"] = 0
                if item["enter"] >= policy.consecutive:
                    item["state"] = "active"
            elif clear(value):
                item["clear"] += 1
                item["enter"] = 0
                if item["clear"] >= policy.clear_samples:
                    item["state"] = "clear"
            else:
                item["enter"] = item["clear"] = 0
            item["value"] = value
        reference.append(point)
        previous = point
    return result


def update_alert(
    db,
    device_id,
    series,
    rule,
    state,
    *,
    now,
    category="rf",
    severity="warning",
    evidence=None
):
    row = db.scalar(
        select(MonitoringAlert)
        .where(
            MonitoringAlert.device_id == device_id,
            MonitoringAlert.series == series,
            MonitoringAlert.rule == rule,
        )
        .with_for_update()
    )
    if row is None and state != "active":
        return
    if row is None:
        row = MonitoringAlert(
            device_id=device_id,
            series=series,
            rule=rule,
            state=state,
            category=category,
            severity=severity,
            first_seen_at=now,
            updated_at=now,
            evidence=evidence or {},
        )
        db.add(row)
        db.flush()
        event = "opened"
    else:
        old = row.state
        event = None
        if state == "clear":
            state = "cleared"
        if state == "active" and row.cleared_at:
            row.episode += 1
            row.first_seen_at = now
            row.cleared_at = None
            event = "recurred"
        elif state == "cleared" and old != "cleared":
            row.cleared_at = now
            event = "cleared"
        elif old != state:
            event = "evidence_changed"
        row.state = state
        row.updated_at = now
        row.evidence = evidence or {}
    if event:
        db.add(
            AuditLog(
                action="monitoring.alert_" + event,
                entity_type="monitoring_alert",
                entity_id=str(row.id),
                created_at=now,
                details={
                    "episode": row.episode,
                    "state": row.state,
                    "rule": rule,
                    "category": category,
                    "execution_enabled": False,
                },
            )
        )
    return event


def process(db, device_id, *, now, policy):
    rows = list(
        db.scalars(
            select(TelemetrySample)
            .where(
                TelemetrySample.device_id == device_id,
                TelemetrySample.sampled_at >= now - timedelta(days=1),
            )
            .order_by(TelemetrySample.sampled_at.desc(), TelemetrySample.id.desc())
            .limit(100)
        )
    )
    unique = {}
    for row in reversed(rows):
        for vector in vectors(row):
            unique[(vector["series"], vector["at"])] = vector
    series = defaultdict(list)
    for (_, at), point in sorted(unique.items(), key=lambda kv: kv[0][1]):
        series[point["series"]].append(point)
    seen = set()
    for key, points in series.items():
        latest = points[-1]
        for name, state in rules(points, now, policy).items():
            if key.startswith("microwave:") and name == "wireless_outage":
                name = "carrier_failure"
                state["severity"] = "warning"
            seen.add((key, name))
            update_alert(
                db,
                device_id,
                key,
                name,
                state["state"],
                now=now,
                category=state["category"],
                severity=state["severity"],
                evidence={
                    "source_table": "telemetry_samples",
                    "latest_sample_id": latest["source_id"],
                    "observed_at": latest["at"].isoformat(),
                    "link_id": latest["link_id"],
                    "processing_delay_ms": (
                        max(0, (now - latest["at"]).total_seconds() * 1000)
                        if latest["at"] <= now
                        else None
                    ),
                    "value": state["value"],
                    "consecutive_enter": state["enter"],
                    "consecutive_clear": state["clear"],
                    "root_cause": "unconfirmed",
                    "freshness": (
                        "fresh"
                        if 0
                        <= (now - latest["at"]).total_seconds()
                        <= policy.stale_seconds
                        else "stale_or_future"
                    ),
                },
            )
    for row in db.scalars(
        select(MonitoringAlert).where(
            MonitoringAlert.device_id == device_id,
            MonitoringAlert.category != "monitoring",
        )
    ):
        if (row.series, row.rule) not in seen and row.state != "cleared":
            update_alert(
                db,
                device_id,
                row.series,
                row.rule,
                "unknown",
                now=now,
                category=row.category,
                severity=row.severity,
                evidence={
                    "reason": "missing_or_invalid_data",
                    "root_cause": "unconfirmed",
                },
            )
