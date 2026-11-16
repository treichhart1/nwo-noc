"""Synthetic NOC-98 health, power, capacity, history, privacy and recovery acceptance."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import math, pytest
from sqlalchemy import select, func
from pydantic import ValidationError
from app.core.config import settings
from app.tower_health.contracts import Observation, Metrics, Power, Policy
from app.tower_health.analytics import (
    capacity,
    power,
    forecast,
    current,
    rule_decisions,
)
from app.tower_health.service import ingest, assess, prune, clock, trend_report
from app.tower_health.models import (
    TowerEvent,
    TowerSample,
    TowerDaily,
    TowerFinding,
    TowerCurrent,
    TowerPolicyRow,
    TowerHistory,
)
from app.tower_health.worker import consume
from app.models.entities import Device, TelemetrySample, AuditLog
from app.customers.models import CustomerService
from app.notifications.models import NotificationPlan, NotificationDelivery
from app.down_detector.models import OutageEpisode, OutageState
from test_notifications import network, api
from test_powercode_customers import imported, inventory
from test_rf_postgres_integration import postgres_db


@pytest.fixture
def health(network):
    network.now = clock()
    return network


@pytest.fixture
def client(api):
    x, c, h = api
    x.now = clock()
    return x, c, h


def observation(
    x,
    second=0,
    *,
    metrics=None,
    power_data=None,
    device=None,
    sector=None,
    link=None,
    event=None,
    base=True,
    unavailable=None,
):
    d = device or x.ap
    values = (
        {
            "reachable": True,
            "collector_ok": True,
            "cpu_percent": 20.0,
            "memory_percent": 30.0,
            "temperature_c": 45.0,
            "uptime_seconds": 10000.0 + second,
            "interface_up": True,
            "rf_ok": True,
        }
        if base
        else {}
    )
    values.update(metrics or {})
    return Observation(
        event_id=event or f"SYN98:{d.id}:{second}",
        site_id=d.wisp_site_id,
        device_id=d.id,
        sector_id=sector,
        link_id=link,
        source="synthetic",
        observed_at=x.now + timedelta(seconds=second),
        metrics=Metrics(**values),
        power=Power(**(power_data or {})),
        unavailable=unavailable or {},
    )


def sequence(
    x, *, metrics=None, power_data=None, device=None, sector=None, link=None, base=True
):
    for second in [-120, -60, 0]:
        ingest(
            x.db,
            observation(
                x,
                second,
                metrics=metrics,
                power_data=power_data,
                device=device,
                sector=sector,
                link=link,
                base=base,
            ),
            now=x.now,
        )
    return assess(x.db, site_ids=[x.a.id], now=x.now, persist=True)[0]


def rules(x):
    return {f.rule: f for f in x.db.scalars(select(TowerFinding))}


@pytest.mark.parametrize(
    "metrics",
    [
        {"cpu_percent": 101},
        {"memory_percent": -1},
        {"temperature_c": float("nan")},
        {"tx_mbps": True},
        {"reachable": 1},
        {"interface_error_count": -1},
        {"subscribers": True},
        {"tx_capacity_mbps": 0},
        {"latency_ms": float("inf")},
        {"api_token": "forbidden"},
    ],
)
def test_closed_metric_contract(metrics):
    with pytest.raises(ValidationError):
        Metrics.model_validate(metrics)


@pytest.mark.parametrize(
    "values",
    [
        {"battery_voltage": -1},
        {"ac_present": 0},
        {"load_watts": float("nan")},
        {"charge_state": "guess"},
        {"rated_capacity_wh": 0},
        {"password": "forbidden"},
    ],
)
def test_closed_power_contract(values):
    with pytest.raises(ValidationError):
        Power.model_validate(values)


@pytest.mark.parametrize(
    "values,expected,basis",
    [
        ({}, None, "unavailable"),
        (
            {
                "remaining_energy_wh": 20.0,
                "load_watts": 10.0,
                "charge_state": "discharging",
            },
            120.0,
            "remaining_measured_energy_divided_by_measured_load",
        ),
        (
            {
                "remaining_energy_wh": 20.0,
                "load_watts": 0.0,
                "charge_state": "discharging",
            },
            None,
            "unavailable",
        ),
        (
            {
                "remaining_energy_wh": 20.0,
                "load_watts": 10.0,
                "charge_state": "charging",
            },
            None,
            "unavailable",
        ),
        ({"reported_runtime_minutes": 0.0}, 0.0, "vendor_reported"),
        (
            {
                "remaining_energy_wh": 1e7,
                "load_watts": 5e-324,
                "charge_state": "discharging",
            },
            None,
            "measurement_pair_outside_supported_estimation_range",
        ),
    ],
)
def test_runtime_requires_justified_measured_pair(values, expected, basis):
    r = power(values)
    assert (
        r["runtime_minutes"] == expected
        and r["runtime_basis"] == basis
        and not r["runtime_is_guarantee"]
    )


@pytest.mark.parametrize(
    "values,tx,rx",
    [
        ({}, None, None),
        ({"tx_mbps": 80.0}, None, None),
        ({"tx_mbps": 80.0, "reported_capacity_mbps": 100.0}, None, None),
        ({"tx_phy_rate_mbps": 1000.0, "tx_capacity_mbps": 100.0}, None, None),
        (
            {
                "tx_mbps": 80.0,
                "rx_mbps": 30.0,
                "tx_capacity_mbps": 100.0,
                "rx_capacity_mbps": 50.0,
            },
            80.0,
            60.0,
        ),
        ({"tx_mbps": 101.0, "tx_capacity_mbps": 100.0}, None, None),
    ],
)
def test_directional_capacity_does_not_invent_rf_capacity(values, tx, rx):
    r = capacity(values)
    assert r["tx_utilization_percent"] == tx and r["rx_utilization_percent"] == rx
    if tx is None:
        assert r["tx_headroom_mbps"] is None


def test_healthy_observed_tower_preserves_missing_metric_states(health):
    x = health
    r = sequence(x)
    assert r["state"] == "observed_normal" and r["known_metric_score"] == 100
    assert r["equipment"][0]["metric_states"]["battery_voltage"]["state"] == "missing"
    assert r["equipment"][0]["power"]["runtime_minutes"] is None
    assert r["customers"]["known_active_customers"] == 1
    assert r["automatic_actions_enabled"] is False


@pytest.mark.parametrize(
    "metrics,rule",
    [
        ({"temperature_c": 80.0}, "temperature_high"),
        ({"temperature_c": 95.0}, "temperature_critical"),
        ({"cpu_percent": 95.0}, "cpu_pressure"),
        ({"memory_percent": 95.0}, "memory_pressure"),
        ({"reachable": False}, "management_unreachable"),
        ({"collector_ok": False}, "collector_failure"),
        ({"rf_ok": False}, "rf_degraded"),
        ({"interface_up": False}, "interface_down"),
    ],
)
def test_sustained_equipment_warnings(health, metrics, rule):
    x = health
    r = sequence(x, metrics=metrics)
    assert rule in rules(x)
    assert rules(x)[rule].state == "active" and r["state"] in {"warning", "critical"}
    if rule == "collector_failure":
        assert (
            r["known_metric_score"] is None
            and r["coverage"]["fresh_usable_devices"] == 0
        )


def test_registered_ups_unavailable_is_unknown_not_healthy_or_power_loss(health):
    x = health
    sequence(x)
    ups = Device(
        source="synthetic",
        external_id="SYN98-UPS",
        name="Synthetic registered UPS",
        role="power",
        wisp_site_id=x.a.id,
    )
    x.db.add(ups)
    x.db.flush()
    ingest(
        x.db,
        observation(x, device=ups, base=False, metrics={"collector_ok": True}),
        now=x.now,
    )
    r = assess(x.db, site_ids=[x.a.id], now=x.now, persist=True)[0]
    assert r["state"] == "unknown" and r["known_metric_score"] is None
    assert not x.db.scalar(select(OutageEpisode))


def test_stale_and_missing_data_do_not_clear_warnings(health):
    x = health
    sequence(x, metrics={"temperature_c": 90.0})
    ingest(
        x.db,
        observation(
            x,
            second=1,
            metrics={"temperature_c": None},
            unavailable={"temperature_c": "unsupported"},
        ),
        now=x.now + timedelta(seconds=1),
    )
    r = assess(
        x.db, site_ids=[x.a.id], now=x.now + timedelta(seconds=400), persist=True
    )[0]
    assert rules(x)["temperature_high"].state == "active"
    assert (
        r["known_metric_score"] is None
        and r["findings"][0]["current_verification"] == "stale_or_unverified"
    )


def test_hysteresis_and_consecutive_recovery(health):
    x = health
    sequence(x, metrics={"temperature_c": 80.0})
    ingest(
        x.db,
        observation(x, 1, metrics={"temperature_c": 68.0}),
        now=x.now + timedelta(seconds=1),
    )
    assess(x.db, site_ids=[x.a.id], now=x.now + timedelta(seconds=1), persist=True)
    assert rules(x)["temperature_high"].state == "active"
    for sec in [31, 61]:
        ingest(
            x.db,
            observation(x, sec, metrics={"temperature_c": 68.0}),
            now=x.now + timedelta(seconds=sec),
        )
    assess(x.db, site_ids=[x.a.id], now=x.now + timedelta(seconds=61), persist=True)
    assert rules(x)["temperature_high"].state == "cleared"


def test_low_voltage_requires_calibrated_threshold_and_measured_recovery(health):
    x = health
    sequence(x, power_data={"battery_voltage": 11.0})
    assert "low_battery_voltage" not in rules(x)
    x.db.add(
        TowerPolicyRow(
            id=1,
            config=Policy(
                device_voltage_thresholds={x.ap.id: {"low_battery_voltage": 12.0}}
            ).model_dump(mode="json"),
        )
    )
    x.db.flush()
    assess(x.db, site_ids=[x.a.id], now=x.now, persist=True)
    assert "low_battery_voltage" in rules(x)
    for sec in [1, 31, 61]:
        ingest(
            x.db,
            observation(x, sec, power_data={"battery_voltage": 12.6}),
            now=x.now + timedelta(seconds=sec),
        )
    assess(x.db, site_ids=[x.a.id], now=x.now + timedelta(seconds=61), persist=True)
    assert rules(x)["low_battery_voltage"].state == "cleared"


def test_power_outage_and_power_only_restoration_do_not_invent_network_up(health):
    x = health
    sequence(
        x,
        base=False,
        metrics={"collector_ok": True},
        power_data={"ac_present": False, "ups_state": "on_battery"},
    )
    e = x.db.scalar(select(OutageEpisode))
    assert e.classification == "confirmed_commercial_power_outage"
    assert x.db.get(OutageState, e.key).state == "degraded"
    for sec in [1, 31, 61]:
        ingest(
            x.db,
            observation(
                x,
                sec,
                base=False,
                metrics={"collector_ok": True},
                power_data={"ac_present": True, "ups_state": "online"},
            ),
            now=x.now + timedelta(seconds=sec),
        )
    assess(x.db, site_ids=[x.a.id], now=x.now + timedelta(seconds=61), persist=True)
    assert (
        e.status == "resolved"
        and x.db.get(OutageState, e.key).state == "power_restored"
    )
    assert rules(x)["commercial_power_lost"].state == "cleared"


def test_power_instability_battery_deterioration_and_reboots(health):
    x = health
    for i, (u, ac) in enumerate(
        [
            (1000, True),
            (200, False),
            (800, True),
            (100, False),
            (300, True),
            (50, False),
        ]
    ):
        sec = -300 + i * 60
        ingest(
            x.db,
            observation(
                x,
                sec,
                metrics={"uptime_seconds": float(u)},
                power_data={
                    "ac_present": ac,
                    "measured_full_capacity_wh": 60.0,
                    "rated_capacity_wh": 100.0,
                },
            ),
            now=x.now,
        )
    assess(x.db, site_ids=[x.a.id], now=x.now, persist=True)
    assert {
        "repeated_uptime_resets",
        "power_instability",
        "battery_deterioration_indicator",
    } <= set(rules(x))


def test_sector_capacity_provisioning_and_actual_demand_are_distinct(health):
    x = health
    r = sequence(
        x,
        sector=x.sector.id,
        metrics={
            "tx_mbps": 10.0,
            "tx_capacity_mbps": 100.0,
            "subscriber_down_capacity_mbps": 10.0,
            "subscribers": 12,
            "airtime_percent": 20.0,
        },
    )
    resource = next(r for r in r["resources"] if r["sector_id"] == x.sector.id)
    assert resource["capacity"]["tx_utilization_percent"] == 10.0
    assert (
        resource["known_provisioned_down_to_reported_subscriber_capacity_ratio"] == 10.0
    )
    assert resource["ratio_is_actual_congestion"] is False
    assert "provisioned_capacity_review" in rules(x) and "congestion" not in rules(x)


def test_measured_congestion_and_adaptive_capacity_reduction(health):
    x = health
    x.up.a_device_id = None
    x.up.z_device_id = x.ap.id
    x.db.flush()
    # AP is a registered endpoint at the Z site; no guessed association.
    for i, cap in enumerate([100.0, 100.0, 100.0, 50.0, 50.0, 50.0]):
        sec = -300 + i * 60
        ingest(
            x.db,
            observation(
                x,
                sec,
                link=x.up.id,
                metrics={
                    "tx_mbps": cap * 0.95,
                    "tx_capacity_mbps": cap,
                    "reported_capacity_mbps": cap,
                    "modulation_bits": 12 - i,
                },
            ),
            now=x.now,
        )
    r = assess(x.db, site_ids=[x.a.id], now=x.now, persist=True)[0]
    assert {
        "congestion",
        "adaptive_capacity_reduction",
        "repeated_modulation_fallback",
    } <= set(rules(x))
    assert (
        next(r for r in r["resources"] if r["link_id"])["exposure"][
            "potential_customers"
        ]
        == 1
    )


def test_counter_resets_and_unknown_uptime_never_become_error_rate(health):
    x = health
    for i, (u, error) in enumerate(
        [(1000.0, 100), (1100.0, 200), (10.0, 1), (20.0, 2)]
    ):
        sec = -180 + i * 60
        o = observation(
            x, sec, metrics={"uptime_seconds": u, "interface_error_count": error}
        )
        o.counter_scope = "a" * 64
        ingest(x.db, o, now=x.now)
    assess(x.db, site_ids=[x.a.id], now=x.now, persist=True)
    assert "increasing_interface_errors" not in rules(x)


def test_duplicate_conflicting_events_and_conflicting_same_time(health):
    x = health
    o = observation(x)
    ingest(x.db, o, now=x.now)
    assert ingest(x.db, o, now=x.now)["duplicate"]
    with pytest.raises(ValueError, match="Conflicting"):
        ingest(
            x.db,
            o.model_copy(update={"metrics": Metrics(temperature_c=95.0)}),
            now=x.now,
        )
    conflict = o.model_copy(
        update={
            "event_id": "SYN98-CONFLICT-TIME",
            "metrics": Metrics(temperature_c=95.0),
        }
    )
    ingest(x.db, conflict, now=x.now)
    report = assess(x.db, site_ids=[x.a.id], now=x.now)[0]
    assert (
        report["equipment"][0]["metric_states"]["temperature_c"]["state"] == "invalid"
    )
    daily = x.db.scalar(select(TowerDaily))
    assert daily.samples == 1 and daily.values["conflicting_observation_times"]


def test_wrong_customer_mappings_not_counted_or_exposed(health):
    x = health
    x.service.site_id = x.b.id
    x.db.flush()
    r = sequence(x)
    assert (
        r["customers"]["known_active_customers"] == 0
        and r["unassigned_or_unverified_active_services_fleet"] == 1
    )
    assert "phone" not in str(r) and "+1555" not in str(r)


def test_customer_counts_change_without_duplicate_accounts(health):
    x = health
    sequence(x)
    s = CustomerService(
        external_id="SYN98-extra-service",
        customer_id=x.account.id,
        status="active",
        service_type="wireless",
        site_id=x.a.id,
        device_id=x.ap.id,
        sector_id=x.sector.id,
        mapping_state="verified",
        source_refs={},
        source_updated_at=x.now,
        last_synced_at=x.now,
        download_mbps=50.0,
        upload_mbps=10.0,
        plan="Synthetic 50/10",
    )
    x.db.add(s)
    x.db.flush()
    r = assess(x.db, site_ids=[x.a.id], now=x.now, persist=True)[0]
    assert (
        r["customers"]["known_active_customers"] == 1
        and r["customers"]["known_active_services"] == 2
    )
    assert r["customers"]["known_provisioned_down_mbps"] == 150.0


def test_worker_restart_rollback_malformed_payloads_and_replay(health):
    x = health
    for i, raw in enumerate(
        [
            [],
            "malformed",
            {
                "tower_health_v1": {
                    "metrics": {"collector_ok": True, "temperature_c": 95.0}
                }
            },
        ]
    ):
        x.db.add(
            TelemetrySample(
                device_id=x.ap.id,
                sampled_at=x.now - timedelta(seconds=120 - i * 60),
                raw_data=raw,
            )
        )
    x.db.flush()
    with x.db.begin_nested() as tx:
        assert consume(x.db, now=x.now)["processed"] == 3
        tx.rollback()
    assert not x.db.scalar(select(TowerSample))
    assert consume(x.db, now=x.now)["processed"] == 3
    x.db.expire_all()
    assert consume(x.db, now=x.now)["processed"] == 0
    assert x.db.scalar(select(func.count(TowerSample.id))) == 3


def test_invalid_inventory_associations_rejected(health):
    x = health
    with pytest.raises(ValueError, match="Backhaul"):
        ingest(x.db, observation(x, link=x.up.id), now=x.now)
    x.sector.site_id = x.b.id
    x.db.flush()
    with pytest.raises(ValueError, match="Sector"):
        ingest(x.db, observation(x, sector=x.sector.id), now=x.now)


def test_api_maintenance_notes_preview_never_selects_customer_sms(client):
    x, c, h = client
    sequence(x, metrics={"temperature_c": 95.0})
    x.db.commit()
    assert c.get("/api/tower-health", headers=h).status_code == 200
    detail = c.get(f"/api/tower-health/sites/{x.a.id}", headers=h)
    assert detail.status_code == 200, detail.text
    assert "phone" not in detail.text and "+1555" not in detail.text
    f = rules(x)["temperature_critical"]
    assert (
        c.post(
            f"/api/tower-health/findings/{f.id}/actions",
            headers=h,
            json={"action": "acknowledge", "explanation": "          "},
        ).status_code
        == 409
    )
    r = c.post(
        f"/api/tower-health/findings/{f.id}/actions",
        headers=h,
        json={
            "action": "acknowledge",
            "explanation": "Synthetic technician reviewing temperature evidence",
        },
    )
    assert r.status_code == 200, r.text
    r = c.post(f"/api/tower-health/findings/{f.id}/technician-preview", headers=h)
    assert r.status_code == 200, r.text
    plan = x.db.get(NotificationPlan, r.json()["plan_id"])
    assert plan.status == "draft"
    assert not any(r["eligible"] for r in plan.snapshot["recipients"])
    assert not x.db.scalar(select(NotificationDelivery.id))
    assert (
        c.post(
            f"/api/tower-health/findings/{f.id}/technician-preview", headers=h
        ).json()["plan_id"]
        == plan.id
    )
    assert (
        c.get(
            f"/api/tower-health/sites/{x.a.id}/history?scope_key=device:{x.ap.id}&days=30",
            headers=h,
        ).status_code
        == 200
    )
    assert c.get(f"/api/tower-health/sites/{x.a.id}/observations", headers=h).json()[
        "items"
    ]
    assert c.get(f"/api/tower-health/findings/{f.id}", headers=h).json()["notes"]


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_policy_and_synthetic_ingress_permissions(client, role):
    x, c, h = client
    x.admin.role = role
    x.db.commit()
    assert (
        c.put(
            "/api/tower-health/policy", headers=h, json=Policy().model_dump(mode="json")
        ).status_code
        == 403
    )
    assert c.post("/api/tower-health/refresh", headers=h, json={}).status_code == 403
    if role == "viewer":
        assert c.get("/api/tower-health", headers=h).status_code == 403


def test_voltage_policy_requires_registered_device_and_synthetic_gate(
    client, monkeypatch
):
    x, c, h = client
    assert (
        c.put(
            "/api/tower-health/policy",
            headers=h,
            json=Policy(
                device_voltage_thresholds={999999: {"low_dc_voltage": 44.0}}
            ).model_dump(mode="json"),
        ).status_code
        == 409
    )
    monkeypatch.setattr(settings, "synthetic_monitoring_enabled", False)
    assert (
        c.post(
            "/api/tower-health/synthetic-observations",
            headers=h,
            json=[observation(x).model_dump(mode="json", exclude_unset=True)],
        ).status_code
        == 409
    )


def daily_fixture(now, days=30, hours=12, changing=False):
    rows = []
    for i in range(days):
        day = now.date() - timedelta(days=days - i)
        cap = 50.0 if changing and i > days // 2 else 100.0
        rows.append(
            SimpleNamespace(
                day=day,
                values={
                    "tx_peak_mbps": 10.0 + i,
                    "tx_peak_capacity_known": True,
                    "timezone": "America/New_York",
                    "tx_capacity_mbps_min": cap,
                    "tx_capacity_mbps_max": cap,
                    "hours": {
                        str(h): {"tx_peak_mbps": 10.0 + i, "tx_capacity_known": True}
                        for h in range(hours)
                    },
                },
            )
        )
    return rows


@pytest.mark.parametrize("days", [30, 60, 90])
def test_forecast_requires_full_span_coverage_and_stable_current_capacity(days):
    now = datetime.now(timezone.utc)
    p = Policy()
    rows = daily_fixture(now, days)
    result = forecast(rows, days, now, p, 100.0)
    assert result["confidence"] == "historical_observed_trend" and result[
        "daily_growth_mbps"
    ] == pytest.approx(1.0)
    assert (
        result["projected_demand_mbps"] > rows[-1].values["tx_peak_mbps"]
        and not result["guaranteed"]
    )


@pytest.mark.parametrize(
    "days,hours,cap,changing",
    [
        (5, 12, 100.0, False),
        (30, 1, 100.0, False),
        (30, 12, None, False),
        (30, 12, 100.0, True),
    ],
)
def test_sparse_or_changing_capacity_does_not_produce_forecast(
    days, hours, cap, changing
):
    now = datetime.now(timezone.utc)
    result = forecast(daily_fixture(now, days, hours, changing), 30, now, Policy(), cap)
    assert result["projected_demand_mbps"] is None


def test_retention_preserves_aggregates_dedupe_and_inventory(health):
    x = health
    old = x.now - timedelta(days=40)
    o = observation(x, event="SYN98-OLD").model_copy(update={"observed_at": old})
    ingest(x.db, o, now=x.now)
    prune(x.db, now=x.now)
    assert not x.db.scalar(select(TowerSample)) and x.db.scalar(select(TowerDaily))
    assert ingest(x.db, o, now=x.now)["duplicate"]
    assert x.db.get(Device, x.ap.id) and x.db.get(TowerEvent, "SYN98-OLD")


def test_pending_temperature_warning_is_not_green(health):
    x = health
    ingest(x.db, observation(x, metrics={"temperature_c": 80.0}), now=x.now)
    r = assess(x.db, site_ids=[x.a.id], now=x.now, persist=True)[0]
    assert r["pending_confirmation_count"] > 0 and r["known_metric_score"] is None
    assert r["state"] == "pending_confirmation"


def test_capacity_and_runtime_require_same_frame(health):
    x = health
    ingest(
        x.db,
        observation(
            x,
            -60,
            metrics={"tx_capacity_mbps": 100.0},
            power_data={"remaining_energy_wh": 100.0, "charge_state": "discharging"},
            base=False,
        ),
        now=x.now,
    )
    ingest(
        x.db,
        observation(
            x, metrics={"tx_mbps": 90.0}, power_data={"load_watts": 50.0}, base=False
        ),
        now=x.now,
    )
    r = assess(x.db, site_ids=[x.a.id], now=x.now)[0]
    d = next(v for v in r["equipment"] if v["device_id"] == x.ap.id)
    cap = next(v["capacity"] for v in r["resources"] if v["device_id"] == x.ap.id)
    assert (
        d["power"]["runtime_minutes"] is None and cap["tx_utilization_percent"] is None
    )


def test_source_expiry_is_not_extended_by_reassessment(health):
    x = health
    sequence(x)
    first = assess(x.db, site_ids=[x.a.id], now=x.now)[0]
    again = assess(x.db, site_ids=[x.a.id], now=x.now + timedelta(seconds=30))[0]
    assert first["data_expires_at"] == again["data_expires_at"]
    late = assess(x.db, site_ids=[x.a.id], now=x.now + timedelta(seconds=301))[0]
    assert late["known_metric_score"] is None


def test_postgres_daily_history_forecast_and_timezone_gate(health):
    x = health
    for i in range(30):
        day = x.now.date() - timedelta(days=30 - i)
        for hour in range(13):  # Includes12 distinct local hours across DST fallback.
            at = datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(
                hours=hour
            )
            ingest(
                x.db,
                Observation(
                    event_id=f"SYN98-history:{i}:{hour}",
                    source="synthetic",
                    site_id=x.a.id,
                    device_id=x.ap.id,
                    observed_at=at,
                    metrics={"tx_mbps": float(10 + i), "tx_capacity_mbps": 100.0},
                ),
                now=x.now,
                emit_power=False,
            )
    ingest(
        x.db,
        observation(x, metrics={"tx_mbps": 40.0, "tx_capacity_mbps": 100.0}),
        now=x.now,
    )
    rows = list(
        x.db.scalars(
            select(TowerDaily).where(TowerDaily.scope_key == f"device:{x.ap.id}")
        )
    )
    result = forecast(rows, 30, x.now, Policy(), 100.0)
    assert (
        result["confidence"] == "historical_observed_trend"
        and result["covered_days"] == 30
    )
    assert result["daily_growth_mbps"] == pytest.approx(1.0)
    assert (
        forecast(rows, 30, x.now, Policy(timezone="UTC"), 100.0)[
            "projected_demand_mbps"
        ]
        is None
    )


@pytest.mark.parametrize(
    "profile",
    [
        "airmax-ac-mib-v1",
        "airmax-legacy-mib-v1",
        "epmp-4600-mib-v1",
        "epmp-legacy-mib-v1",
    ],
)
def test_recorded_radio_adapter_preserves_traffic_and_capacity_meanings(profile):
    from app.tower_health.worker import normalize
    from radio_fixtures import observation as radio

    now = clock()
    r = radio(profile, at=now)
    row = SimpleNamespace(
        id=1, sampled_at=now, raw_data={"rf_radio_v2": r.model_dump(mode="json")}
    )
    d = SimpleNamespace(id=1, wisp_site_id=1)
    o = normalize(row, d, [])
    assert o.source == "recorded_radio" and o.metrics.collector_ok is True
    assert o.metrics.tx_mbps == r.measurements.tx_throughput_mbps
    assert o.metrics.reported_capacity_mbps == r.measurements.link_capacity_mbps
    assert o.metrics.tx_capacity_mbps is None


@pytest.mark.parametrize("vendor", ["Racom", "Aviat"])
def test_recorded_microwave_adapter_retains_validated_association(postgres_db, vendor):
    from test_microwave_postgres import network as microwave_network
    from microwave_fixtures import observation as microwave, mapping
    from app.services.microwave_history import stage_microwave
    from app.tower_health.worker import normalize

    db = postgres_db
    d, z, child, link = microwave_network(db, vendor)
    profile = "aviat-microwave-mib-v1" if vendor == "Aviat" else "racom-ray3-mib-v1"
    from app.services.microwave_model import PROFILES

    profile = next(
        p for p in PROFILES if p.startswith("aviat" if vendor == "Aviat" else "racom")
    )
    o = microwave(at=clock(), m=mapping(profile))
    row = stage_microwave(db, d, link, o)
    normalized = normalize(row, d, [])
    assert normalized.link_id == link.id and normalized.source == "recorded_microwave"
    assert (
        normalized.metrics.reported_capacity_mbps
        == o.measurements.reported_capacity_mbps
    )
    assert normalized.metrics.tx_capacity_mbps is None


def test_history_query_jit_setting_is_transaction_local(health):
    from sqlalchemy import text
    from app.tower_health.service import history_rows

    x = health
    before = x.db.scalar(text("SHOW jit"))
    with pytest.raises(RuntimeError, match="rollback local setting"):
        with x.db.begin_nested():
            history_rows(x.db, [x.ap.id], x.now)
            assert x.db.scalar(text("SHOW jit")) == "off"
            raise RuntimeError("rollback local setting")
    assert x.db.scalar(text("SHOW jit")) == before


def test_dense_recent_history_keeps_hourly_temperature_evidence(health):
    from app.tower_health.service import history_rows

    x = health
    for second in [-14400, -10800, -7200]:
        ingest(
            x.db,
            observation(x, second, metrics={"temperature_c": 40.0}, base=False),
            now=x.now,
        )
    for i in range(121):
        ingest(
            x.db,
            observation(x, -1800 + 15 * i, metrics={"temperature_c": 50.0}, base=False),
            now=x.now,
        )
    rows = history_rows(x.db, [x.ap.id], x.now)
    assert 27 <= len(rows) <= 48
    assert {x.now + timedelta(seconds=n) for n in [-14400, -10800, -7200]} <= {
        r.observed_at for r in rows
    }
    decisions = rule_decisions(rows, x.now, Policy())
    assert decisions["rising_temperature"]["state"] == "warning"


def test_batch_ingestion_reuses_transaction_lock_and_policy(health, monkeypatch):
    """Batch optimization must keep validation and savepoints, not per-row locks."""
    from app.tower_health import worker

    x = health
    calls = {"lock": 0, "policy": 0}
    for name in calls:
        original = getattr(worker, name)

        def counted(db, _name=name, _original=original):
            calls[_name] += 1
            return _original(db)

        monkeypatch.setattr(worker, name, counted)
    for i in range(3):
        x.db.add(
            TelemetrySample(
                device_id=x.ap.id,
                sampled_at=x.now - timedelta(seconds=60 - i),
                raw_data={"tower_health_v1": {"metrics": {"temperature_c": 50 + i}}},
            )
        )
    x.db.flush()
    result = consume(x.db, now=x.now, assess_changed=False)
    assert result["processed"] == 3
    assert calls == {"lock": 1, "policy": 1}
    assert x.db.scalar(select(func.count(TowerSample.id))) == 3
    assert consume(x.db, now=x.now, assess_changed=False)["processed"] == 0
    assert x.db.scalar(select(func.count(TowerSample.id))) == 3
