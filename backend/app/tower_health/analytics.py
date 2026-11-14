"""Measured indicators, cautious daily trends and explicit missing-data semantics."""

from datetime import timedelta
from statistics import mean, median
from math import isfinite
from zoneinfo import ZoneInfo
from .contracts import Metrics, Power

FIELDS = set(Metrics.model_fields) | set(Power.model_fields)
BOOLS = {
    "reachable",
    "collector_ok",
    "rf_ok",
    "interface_up",
    "ac_present",
    "supply_ok",
}


def flat(row):
    return {**row.data.get("metrics", {}), **row.data.get("power", {})}


def points(rows, key):
    values = {}
    for r in rows:
        v = flat(r)
        u = r.data.get("unavailable", {})
        if key not in v and key not in u:
            continue
        value = v.get(key)
        if r.observed_at in values and values[r.observed_at][0] != value:
            value = None
        values[r.observed_at] = (value, r.event_id, r.data.get("counter_scope"))
    return [(t, *v) for t, v in sorted(values.items())]


def current(rows, now, p):
    result = {}
    for key in sorted(FIELDS):
        applicable = [
            r for r in rows if key in flat(r) or key in r.data.get("unavailable", {})
        ]
        if not applicable:
            result[key] = {"value": None, "state": "missing", "observed_at": None}
            continue
        last = max(r.observed_at for r in applicable)
        last_rows = [r for r in applicable if r.observed_at == last]
        vals = [flat(r).get(key) for r in last_rows]
        state = (
            "available"
            if vals[0] is not None
            else last_rows[-1].data.get("unavailable", {}).get(key, "missing")
        )
        if any(v != vals[0] for v in vals):
            state = "invalid"
        if now - last > timedelta(seconds=p.max_age_seconds):
            state = "stale"
        result[key] = {
            "value": vals[0] if state == "available" else None,
            "state": state,
            "observed_at": last.isoformat(),
        }
    return result


def capacity(v):
    result = {
        "tx_mbps": v.get("tx_mbps"),
        "rx_mbps": v.get("rx_mbps"),
        "tx_capacity_mbps": v.get("tx_capacity_mbps"),
        "rx_capacity_mbps": v.get("rx_capacity_mbps"),
        "reported_capacity_mbps": v.get("reported_capacity_mbps"),
        "reported_utilization_percent": v.get("utilization_percent"),
        "capacity_basis": "reported_current_only",
        "pair_errors": [],
    }
    for direction in ["tx", "rx"]:
        demand = v.get(direction + "_mbps")
        cap = v.get(direction + "_capacity_mbps")
        value = None
        if demand is not None and cap is not None and cap > 0:
            ratio = demand / cap * 100
            if ratio <= 100:
                value = ratio
            else:
                result["pair_errors"].append(
                    direction + "_traffic_exceeds_current_capacity"
                )
        result[direction + "_utilization_percent"] = value
        result[direction + "_headroom_mbps"] = (
            max(0, cap - demand) if value is not None else None
        )
    known = [
        result[k]
        for k in [
            "tx_utilization_percent",
            "rx_utilization_percent",
            "reported_utilization_percent",
        ]
        if result[k] is not None
    ]
    result["observed_utilization_percent"] = max(known) if known else None
    result["airtime_percent"] = v.get("airtime_percent")
    result["subscriber_down_capacity_mbps"] = v.get("subscriber_down_capacity_mbps")
    return result


def power(v):
    runtime = v.get("reported_runtime_minutes")
    basis = "vendor_reported" if runtime is not None else "unavailable"
    if (
        runtime is None
        and v.get("charge_state") == "discharging"
        and v.get("remaining_energy_wh") is not None
        and v.get("load_watts") is not None
        and v["load_watts"] > 0
    ):
        runtime = v["remaining_energy_wh"] / v["load_watts"] * 60
        basis = "remaining_measured_energy_divided_by_measured_load"
        if not isfinite(runtime) or runtime > 1e6:
            runtime = None
            basis = "measurement_pair_outside_supported_estimation_range"
    ratio = None
    if (
        v.get("measured_full_capacity_wh") is not None
        and v.get("rated_capacity_wh") is not None
        and v["rated_capacity_wh"] > 0
    ):
        ratio = v["measured_full_capacity_wh"] / v["rated_capacity_wh"]
    if ratio is not None and not isfinite(ratio):
        ratio = None
    return {
        "ac_present": v.get("ac_present"),
        "dc_voltage": v.get("dc_voltage"),
        "battery_voltage": v.get("battery_voltage"),
        "ups_state": v.get("ups_state"),
        "charge_state": v.get("charge_state"),
        "runtime_minutes": runtime,
        "runtime_basis": basis,
        "runtime_is_guarantee": False,
        "measured_to_rated_full_capacity_ratio": ratio,
        "battery_ratio_requires_calibration": ratio is not None and ratio > 1.2,
    }


def rule_decisions(rows, now, p):
    """Per-rule warning/normal/unknown. Unknown never clears a persisted finding."""
    decisions = {}

    def rule(
        name,
        key,
        bad,
        normal,
        priority="medium",
        action="Inspect recorded evidence with a technician",
    ):
        ps = points(rows, key)
        tail = ps[-p.confirmation_samples :]
        valid = (
            len(tail) == p.confirmation_samples
            and all(v is not None for _, v, *_ in tail)
            and now - tail[-1][0] <= timedelta(seconds=p.max_age_seconds)
            and (tail[-1][0] - tail[0][0]).total_seconds() >= p.confirmation_seconds
            and all(
                (b[0] - a[0]).total_seconds() <= p.max_age_seconds
                for a, b in zip(tail, tail[1:])
            )
        )
        state = "unknown"
        if valid:
            if all(bad(v) for _, v, *_ in tail):
                state = "warning"
            elif all(normal(v) for _, v, *_ in tail):
                state = "normal"
        decisions[name] = {
            "state": state,
            "pending_warning": bool(
                state == "unknown"
                and tail
                and tail[-1][1] is not None
                and now - tail[-1][0] <= timedelta(seconds=p.max_age_seconds)
                and bad(tail[-1][1])
            ),
            "priority": priority,
            "confidence": "observed_sustained" if valid else "insufficient_evidence",
            "action": action,
            "metric": key,
            "sample_events": [e for _, _, e, *_ in tail],
            "values": [v for _, v, *_ in tail],
            "observed_at": tail[-1][0].isoformat() if tail else None,
        }

    rule(
        "temperature_high",
        "temperature_c",
        lambda v: v >= p.temperature_warning_c,
        lambda v: v < p.temperature_warning_c - p.temperature_hysteresis_c,
        "high",
        "Inspect cooling, airflow and measured temperature history",
    )
    rule(
        "temperature_critical",
        "temperature_c",
        lambda v: v >= p.temperature_critical_c,
        lambda v: v < p.temperature_critical_c - p.temperature_hysteresis_c,
        "critical",
        "Urgently investigate sustained temperature; do not reboot equipment automatically",
    )
    rule(
        "cpu_pressure",
        "cpu_percent",
        lambda v: v >= 90,
        lambda v: v < 80,
        "medium",
        "Inspect sustained CPU load and service impact",
    )
    rule(
        "memory_pressure",
        "memory_percent",
        lambda v: v >= 90,
        lambda v: v < 80,
        "medium",
        "Inspect sustained memory pressure and process history",
    )
    rule(
        "management_unreachable",
        "reachable",
        lambda v: v is False,
        lambda v: v is True,
        "high",
        "Check known upstream paths and independent monitoring evidence",
    )
    rule(
        "collector_failure",
        "collector_ok",
        lambda v: v is False,
        lambda v: v is True,
        "high",
        "Investigate the monitoring collector; missing readings are not healthy equipment",
    )
    rule(
        "interface_down",
        "interface_up",
        lambda v: v is False,
        lambda v: v is True,
        "high",
        "Review read-only interface state and upstream dependencies",
    )
    rule(
        "rf_degraded",
        "rf_ok",
        lambda v: v is False,
        lambda v: v is True,
        "medium",
        "Review existing RF diagnostics and historical measurements",
    )
    rule(
        "commercial_power_lost",
        "ac_present",
        lambda v: v is False,
        lambda v: v is True,
        "high",
        "Inspect commercial power and backup-power evidence; service impact is unconfirmed",
    )
    rule(
        "ups_warning",
        "ups_state",
        lambda v: v in {"low_battery", "fault"},
        lambda v: v == "online",
        "high",
        "Inspect UPS alarms and measured battery status",
    )
    rule(
        "supply_warning",
        "supply_ok",
        lambda v: v is False,
        lambda v: v is True,
        "high",
        "Inspect the registered power supply and its alarms",
    )
    for key, threshold, name in [
        ("dc_voltage", p.low_dc_voltage, "low_dc_voltage"),
        ("battery_voltage", p.low_battery_voltage, "low_battery_voltage"),
    ]:
        if threshold is not None:
            rule(
                name,
                key,
                lambda v, t=threshold: v < t,
                lambda v, t=threshold: v >= t + p.voltage_hysteresis,
                "high",
                "Inspect measured voltage against the configured device-system threshold",
            )

    rule(
        "low_rssi",
        "rssi_dbm",
        lambda v: v < p.rssi_warning_dbm,
        lambda v: v >= p.rssi_warning_dbm + 3,
        "medium",
        "Review sustained observed signal level and vendor-specific RF thresholds",
    )
    rule(
        "low_snr",
        "snr_db",
        lambda v: v < p.snr_warning_db,
        lambda v: v >= p.snr_warning_db + 3,
        "medium",
        "Review measured SNR and existing interference diagnostics",
    )
    rule(
        "airtime_pressure",
        "airtime_percent",
        lambda v: v >= p.utilization_warning_percent,
        lambda v: v < p.utilization_warning_percent - p.utilization_hysteresis_percent,
        "high",
        "Investigate observed RF airtime pressure; Ethernet traffic is not RF capacity",
    )

    # Derived metrics are only generated from values from the same observation.
    class Proxy:
        pass

    derived = []
    for row in rows:
        d = Proxy()
        d.observed_at = row.observed_at
        d.event_id = row.event_id
        vals = flat(row)
        cap = capacity(vals)
        powr = power(vals)
        d.data = {
            "metrics": {
                "capacity_utilization": cap["observed_utilization_percent"],
                "battery_health_ratio": powr["measured_to_rated_full_capacity_ratio"],
            },
            "unavailable": {},
        }
        derived.append(d)
    original = rows
    rows = derived
    rule(
        "congestion",
        "capacity_utilization",
        lambda v: v >= p.utilization_warning_percent,
        lambda v: v < p.utilization_warning_percent - p.utilization_hysteresis_percent,
        "high",
        "Review measured traffic and current adaptive capacity before planning an upgrade",
    )
    rule(
        "battery_deterioration_indicator",
        "battery_health_ratio",
        lambda v: v < p.battery_health_ratio,
        lambda v: p.battery_health_ratio + 0.05 <= v <= 1.2,
        "medium",
        "Review measured full-charge capacity and rated calibration; this is an indicator, not predicted failure",
    )
    congestion = points(derived, "capacity_utilization")
    bursts = []
    previous = None
    for pt in congestion:
        if pt[1] is None:
            previous = None
            continue
        if (
            pt[1] >= p.utilization_warning_percent
            and previous is not None
            and previous
            < p.utilization_warning_percent - p.utilization_hysteresis_percent
        ):
            bursts.append(pt)
        previous = pt[1]
    rows = original

    # Pattern findings require human review; absence in a truncated window is not recovery.
    def pattern(name, ps, condition, action, priority="medium"):
        if condition and ps and now - ps[-1][0] <= timedelta(seconds=p.max_age_seconds):
            decisions[name] = {
                "state": "warning",
                "priority": priority,
                "confidence": "observed_pattern",
                "action": action,
                "sample_events": [v[2] for v in ps],
                "values": [v[1] for v in ps],
                "observed_at": ps[-1][0].isoformat(),
                "window_coverage": "bounded observed samples; not continuous monitoring",
            }

    pattern(
        "recurring_congestion",
        bursts,
        len(bursts) >= 3,
        "Review observed recurring congestion with peak-hour demand and available capacity",
        "high",
    )
    up = points(rows, "uptime_seconds")
    resets = [
        b
        for a, b in zip(up, up[1:])
        if a[1] is not None and b[1] is not None and b[1] < a[1]
    ]
    pattern(
        "repeated_uptime_resets",
        resets,
        len(resets) >= p.reboot_warning_count,
        "Investigate observed uptime resets; power or software cause is unconfirmed",
        "high",
    )
    ac = points(rows, "ac_present")
    changes = [
        b
        for a, b in zip(ac, ac[1:])
        if a[1] is not None and b[1] is not None and a[1] != b[1]
    ]
    pattern(
        "power_instability",
        changes,
        len(changes) >= p.power_transition_warning_count,
        "Inspect repeated AC transitions and correlated UPS evidence",
        "high",
    )
    temps = [v for v in points(rows, "temperature_c") if v[1] is not None]
    baseline = [
        v for v in temps if temps and v[0] <= temps[-1][0] - timedelta(hours=1)
    ][-3:]
    recent = temps[-3:]
    pattern(
        "rising_temperature",
        baseline + recent,
        len(baseline) == 3
        and len(recent) == 3
        and mean(v[1] for v in recent) - mean(v[1] for v in baseline)
        >= p.temperature_rise_c,
        "Review observed temperature increase and cooling conditions",
    )
    caps = [v for v in points(rows, "reported_capacity_mbps") if v[1] is not None]
    pattern(
        "adaptive_capacity_reduction",
        caps[-6:],
        len(caps) >= 6
        and median(v[1] for v in caps[-6:-3]) > 0
        and all(v[1] < median(x[1] for x in caps[-6:-3]) * 0.75 for v in caps[-3:]),
        "Investigate reduced measured link capacity and modulation; nominal inventory capacity is not available capacity",
        "high",
    )
    mods = points(rows, "modulation_bits")
    drops = [
        b
        for a, b in zip(mods, mods[1:])
        if a[1] is not None and b[1] is not None and b[1] < a[1]
    ]
    pattern(
        "repeated_modulation_fallback",
        drops,
        len(drops) >= 3,
        "Review repeated observed modulation drops with RF diagnostics",
    )
    errors = points(rows, "interface_error_count")
    uptimes = {t: v for t, v, *_ in up}
    rates = []
    for a, b in zip(errors, errors[1:]):
        t0, c0, _, scope0 = a
        t1, c1, event, scope1 = b
        u0 = uptimes.get(t0)
        u1 = uptimes.get(t1)
        dt = (t1 - t0).total_seconds()
        if (
            None not in (c0, c1, u0, u1)
            and scope0
            and scope0 == scope1
            and u1 >= u0
            and c1 >= c0
            and 0 < dt <= p.max_age_seconds
        ):
            rates.append((t1, (c1 - c0) / dt, event, scope1))
    pattern(
        "increasing_interface_errors",
        rates[-3:],
        len(rates) >= 3
        and all(v[1] >= p.error_rate_warning_per_second for v in rates[-3:]),
        "Inspect increasing errors on the same recorded interface set; counter resets are excluded",
        "high",
    )
    collector = points(rows, "collector_ok")
    if collector and collector[-1][1] is False:
        for name, result in decisions.items():
            if name != "collector_failure":
                result["state"] = "unknown"
                result["confidence"] = "collector_unavailable"
    return decisions


def daily_update(values, o, p):
    v = {
        **o.metrics.model_dump(exclude_unset=True),
        **o.power.model_dump(exclude_unset=True),
    }
    cap = capacity(v)
    result = dict(values or {})
    if result.get("timezone") is not None and result["timezone"] != p.timezone:
        result["timezone_mixed"] = True
    result.setdefault("timezone", p.timezone)
    hour = str(o.observed_at.astimezone(ZoneInfo(p.timezone)).hour)
    if v.get("tx_mbps") is not None and v["tx_mbps"] >= result.get(
        "tx_peak_mbps", v["tx_mbps"]
    ):
        result["tx_peak_capacity_known"] = cap["tx_utilization_percent"] is not None
    for key, value in [
        ("tx_peak_mbps", v.get("tx_mbps")),
        ("rx_peak_mbps", v.get("rx_mbps")),
        ("util_peak_percent", cap["observed_utilization_percent"]),
    ]:
        if value is not None:
            result[key] = max(result.get(key, value), value)
    for key in ["tx_capacity_mbps", "rx_capacity_mbps", "reported_capacity_mbps"]:
        value = v.get(key)
        if value is not None:
            result[key + "_min"] = min(result.get(key + "_min", value), value)
            result[key + "_max"] = max(result.get(key + "_max", value), value)
    hours = dict(result.get("hours", {}))
    h = dict(hours.get(hour, {}))
    if v.get("tx_mbps") is not None and v["tx_mbps"] >= h.get(
        "tx_peak_mbps", v["tx_mbps"]
    ):
        h["tx_capacity_known"] = cap["tx_utilization_percent"] is not None
    if v.get("tx_mbps") is not None:
        h["tx_peak_mbps"] = max(h.get("tx_peak_mbps", v["tx_mbps"]), v["tx_mbps"])
    if v.get("rx_mbps") is not None:
        h["rx_peak_mbps"] = max(h.get("rx_peak_mbps", v["rx_mbps"]), v["rx_mbps"])
    if h:
        hours[hour] = h
    result["hours"] = hours
    return result


def forecast(daily, days, now, p, current_tx_capacity=None):
    if days not in {30, 60, 90}:
        raise ValueError("Supported trend windows are30,60,90 days")
    cutoff = now.date() - timedelta(days=days)
    rows = [
        d
        for d in daily
        if cutoff <= d.day < now.date()
        and sum(
            bool(h.get("tx_capacity_known")) for h in d.values.get("hours", {}).values()
        )
        >= p.forecast_hours_per_day
        and d.values.get("tx_peak_capacity_known", False)
        and d.values.get("tx_peak_mbps") is not None
        and not d.values.get("conflicting_observation_times")
        and not d.values.get("timezone_mixed")
        and d.values.get("timezone") == p.timezone
    ]
    base = {
        "window_days": days,
        "covered_days": len(rows),
        "required_days": int(days * p.forecast_coverage_fraction + 0.999),
        "projected_demand_mbps": None,
        "confidence": "insufficient_history",
        "basis": "observed daily TX peaks, not provisioned bandwidth",
        "guaranteed": False,
    }
    if (
        len(rows) < base["required_days"]
        or max(d.day for d in rows) < now.date() - timedelta(days=2)
        or (max(d.day for d in rows) - min(d.day for d in rows)).days < days - 3
    ):
        return base
    if current_tx_capacity is None or current_tx_capacity <= 0:
        return base | {"confidence": "current_directional_capacity_unavailable"}
    caps = [
        d.values.get(k)
        for d in rows
        for k in ["tx_capacity_mbps_min", "tx_capacity_mbps_max"]
    ]
    if any(v is None for v in caps) or min(caps) <= 0 or max(caps) / min(caps) > 1.1:
        return base | {"confidence": "changing_or_unknown_capacity"}
    xs = [(d.day - cutoff).days for d in rows]
    ys = [d.values["tx_peak_mbps"] for d in rows]
    mx = mean(xs)
    my = mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0
    estimate = max(0, my + slope * (days + 30 - mx))
    variance = sum((y - my) ** 2 for y in ys)
    error = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
    fit = 1 - error / variance if variance else 1.0
    if fit < 0.5:
        return base | {"confidence": "unstable_observed_trend", "fit_r_squared": fit}

    return base | {
        "confidence": "historical_observed_trend",
        "daily_growth_mbps": slope,
        "fit_r_squared": fit,
        "upgrade_priority": (
            "high"
            if slope > 0
            and estimate / current_tx_capacity >= p.utilization_warning_percent / 100
            else "monitor"
        ),
        "projected_demand_mbps": estimate,
        "horizon_days": 30,
        "projected_demand_to_current_capacity_ratio": estimate / current_tx_capacity,
        "capacity_assumption_mbps": current_tx_capacity,
        "caution": "Linear scenario assuming unchanged capacity and comparable observation coverage; not guaranteed demand or a failure probability",
    }
