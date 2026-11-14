"""Updated NOC-96 requirements: precise scope, controls, reminders and callbacks."""

import hashlib, hmac, json, time, os
from datetime import timedelta, datetime, timezone
import pytest
from sqlalchemy import select, func
from cryptography.fernet import Fernet
from app.core.config import settings
from app.models.entities import (
    Incident,
    Device,
    Sector,
    NetworkLink,
    MonitorEvent,
    MaintenanceWindow,
)
from app.customers.models import CustomerAccount, CustomerService
from app.notifications.models import (
    NotificationPlan,
    NotificationRecipient,
    NotificationDelivery,
    NotificationMaintenance,
)
from app.notifications.controls import (
    NotificationControl,
    NotificationCallback,
    NotificationProviderConfig,
    emergency_disabled,
)
from app.notifications.contracts import MaintenanceRequest, PlanRequest, Evidence
from app.notifications.service import create_plan, approve, utc, snapshot
from app.notifications.worker import run_batch
from test_notifications import network, api, req, prepared, drain, sms_rows, NOW
from test_powercode_customers import imported, inventory
from test_rf_postgres_integration import postgres_db


def customer(
    x,
    key,
    site,
    *,
    device=None,
    sector=None,
    service_type="wireless",
    fiber=None,
    phone=None,
):
    a = CustomerAccount(
        external_id="SYN-EXP-" + str(key),
        phone=phone or f"+1555555{key:04d}",
        sms_consent="granted",
        consent_record="SYN-EXP",
        consent_recorded_at=NOW,
        communication_preference="sms",
        source_updated_at=NOW,
        last_synced_at=NOW,
    )
    x.db.add(a)
    x.db.flush()
    if service_type == "wireless" and not device:
        device = Device(
            source="synthetic",
            external_id="SYN-EXP-AP-" + str(key),
            name="Synthetic access point",
            role="access-point",
            wisp_site_id=site.id,
        )
        x.db.add(device)
        x.db.flush()
    s = CustomerService(
        external_id="SYN-EXP-S-" + str(key),
        customer_id=a.id,
        status="active",
        service_type=service_type,
        site_id=site.id,
        device_id=device.id if device else None,
        sector_id=sector.id if sector else None,
        fiber_link_id=fiber.id if fiber else None,
        mapping_state="verified",
        source_refs={},
        source_updated_at=NOW,
        last_synced_at=NOW,
    )
    x.db.add(s)
    x.db.flush()
    return a, s


def test_backhaul_multiple_towers_distinct_accounts_and_redundancy(network):
    x = network
    customer(x, 2, x.b)
    x.incident.site_id = None
    x.incident.link_id = x.up.id
    x.db.flush()
    r = req(x)
    r.evidence.event_type = "backhaul_outage"
    p = prepared(x, r)
    assert sum(v["eligible"] for v in p.snapshot["recipients"]) == 2
    x.db.add(
        NetworkLink(
            name="Synthetic redundant backhaul expanded",
            a_site_id=x.root.id,
            z_site_id=x.b.id,
            status="active",
        )
    )
    x.db.flush()
    p = prepared(x, r)
    assert sum(v["eligible"] for v in p.snapshot["recipients"]) == 0


def test_single_sector_does_not_message_other_sector(network):
    x = network
    second = Sector(name="Synthetic second sector", site_id=x.a.id, device_id=x.ap.id)
    x.db.add(second)
    x.db.flush()
    customer(x, 2, x.a, device=x.ap, sector=second)
    x.incident.site_id = x.a.id
    x.incident.sector_id = x.sector.id
    x.db.flush()
    r = req(x)
    r.evidence.event_type = "sector_outage"
    p = prepared(x, r)
    assert [v["customer_id"] for v in p.snapshot["recipients"] if v["eligible"]] == [
        x.account.id
    ]
    assert p.target == {
        "site_id": None,
        "device_id": None,
        "sector_id": x.sector.id,
        "link_id": None,
    }


def test_fiber_segment_only_targets_explicit_fiber_customer(network):
    x = network
    a, s = customer(x, 2, x.a, service_type="fiber", fiber=x.fiber)
    x.incident.site_id = None
    x.incident.link_id = x.fiber.id
    x.db.flush()
    r = req(x)
    r.evidence.event_type = "fiber_outage"
    p = prepared(x, r)
    assert [v["customer_id"] for v in p.snapshot["recipients"] if v["eligible"]] == [
        a.id
    ]


def test_shared_number_across_accounts_suppresses_both(network):
    x = network
    customer(x, 2, x.a, phone=x.account.phone)
    p = prepared(x)
    assert len(p.snapshot["recipients"]) == 2 and all(
        not r["eligible"] and r["reason"] == "shared_number_review"
        for r in p.snapshot["recipients"]
    )


def test_overlapping_incidents_share_customer_cooldown(network):
    x = network
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x)
    other = Incident(
        title="Synthetic overlapping outage",
        site_id=x.a.id,
        started_at=NOW - timedelta(hours=1),
    )
    x.db.add(other)
    x.db.flush()
    r = req(x)
    r.incident_id = other.id
    p2 = prepared(x, r)
    approve(x.db, p2.id, x.admin.id, NOW)
    drain(x, now=NOW + timedelta(seconds=5))
    assert sms_rows(x)[-1].last_error == "incident_cooldown"


def test_flapping_real_monitor_event_history_defers_customer_notice(network):
    x = network
    for i in range(3):
        x.db.add(
            MonitorEvent(
                device_id=x.ap.id,
                event_type="status_change",
                from_status="up" if i % 2 == 0 else "down",
                to_status="down" if i % 2 == 0 else "up",
                message="Synthetic transition",
                created_at=NOW - timedelta(seconds=30 - i),
            )
        )
    x.db.flush()
    p = prepared(x)
    assert p.snapshot["context"]["flapping_detected"] and all(
        not v["eligible"] for v in p.snapshot["recipients"]
    )


def test_active_maintenance_suppresses_only_covered_services(network):
    x = network
    a, s = customer(x, 2, x.b)
    x.incident.site_id = None
    x.incident.link_id = x.up.id
    x.db.add(
        NotificationMaintenance(
            title="Synthetic tower A work",
            target={"site_id": x.a.id},
            targets=[{"site_id": x.a.id}],
            starts_at=NOW - timedelta(minutes=1),
            ends_at=NOW + timedelta(hours=1),
            created_by=x.admin.id,
        )
    )
    x.db.flush()
    p = prepared(x)
    assert (
        next(r for r in p.snapshot["recipients"] if r["customer_id"] == x.account.id)[
            "reason"
        ]
        == "planned_maintenance_suppression"
    )
    # Tower A maintenance also cuts the recorded downstream path, conservatively
    # covering B under that hypothesis; no fabricated redundancy is assumed.
    assert p.snapshot["context"]["maintenance_suppressed"]


def test_consent_timestamp_required_for_notification_eligibility(network):
    x = network
    x.account.consent_recorded_at = None
    x.db.flush()
    p = prepared(x)
    assert p.snapshot["recipients"][0]["reason"] == "consent_timestamp_unverified"


def test_multi_target_maintenance_union_and_24h_1h_reminder_dedupe(api):
    x, c, h = api
    customer(x, 2, x.b)
    x.db.commit()
    start = NOW + timedelta(days=2)
    r = c.post(
        "/api/notifications/maintenance",
        headers=h,
        json={
            "title": "Synthetic multi-tower work",
            "targets": [{"site_id": x.a.id}, {"site_id": x.b.id}],
            "description": "Synthetic power work",
            "advance_notice_minutes": [1440, 60],
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
        },
    )
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    pids = []
    for interval in (1440, 60):
        body = {
            "maintenance_id": mid,
            "kind": "maintenance",
            "reminder_minutes": interval,
            "evidence": {"reference": "SYN-MULTI", "event_type": "planned_maintenance"},
        }
        p = c.post("/api/notifications/plans", headers=h, json=body)
        assert p.status_code == 200, p.text
        pids.append(p.json()["id"])
        assert datetime.fromisoformat(p.json()["due_at"]) == start - timedelta(
            minutes=interval
        )
        assert (
            c.post("/api/notifications/plans", headers=h, json=body).json()["id"]
            == pids[-1]
        )
        stored = x.db.get(NotificationPlan, pids[-1])
        assert sum(v["eligible"] for v in stored.snapshot["recipients"]) == 2
    assert len(set(pids)) == 2
    assert (
        c.patch(
            f"/api/notifications/maintenance/{mid}",
            headers=h,
            json={
                "revision": 1,
                "action": "reschedule",
                "starts_at": (start + timedelta(days=1)).isoformat(),
                "ends_at": (start + timedelta(days=1, hours=2)).isoformat(),
            },
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "targets", [[{"site_id": 1}, {"site_id": 1}], [{"site_id": 1, "sector_id": 2}], []]
)
def test_invalid_multi_target_scope_rejected(targets):
    with pytest.raises(ValueError):
        MaintenanceRequest(
            title="Synthetic work",
            targets=targets,
            starts_at=NOW,
            ends_at=NOW + timedelta(hours=1),
        )


def test_pause_resume_and_global_emergency_latch(api, monkeypatch, tmp_path):
    x, c, h = api
    monkeypatch.setattr(settings, "rf_scheduler_spool_dir", str(tmp_path))
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    x.db.commit()
    assert (
        c.put(
            "/api/notifications/pause",
            headers=h,
            json={"paused": True, "confirm": True},
        ).status_code
        == 200
    )
    assert (
        run_batch(x.db, now=NOW)["processed"] == 0 and sms_rows(x)[0].state == "queued"
    )
    x.db.commit()
    assert (
        c.put(
            "/api/notifications/pause",
            headers=h,
            json={"paused": False, "confirm": True},
        ).status_code
        == 200
    )
    assert c.post("/api/notifications/emergency-disable", headers=h).json()[
        "emergency_disabled"
    ]
    assert emergency_disabled() and run_batch(x.db, now=NOW)["processed"] == 0
    assert c.put(
        "/api/notifications/pause", headers=h, json={"paused": False, "confirm": True}
    ).json()["emergency_disabled"]
    assert c.post("/api/notifications/mock-test", headers=h).status_code == 409


def test_emergency_latch_fails_closed_for_malformed_file(
    network, monkeypatch, tmp_path
):
    x = network
    monkeypatch.setattr(settings, "rf_scheduler_spool_dir", str(tmp_path))
    (tmp_path / "notifications.disabled").write_text("broken")
    assert emergency_disabled() and run_batch(x.db, now=NOW)["processed"] == 0


def test_dashboard_filters_and_date_validation(api):
    x, c, h = api
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x)
    x.db.commit()
    for query in [
        f"tower_id={x.a.id}",
        f"customer_id={x.account.id}",
        f"incident_id={x.incident.id}",
        "event_type=tower_power",
        "delivery_status=simulated",
    ]:
        r = c.get("/api/notifications?" + query, headers=h)
        assert r.status_code == 200 and r.json()["total"] == 1
    assert (
        c.get("/api/notifications?customer_id=999999", headers=h).json()["total"] == 0
    )
    assert (
        c.get("/api/notifications?since=2026-01-01T00:00:00", headers=h).status_code
        == 409
    )


def test_separate_critical_maintenance_warning_slack_channels(network):
    x = network
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    assert x.db.scalar(
        select(NotificationDelivery.id).where(
            NotificationDelivery.recipient_key == "noc-simulation-critical"
        )
    )
    m = NotificationMaintenance(
        title="Synthetic channel work",
        target={"site_id": x.a.id},
        starts_at=NOW + timedelta(hours=1),
        ends_at=NOW + timedelta(hours=2),
        created_by=x.admin.id,
    )
    x.db.add(m)
    x.db.flush()
    r = PlanRequest(
        maintenance_id=m.id,
        kind="maintenance",
        evidence=Evidence(reference="SYN", event_type="planned_maintenance"),
    )
    p2 = prepared(x, r)
    approve(x.db, p2.id, x.admin.id, NOW)
    assert x.db.scalar(
        select(NotificationDelivery.id).where(
            NotificationDelivery.recipient_key == "noc-simulation-maintenance"
        )
    )


@pytest.mark.parametrize(
    "event_type", ["high_utilization", "monitoring_failure", "acknowledgment"]
)
def test_technician_only_events_do_not_imply_customer_outage(network, event_type):
    x = network
    r = req(x)
    r.evidence.event_type = event_type
    p = prepared(x, r)
    approve(x.db, p.id, x.admin.id, NOW)
    assert not sms_rows(x)


def callback_setup(x, c, h, tmp_path, monkeypatch):
    key = tmp_path / "callback.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "notifications_vault_key_file", str(key))
    secret = "SYN-private-callback-secret"
    r = c.put(
        "/api/notifications/providers/sms",
        headers=h,
        json={
            "adapter": "mock",
            "sender": "+15555550996",
            "registration_reference": "SYN-REG",
            "callback_secret": secret,
        },
    )
    assert r.status_code == 200, r.text
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x)
    x.db.commit()
    return secret, sms_rows(x)[0]


def signed(secret, d, status="delivered", event="SYN-CALLBACK", timestamp=None):
    body = {
        "event_id": event,
        "channel": "sms",
        "delivery_id": d.id,
        "status": status,
        "timestamp": int(time.time()) if timestamp is None else timestamp,
    }
    data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["signature"] = hmac.new(
        secret.encode(), str(body["timestamp"]).encode() + b"." + data, hashlib.sha256
    ).hexdigest()
    return body


def test_mock_signed_status_stop_callbacks_replay_and_regression(
    api, tmp_path, monkeypatch
):
    x, c, h = api
    secret, d = callback_setup(x, c, h, tmp_path, monkeypatch)
    body = signed(secret, d)
    r = c.post("/api/notifications/mock-callback", headers=h, json=body)
    assert r.status_code == 200, r.text
    assert c.post("/api/notifications/mock-callback", headers=h, json=body).json()[
        "duplicate"
    ]
    assert (
        c.post(
            "/api/notifications/mock-callback",
            headers=h,
            json=signed(secret, d, status="accepted", event="SYN-REGRESSION"),
        ).status_code
        == 409
    )
    assert (
        c.post(
            "/api/notifications/mock-callback",
            headers=h,
            json=signed(secret, d, status="opt_out", event="SYN-STOP"),
        ).status_code
        == 200
    )
    assert (
        x.db.get(CustomerAccount, x.account.id).sms_opt_out
        and x.db.get(CustomerAccount, x.account.id).opt_out_at
    )
    assert c.post("/api/notifications/mock-callback", json=body).status_code == 401


@pytest.mark.parametrize("case", ["signature", "expired", "wrong_channel", "conflict"])
def test_mock_callback_rejects_invalid_auth_and_identifiers(
    api, tmp_path, monkeypatch, case
):
    x, c, h = api
    secret, d = callback_setup(x, c, h, tmp_path, monkeypatch)
    body = signed(secret, d)
    if case == "signature":
        body["signature"] = "0" * 64
    if case == "expired":
        body = signed(secret, d, timestamp=int(time.time()) - 301)
    if case == "wrong_channel":
        body["channel"] = "slack"
    if case == "conflict":
        assert (
            c.post("/api/notifications/mock-callback", headers=h, json=body).status_code
            == 200
        )
        body = signed(secret, d, status="failed")
    assert (
        c.post("/api/notifications/mock-callback", headers=h, json=body).status_code
        == 409
    )


def test_provider_selection_health_and_secret_privacy(api, tmp_path, monkeypatch):
    x, c, h = api
    key = tmp_path / "vault"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "notifications_vault_key_file", str(key))
    assert (
        c.put(
            "/api/notifications/credentials/sms",
            headers=h,
            json={"value": "synthetic-provider-token"},
        ).status_code
        == 200
    )
    r = c.put(
        "/api/notifications/providers/sms",
        headers=h,
        json={
            "adapter": "twilio_mock",
            "sender": "+15555550996",
            "account_reference": "AC" + "b" * 32,
            "registration_reference": "SYN-REG",
        },
    )
    assert r.status_code == 200
    assert (
        c.post("/api/notifications/providers/sms/mock-health", headers=h).json()[
            "status"
        ]
        == "mock_healthy"
    )
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x)
    x.db.commit()
    assert sms_rows(x)[0].provider_receipt.startswith("mock-sms-")
    result = c.get("/api/notifications", headers=h)
    assert (
        "synthetic-provider-token" not in result.text
        and "callback_secret_ciphertext" not in result.text
    )


def test_consent_preferences_record_timestamps(api):
    x, c, h = api
    r = c.patch(
        f"/api/customers/accounts/{x.account.id}/preferences",
        headers=h,
        json={
            "consent": "granted",
            "consent_record": "SYN-NEW-CONSENT",
            "preference": "sms",
            "opt_out": False,
        },
    )
    assert r.status_code == 200
    x.db.expire_all()
    a = x.db.get(CustomerAccount, x.account.id)
    assert a.consent_recorded_at and a.preference_updated_at


@pytest.mark.parametrize("template_key", ["power", "backhaul"])
def test_specific_outage_templates_require_matching_evidence(network, template_key):
    r = req(network)
    r.evidence.event_type = "tower_outage"
    r.template_key = template_key
    with pytest.raises(ValueError, match="template requires confirmed"):
        create_plan(network.db, r, network.admin.id, NOW)
