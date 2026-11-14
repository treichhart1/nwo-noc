"""Synthetic evidence, topology, privacy, recovery and notification contracts."""

from datetime import timedelta
import pytest
from sqlalchemy import select, func
from pydantic import ValidationError
from app.down_detector.contracts import Observation, DetectionPolicy
from app.down_detector.engine import ingest, reconcile, classify, impact
from app.down_detector.models import (
    OutageState,
    OutageEpisode,
    OutageObservation,
    OutageTimeline,
)
from app.down_detector.worker import consume
from app.models.entities import Incident, NetworkLink, TelemetrySample, Device
from app.notifications.models import NotificationPlan, NotificationDelivery
from app.core.config import settings
from test_notifications import network, api, NOW
from test_powercode_customers import imported, inventory
from test_rf_postgres_integration import postgres_db

BAD = {"icmp": False, "snmp": False, "collector": True}
GOOD = {"icmp": True, "snmp": True, "collector": True}


def obs(x, kind="device", ident=None, signals=None, second=-60, event=None, **kw):
    ident = ident or x.ap.id
    return Observation(
        event_id=event or f"SYN:{kind}:{ident}:{second}",
        kind=kind,
        target_id=ident,
        source="synthetic",
        observed_at=NOW + timedelta(seconds=second),
        signals=signals if signals is not None else BAD,
        **kw,
    )


def fail(x, kind="device", ident=None, signals=None):
    for second in [-120, -90, -60]:
        ingest(x.db, obs(x, kind, ident, signals, second), now=NOW)
    return x.db.scalar(
        select(OutageEpisode).where(OutageEpisode.key == f"{kind}:{ident or x.ap.id}")
    )


def recover(x, kind="device", ident=None):
    for second in [-50, -20, 10]:
        ingest(x.db, obs(x, kind, ident, GOOD, second), now=NOW + timedelta(seconds=10))


@pytest.mark.parametrize(
    "kind,signals,classification",
    [
        ("device", BAD, "device_offline"),
        ("sector", BAD, "sector_outage"),
        ("site", BAD, "unknown_tower_connectivity_loss"),
        (
            "site",
            {**BAD, "commercial_power": False},
            "confirmed_commercial_power_outage",
        ),
        ("site", {**BAD, "ups": False}, "suspected_power_failure"),
        (
            "link",
            {"microwave": False, "interface": False, "collector": True},
            "backhaul_outage",
        ),
        (
            "link",
            {"fiber": False, "interface": False, "collector": True},
            "fiber_outage",
        ),
        (
            "device",
            {"ospf": False, "interface": False, "collector": True},
            "network_path_unavailable",
        ),
        ("device", {"collector": False}, "collector_failure"),
        (
            "device",
            {"icmp": False, "telemetry": True, "collector": True},
            "management_connectivity_failure",
        ),
        (
            "device",
            {"wireless": False, "interface": True, "collector": True},
            "partial_service_degradation",
        ),
    ],
)
def test_sustained_classifications(network, kind, signals, classification):
    x = network
    ident = {"device": x.ap.id, "site": x.a.id, "link": x.up.id, "sector": x.sector.id}[
        kind
    ]
    e = fail(x, kind, ident, signals)
    assert e.classification == classification
    reconcile(x.db, now=NOW)
    assert e.incident_id
    assert e.confidence != "confirmed" or signals.get("commercial_power") is False


def test_temporary_loss_flapping_duplicate_and_reordered_samples(network):
    x = network
    o = obs(x, second=-90)
    ingest(x.db, o, now=NOW)
    assert ingest(x.db, o, now=NOW)["duplicate"]
    assert x.db.scalar(select(func.count(OutageObservation.id))) == 1
    with pytest.raises(ValueError, match="Conflicting"):
        ingest(x.db, o.model_copy(update={"signals": GOOD}), now=NOW)
    ingest(x.db, obs(x, signals=GOOD, second=-60), now=NOW)
    assert ingest(x.db, obs(x, second=-70), now=NOW)["late"]
    ingest(x.db, obs(x, second=-30), now=NOW)
    assert not x.db.scalar(select(OutageEpisode))


@pytest.mark.parametrize(
    "signals", [{}, {"icmp": False}, {"collector": None}, {"commercial_power": None}]
)
def test_missing_single_signal_never_becomes_outage(network, signals):
    assert fail(network, signals=signals) is None


@pytest.mark.parametrize("delta", [-181, 1])
def test_stale_future_rejected(network, delta):
    with pytest.raises(ValueError, match="stale or in the future"):
        ingest(network.db, obs(network, second=delta), now=NOW)


@pytest.mark.parametrize(
    "bad",
    [
        {"signals": {"password": False}},
        {"signals": {"icmp": 1}},
        {"packet_loss_percent": 101},
        {"voltage": -1},
        {"uptime_seconds": float("nan")},
        {"observed_at": "2026-11-14T00:00:00"},
        {"event_id": "https://secret.invalid"},
    ],
)
def test_closed_bounded_contract(network, bad):
    with pytest.raises(ValidationError):
        Observation.model_validate({**obs(network).model_dump(), **bad})


def test_backhaul_correlates_downstream_without_duplicate_incidents(network):
    x = network
    root = fail(x, "link", x.up.id)
    child = fail(x)
    report = reconcile(x.db, now=NOW)
    assert child.parent_id == root.id and child.incident_id == root.incident_id
    assert report["primary_incidents"] == 1 and report["symptoms"] == 1
    reconcile(x.db, now=NOW)
    assert (
        x.db.scalar(select(func.count(Incident.id))) == 2
    )  # fixture incident + one root
    assert impact(x.db, root)["potential_customer_count"] == 1


def test_redundant_recorded_path_prevents_false_parent(network):
    x = network
    x.db.add(
        NetworkLink(
            name="SYN alternate", a_site_id=x.root.id, z_site_id=x.a.id, status="active"
        )
    )
    x.db.flush()
    root = fail(x, "link", x.up.id)
    child = fail(x)
    reconcile(x.db, now=NOW)
    assert child.parent_id is None and child.incident_id != root.incident_id
    assert impact(x.db, root)["service_counts"]["not_affected"] == 1


def test_independent_and_unknown_topology_not_forced_to_common_cause(network):
    x = network
    x.a.site_type = "tower"
    x.root.site_type = "tower"
    x.db.flush()
    root = fail(x, "link", x.up.id)
    child = fail(x)
    reconcile(x.db, now=NOW)
    assert child.parent_id is None


def test_stable_partial_recovery_preserves_root_until_child_recovers(network):
    x = network
    root = fail(x, "link", x.up.id)
    child = fail(x)
    reconcile(x.db, now=NOW)
    recover(x, "link", x.up.id)
    reconcile(x.db, now=NOW + timedelta(seconds=10))
    assert (
        root.status == "resolved"
        and x.db.get(Incident, root.incident_id).status == "open"
    )
    assert child.parent_id == root.id
    recover(x)
    reconcile(x.db, now=NOW + timedelta(seconds=10))
    assert x.db.get(Incident, root.incident_id).status == "resolved"


def test_missing_collector_and_single_success_cannot_close(network):
    x = network
    e = fail(x)
    reconcile(x.db, now=NOW)
    ingest(x.db, obs(x, signals=GOOD, second=-20), now=NOW)
    ingest(x.db, obs(x, signals={"collector": None}, second=-10), now=NOW)
    assert e.status == "active" and x.db.get(Incident, e.incident_id).status == "open"


def test_reboot_evidence_is_not_power_proof(network):
    x = network
    ingest(x.db, obs(x, signals=GOOD, second=-30, uptime_seconds=1000), now=NOW)
    ingest(x.db, obs(x, signals=GOOD, second=0, uptime_seconds=10), now=NOW)
    assert x.db.get(OutageState, f"device:{x.ap.id}").evidence["reboot_detected"]
    assert not x.db.scalar(select(OutageEpisode))


def test_worker_replay_restart_and_transaction_rollback(network):
    x = network
    for i in [-120, -90, -60]:
        x.db.add(
            TelemetrySample(
                device_id=x.ap.id,
                sampled_at=NOW + timedelta(seconds=i),
                raw_data={"down_detector_v1": {"signals": BAD}},
            )
        )
    x.db.flush()
    with x.db.begin_nested() as t:
        assert consume(x.db, now=NOW)["processed"] == 3
        t.rollback()
    assert not x.db.scalar(select(OutageEpisode))
    assert consume(x.db, now=NOW)["processed"] == 3
    x.db.expire_all()
    assert consume(x.db, now=NOW)["processed"] == 0
    assert x.db.scalar(select(func.count(OutageEpisode.id))) == 1


def test_dashboard_timeline_override_notification_and_privacy(api):
    x, c, h = api
    e = fail(x)
    reconcile(x.db, now=NOW)
    x.db.commit()
    r = c.get("/api/down-detector", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    detail = c.get(f"/api/down-detector/episodes/{e.id}", headers=h)
    assert detail.status_code == 200, detail.text
    assert "phone" not in detail.text and "+1555" not in detail.text
    r = c.post(
        f"/api/down-detector/episodes/{e.id}/notification-preview", headers=h, json={}
    )
    assert r.status_code == 200, r.text
    p = x.db.get(NotificationPlan, r.json()["plan_id"])
    assert p.status == "draft"
    assert not x.db.scalar(select(NotificationDelivery.id))
    assert (
        c.post(
            f"/api/down-detector/episodes/{e.id}/notification-preview",
            headers=h,
            json={},
        ).json()["plan_id"]
        == p.id
    )
    r = c.post(
        f"/api/down-detector/episodes/{e.id}/operator-action",
        headers=h,
        json={
            "action": "acknowledge",
            "explanation": "Synthetic operator acknowledgment",
        },
    )
    assert r.status_code == 200, r.text
    r = c.post(
        f"/api/down-detector/episodes/{e.id}/operator-action",
        headers=h,
        json={
            "action": "override",
            "explanation": "Synthetic review of management evidence",
            "classification": "management_connectivity_failure",
        },
    )
    assert r.status_code == 200, r.text
    assert e.confidence == "operator_assessment"
    assert (
        c.post(
            f"/api/down-detector/episodes/{e.id}/operator-action",
            headers=h,
            json={
                "action": "override",
                "explanation": "          ",
                "classification": "device_offline",
            },
        ).status_code
        == 409
    )
    assert (
        c.get("/api/down-detector/history", headers=h).json()["availability_percent"]
        is None
    )
    assert c.get("/api/down-detector?device_id=999999", headers=h).json()["total"] == 0


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "", None),
        ("post", "/synthetic-observations", []),
        (
            "post",
            "/episodes/1/operator-action",
            {"action": "acknowledge", "explanation": "Synthetic denial test"},
        ),
    ],
)
def test_unauthorized_access_audited(api, method, path, body):
    x, c, h = api
    x.admin.role = "viewer"
    x.db.commit()
    r = c.request(method, "/api/down-detector" + path, headers=h, json=body)
    assert r.status_code == 403


def test_synthetic_ingress_requires_explicit_staging(api, monkeypatch):
    x, c, h = api
    monkeypatch.setattr(settings, "synthetic_monitoring_enabled", False)
    r = c.post(
        "/api/down-detector/synthetic-observations",
        headers=h,
        json=[obs(x).model_dump(mode="json")],
    )
    assert r.status_code == 409


def test_parent_device_chain_and_cycles(network):
    x = network
    upstream = Device(
        source="synthetic",
        external_id="SYN-N97-parent",
        name="Synthetic aggregation",
        wisp_site_id=x.a.id,
    )
    x.db.add(upstream)
    x.db.flush()
    x.ap.parent_device_id = upstream.id
    root = fail(x, "device", upstream.id)
    child = fail(x)
    reconcile(x.db, now=NOW)
    assert child.parent_id == root.id
    upstream.parent_device_id = x.ap.id
    x.db.flush()
    reconcile(x.db, now=NOW)
    assert child.parent_id is None and root.parent_id is None


@pytest.mark.parametrize("maintenance_status", ["scheduled", "in_progress", "active"])
def test_primary_preview_dedupe_and_maintenance_suppression(api, maintenance_status):
    from app.models.entities import MaintenanceWindow

    x, c, h = api
    from app.notifications.models import NotificationPolicy

    pol = x.db.get(NotificationPolicy, 1)
    pol.config = {**pol.config, "minimum_outage_seconds": 60}
    root = fail(x, "link", x.up.id)
    child = fail(x)
    reconcile(x.db, now=NOW)
    x.db.add(
        MaintenanceWindow(
            title="SYN planned outage",
            site_id=x.a.id,
            starts_at=NOW - timedelta(hours=1),
            ends_at=NOW + timedelta(hours=1),
            status=maintenance_status,
        )
    )
    x.db.commit()
    assert (
        c.post(
            f"/api/down-detector/episodes/{child.id}/notification-preview",
            headers=h,
            json={},
        ).status_code
        == 409
    )
    r = c.post(
        f"/api/down-detector/episodes/{root.id}/notification-preview",
        headers=h,
        json={},
    )
    assert r.status_code == 200, r.text
    p = c.get(f"/api/notifications/plans/{r.json()['plan_id']}", headers=h).json()
    assert p["recipient_counts"].get("eligible", 0) == 0
    assert any(
        r["reason"] == "planned_maintenance_suppression" for r in p["recipients"]
    )
    assert not x.db.scalar(select(NotificationDelivery.id))


def test_power_loss_with_working_telemetry_is_not_customer_outage(api):
    x, c, h = api
    e = fail(
        x,
        "site",
        x.a.id,
        {"commercial_power": False, "telemetry": True, "collector": True},
    )
    reconcile(x.db, now=NOW)
    x.db.commit()
    r = c.post(
        f"/api/down-detector/episodes/{e.id}/notification-preview", headers=h, json={}
    )
    assert r.status_code == 200, r.text
    plan = x.db.get(NotificationPlan, r.json()["plan_id"])
    assert plan.snapshot["request"]["evidence"]["event_type"] == "ups_warning"
    assert x.db.get(OutageState, e.key).state == "degraded"
    assert not any(r["eligible"] for r in plan.snapshot["recipients"])


def test_native_router_recorded_bridge_routes_without_claiming_power(network):
    x = network
    for i in [-120, -90, -60]:
        x.db.add(
            TelemetrySample(
                device_id=x.ap.id,
                sampled_at=NOW + timedelta(seconds=i),
                raw_data={
                    "mikrotik_v1": {
                        "uptime_seconds": 1000 + i,
                        "interfaces": [],
                        "ospf_states": ["down"],
                    }
                },
            )
        )
    x.db.flush()
    assert consume(x.db, now=NOW)["processed"] == 3
    e = x.db.scalar(select(OutageEpisode))
    assert e.classification == "partial_service_degradation"
    assert x.db.get(OutageState, e.key).evidence["signals"]["ospf"] is False


def test_missing_root_evidence_preserves_correlated_symptoms(network):
    x = network
    root = fail(x, "link", x.up.id)
    child = fail(x)
    reconcile(x.db, now=NOW)
    ingest(x.db, obs(x, "link", x.up.id, {"collector": None}, second=-20), now=NOW)
    reconcile(x.db, now=NOW)
    assert child.parent_id == root.id and child.incident_id == root.incident_id


def test_raw_event_history_and_unverified_restoration_rejection(api):
    x, c, h = api
    e = fail(x)
    reconcile(x.db, now=NOW)
    x.db.commit()
    r = c.get(f"/api/down-detector/episodes/{e.id}/observations", headers=h)
    assert r.status_code == 200 and len(r.json()["items"]) == 3
    assert (
        c.post(
            f"/api/down-detector/episodes/{e.id}/notification-preview",
            headers=h,
            json={"kind": "restoration"},
        ).status_code
        == 409
    )


def test_duplicate_recorded_sample_times_do_not_poison_worker_queue(network):
    x = network
    for _ in range(2):
        x.db.add(
            TelemetrySample(
                device_id=x.ap.id,
                sampled_at=NOW - timedelta(seconds=60),
                raw_data={"down_detector_v1": {"signals": BAD}},
            )
        )
    x.db.flush()
    assert consume(x.db, now=NOW)["processed"] == 2
    assert consume(x.db, now=NOW)["processed"] == 0
    assert x.db.get(OutageState, f"device:{x.ap.id}").bad_count == 1
    assert x.db.scalar(select(func.count(OutageObservation.id))) == 2
    assert not x.db.scalar(select(OutageEpisode))


def test_conflicting_same_time_evidence_is_unknown(network):
    x = network
    o = obs(x, second=-30)
    ingest(x.db, o, now=NOW)
    ingest(
        x.db,
        o.model_copy(update={"event_id": "SYN-CONFLICT-TIME", "signals": GOOD}),
        now=NOW,
    )
    st = x.db.get(OutageState, f"device:{x.ap.id}")
    assert st.state == "unknown" and st.bad_count == st.good_count == 0
    assert x.db.scalar(select(func.count(OutageObservation.id))) == 2


def test_malformed_stored_payloads_are_isolated_without_poisoning_queue(network):
    x = network
    for i, payload in enumerate([[], "malformed", {"rf_radio_v2": "malformed"}]):
        x.db.add(
            TelemetrySample(
                device_id=x.ap.id,
                sampled_at=NOW - timedelta(seconds=60 - i),
                raw_data=payload,
            )
        )
    x.db.flush()
    assert consume(x.db, now=NOW)["processed"] == 3
    assert consume(x.db, now=NOW)["processed"] == 0
    assert x.db.get(OutageState, f"device:{x.ap.id}").state == "unknown"
    assert not x.db.scalar(select(OutageEpisode))


@pytest.mark.parametrize(
    "kind,signals,expected",
    [
        (
            "link",
            {"fiber": False, "interface": False, "collector": True},
            "fiber_outage",
        ),
        ("device", {"collector": False}, "monitoring_failure"),
    ],
)
def test_notification_preview_preserves_observed_failure_type(
    api, kind, signals, expected
):
    x, c, h = api
    ident = x.fiber.id if kind == "link" else x.ap.id
    e = fail(x, kind, ident, signals)
    reconcile(x.db, now=NOW)
    x.db.commit()
    r = c.post(
        f"/api/down-detector/episodes/{e.id}/notification-preview", headers=h, json={}
    )
    assert r.status_code == 200, r.text
    assert (
        x.db.get(NotificationPlan, r.json()["plan_id"]).snapshot["request"]["evidence"][
            "event_type"
        ]
        == expected
    )


def test_long_stable_recovery_count_fits_notification_evidence_contract(api):
    from app.down_detector.engine import clock

    x, c, h = api
    from app.notifications.models import NotificationPolicy
    from app.notifications.service import approve
    from app.notifications.worker import run_batch

    pol = x.db.get(NotificationPolicy, 1)
    pol.config = {**pol.config, "minimum_outage_seconds": 60}
    at = clock()
    for second in [-170, -140, -110]:
        ingest(
            x.db,
            obs(x, second=second).model_copy(
                update={"observed_at": at + timedelta(seconds=second)}
            ),
            now=at,
        )
    reconcile(x.db, now=at)
    e = x.db.scalar(select(OutageEpisode))
    x.db.commit()
    notice = c.post(
        f"/api/down-detector/episodes/{e.id}/notification-preview", headers=h, json={}
    )
    assert notice.status_code == 200, notice.text
    approve(x.db, notice.json()["plan_id"], x.admin.id, clock())
    run_batch(x.db, now=clock())
    x.db.commit()
    for second in [-90, -60, -30]:
        ingest(
            x.db,
            obs(x, signals=GOOD, second=second).model_copy(
                update={"observed_at": at + timedelta(seconds=second)}
            ),
            now=at,
        )
    reconcile(x.db, now=at)
    x.db.get(OutageState, e.key).good_count = 101
    x.db.commit()
    r = c.post(
        f"/api/down-detector/episodes/{e.id}/notification-preview",
        headers=h,
        json={"kind": "restoration"},
    )
    assert r.status_code == 200, r.text
    assert (
        x.db.get(NotificationPlan, r.json()["plan_id"]).snapshot["request"]["evidence"][
            "recovery_samples"
        ]
        == 100
    )

    assert any(
        r["eligible"]
        for r in x.db.get(NotificationPlan, r.json()["plan_id"]).snapshot["recipients"]
    )


def test_original_failed_power_and_routing_signals_require_explicit_recovery(network):
    x = network
    e = fail(
        x,
        "site",
        x.a.id,
        {"commercial_power": False, "telemetry": True, "collector": True},
    )
    assert x.db.get(OutageState, e.key).state == "degraded"
    for second in [-50, -20, 10]:
        ingest(
            x.db, obs(x, "site", x.a.id, GOOD, second), now=NOW + timedelta(seconds=10)
        )
    assert e.status == "active"
    assert x.db.get(OutageState, e.key).classification == "incomplete_recovery_evidence"
    for second in [20, 50, 80]:
        ingest(
            x.db,
            obs(x, "site", x.a.id, {**GOOD, "commercial_power": True}, second),
            now=NOW + timedelta(seconds=80),
        )
    assert e.status == "resolved"


@pytest.mark.parametrize(
    "signals,state,kind",
    [
        ({"commercial_power": False}, "degraded", "confirmed_commercial_power_outage"),
        (
            {**GOOD, "commercial_power": False},
            "degraded",
            "confirmed_commercial_power_outage",
        ),
        (
            {**BAD, "commercial_power": False},
            "bad",
            "confirmed_commercial_power_outage",
        ),
        (
            {**BAD, "telemetry": True, "commercial_power": False},
            "degraded",
            "confirmed_commercial_power_outage",
        ),
        ({**GOOD, "ups": False}, "degraded", "backup_power_warning"),
        ({**GOOD, "battery": False}, "degraded", "backup_power_warning"),
        ({**GOOD, "power_supply": False}, "degraded", "backup_power_warning"),
        ({**BAD, "ups": False}, "bad", "suspected_power_failure"),
        ({**GOOD, "commercial_power": True}, "good", "healthy"),
        ({**GOOD, "commercial_power": None}, "good", "healthy"),
        ({}, "unknown", "missing_signals"),
    ],
)
def test_power_state_matrix_without_invented_liveness(signals, state, kind):
    o = Observation(
        event_id="SYN-POWER-MATRIX",
        kind="site",
        target_id=1,
        source="synthetic",
        observed_at=NOW,
        signals=signals,
    )
    result = classify(o, DetectionPolicy())
    assert result[:2] == (state, kind)


def test_cause_change_does_not_forget_unresolved_power_failure(network):
    x = network
    e = fail(
        x,
        "site",
        x.a.id,
        {"commercial_power": False, "telemetry": True, "collector": True},
    )
    for second in [-50, -20, 10]:
        ingest(
            x.db,
            obs(x, "site", x.a.id, {"collector": False}, second),
            now=NOW + timedelta(seconds=10),
        )
    assert e.classification == "collector_failure"
    assert set(e.evidence["required_recovery_signals"]) == {
        "collector",
        "commercial_power",
    }
    for second in [20, 50, 80]:
        ingest(
            x.db, obs(x, "site", x.a.id, GOOD, second), now=NOW + timedelta(seconds=80)
        )
    assert e.status == "active"
    for second in [90, 120, 150]:
        ingest(
            x.db,
            obs(x, "site", x.a.id, {**GOOD, "commercial_power": True}, second),
            now=NOW + timedelta(seconds=150),
        )
    assert e.status == "resolved"
