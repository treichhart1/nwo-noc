"""NOC-96: synthetic source/recipient, durable mock queue and privacy acceptance."""

import json, copy, hmac, hashlib, os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import httpx, pytest
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from app.main import app
from app.core.config import settings, Settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.entities import Incident, User, AuditLog, NetworkLink
from app.customers.models import CustomerAccount, CustomerService
from app.notifications.models import (
    NotificationPolicy,
    NotificationPlan,
    NotificationRecipient,
    NotificationDelivery,
    NotificationAttempt,
    NotificationMaintenance,
    NotificationSuppression,
    NotificationCredential,
)
from app.notifications.contracts import (
    Policy,
    PlanRequest,
    Evidence,
    MaintenanceRequest,
)
from app.notifications.service import (
    create_plan,
    approve,
    snapshot,
    quiet_due,
    validate_template,
    clock,
    utc,
    probable_upstream,
)
from app.notifications.worker import run_batch, discover
from app.notifications.providers import (
    MockSender,
    MockSlack,
    DeliveryFailure,
    verified_callback,
)
from app.notifications.secrets import seal, unseal
from test_rf_postgres_integration import postgres_db
from test_powercode_customers import imported, inventory

NOW = datetime.now(timezone.utc)


@pytest.fixture
def network(imported, monkeypatch):
    x = imported
    monkeypatch.setattr(settings, "offline_mode", True)
    monkeypatch.setattr(settings, "app_env", "staging")
    x.account.consent_recorded_at = NOW
    x.account.sms_consent = "granted"
    x.account.communication_preference = "sms"
    x.account.consent_record = "SYN-CONSENT"
    x.admin = User(
        username="noc96-admin",
        email="noc96@example.invalid",
        password_hash=hash_password("synthetic-only-notification"),
        role="admin",
        is_active=True,
    )
    x.db.add(x.admin)
    x.incident = Incident(
        title="Synthetic tower power interruption",
        site_id=x.a.id,
        severity="critical",
        started_at=NOW - timedelta(hours=1),
    )
    x.db.add(x.incident)
    x.db.add(
        NotificationPolicy(
            id=1,
            config=Policy(
                quiet_start=(NOW.hour + 12) % 24,
                quiet_end=(NOW.hour + 13) % 24,
                timezone="UTC",
            ).model_dump(),
        )
    )
    x.db.flush()
    return x


def req(x, **kwargs):
    return PlanRequest(
        incident_id=x.incident.id,
        evidence=Evidence(
            reference="SYN-OBSERVATION", event_type="tower_power", confirmed=True
        ),
        **kwargs,
    )


def prepared(x, request=None):
    return create_plan(x.db, request or req(x), x.admin.id, NOW)


def drain(x, **kwargs):
    return run_batch(x.db, now=kwargs.pop("now", NOW), **kwargs)


def sms_rows(x):
    return list(
        x.db.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.channel == "sms")
            .order_by(NotificationDelivery.id)
        )
    )


@pytest.mark.parametrize(
    "event_type",
    [
        "backhaul_outage",
        "severe_degradation",
        "tower_power",
        "tower_outage",
        "ap_outage",
        "sector_outage",
        "fiber_outage",
        "equipment_failure",
    ],
)
def test_confirmed_network_events_prepare_targeted_sms(network, event_type):
    x = network
    r = req(x)
    r.evidence.event_type = event_type
    p = prepared(x, r)
    assert (
        x.db.scalar(
            select(NotificationRecipient).where(NotificationRecipient.plan_id == p.id)
        ).state
        == "eligible"
    )
    assert (
        "Northwest Ohio Broadband" in p.message
        and "419-954-0586" in p.message
        and "STOP" in p.message
    )
    assert x.account.name not in p.message and x.account.phone not in p.message
    assert p.status == "draft" and not x.db.scalar(select(NotificationDelivery.id))


@pytest.mark.parametrize(
    "case", ["brief", "suspected", "ups", "unmapped", "optout", "missing", "suppressed"]
)
def test_uncertain_brief_or_ineligible_never_queue_customer_sms(network, case):
    x = network
    r = req(x)
    if case == "brief":
        x.incident.started_at = NOW - timedelta(seconds=5)
    if case == "suspected":
        r.evidence.confirmed = False
    if case == "ups":
        r.evidence.event_type = "ups_warning"
    if case == "unmapped":
        x.service.mapping_state = "unknown"
    if case == "optout":
        x.account.sms_opt_out = True
    if case == "missing":
        x.account.phone = None
    if case == "suppressed":
        x.db.add(
            NotificationSuppression(customer_id=x.account.id, reason="operator_hold")
        )
    x.db.flush()
    p = prepared(x, r)
    approve(x.db, p.id, x.admin.id, NOW)
    assert sms_rows(x) == []


def test_duplicate_incident_and_approval_do_not_duplicate_delivery(network):
    x = network
    p = prepared(x)
    assert prepared(x).id == p.id
    approve(x.db, p.id, x.admin.id, NOW)
    approve(x.db, p.id, x.admin.id, NOW)
    assert len(sms_rows(x)) == 1
    sender = MockSender()
    drain(x, sms=sender)
    drain(x, sms=sender)
    assert len(sender.calls) == 1 and sms_rows(x)[0].state == "simulated"
    assert (
        x.db.scalar(
            select(func.count(NotificationAttempt.id)).where(
                NotificationAttempt.delivery_id == sms_rows(x)[0].id
            )
        )
        == 1
    )


@pytest.mark.parametrize("change", ["phone", "optout", "mapping", "policy"])
def test_changes_after_preview_require_new_operator_review(network, change):
    x = network
    p = prepared(x)
    if change == "phone":
        x.account.phone = "+15555550999"
    if change == "optout":
        x.account.sms_opt_out = True
    if change == "mapping":
        x.service.mapping_state = "review_required"
    if change == "policy":
        x.db.get(NotificationPolicy, 1).config = Policy(max_sms_per_plan=1).model_dump()
    x.db.flush()
    with pytest.raises(ValueError, match="changed"):
        approve(x.db, p.id, x.admin.id, NOW)
    assert not sms_rows(x)


@pytest.mark.parametrize("change", ["consent", "mapping", "actor"])
def test_worker_revalidates_approved_context_and_actor(network, change):
    x = network
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    if change == "consent":
        x.account.sms_opt_out = True
    if change == "mapping":
        x.service.mapping_state = "unknown"
    if change == "actor":
        x.admin.is_active = False
    x.db.flush()
    sender = MockSender()
    drain(x, sms=sender)
    assert not sender.calls and sms_rows(x)[0].state == "suppressed"


def test_provider_retry_exponential_backoff_and_restart(network):
    x = network
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    sender = MockSender(failures=1)
    drain(x, sms=sender)
    d = sms_rows(x)[0]
    assert (
        d.state == "retry"
        and d.attempts == 1
        and utc(d.next_attempt_at) == NOW + timedelta(seconds=5)
    )
    # A fresh worker/provider object resumes the same durable row, not a new job.
    restarted = MockSender()
    drain(x, sms=restarted, now=NOW + timedelta(seconds=6))
    assert d.state == "simulated" and d.attempts == 2
    drain(x, sms=MockSender(), now=NOW + timedelta(seconds=20))
    assert d.attempts == 2


def test_dead_letter_preserves_failure_history(network):
    x = network
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    sender = MockSender(failures=10)
    for seconds in (0, 6, 17):
        drain(x, sms=sender, now=NOW + timedelta(seconds=seconds))
    d = sms_rows(x)[0]
    assert (
        d.state == "dead_letter"
        and d.attempts == 3
        and d.last_error == "provider_unavailable"
    )
    assert (
        x.db.scalar(
            select(func.count(NotificationAttempt.id)).where(
                NotificationAttempt.delivery_id == d.id
            )
        )
        == 3
    )


def test_slack_failure_isolated_from_sms_and_dashboard(network):
    x = network
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x, slack=MockSender(failures=10, permanent=True))
    assert sms_rows(x)[0].state == "simulated"
    assert (
        x.db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.channel == "slack",
                NotificationDelivery.recipient_key == "noc-simulation-critical",
            )
        ).state
        == "dead_letter"
    )
    assert (
        x.db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.channel == "internal"
            )
        ).state
        == "simulated"
    )


def test_real_provider_and_live_configuration_rejected(network):
    with pytest.raises(ValueError):
        MockSlack(None)
    with pytest.raises(ValueError):
        Settings(
            secret_key="x" * 40,
            admin_password="synthetic-only",
            database_url="sqlite://",
            notifications_live_delivery_enabled=True,
        )
    with pytest.raises(ValueError):
        run_batch(network.db, sms=object())


@pytest.mark.parametrize(
    "zone,at,expected",
    [
        ("America/New_York", "2026-11-01T05:30:00+00:00", "2026-11-01T13:00:00+00:00"),
        ("America/New_York", "2026-03-08T06:30:00+00:00", "2026-03-08T12:00:00+00:00"),
        ("UTC", "2026-11-14T12:00:00+00:00", "2026-11-14T12:00:00+00:00"),
    ],
)
def test_quiet_hours_and_dst_are_timezone_aware(zone, at, expected):
    assert quiet_due(
        datetime.fromisoformat(at), Policy(timezone=zone)
    ) == datetime.fromisoformat(expected)


def test_quiet_exception_requires_explicit_critical_policy(network):
    x = network
    r = req(x, critical_quiet_bypass=True)
    with pytest.raises(ValueError):
        prepared(x, r)
    x.db.get(NotificationPolicy, 1).config = Policy(
        critical_quiet_bypass=True
    ).model_dump()
    x.db.flush()
    assert prepared(x, r)


def test_cost_budget_and_volume_caps(network):
    x = network
    x.db.get(NotificationPolicy, 1).config = Policy(
        sms_price_microunits=100000, daily_budget_microunits=0
    ).model_dump()
    x.db.flush()
    p = prepared(x)
    assert p.estimated_cost_microunits > 0
    with pytest.raises(ValueError, match="budget"):
        approve(x.db, p.id, x.admin.id, NOW)


def test_customer_and_incident_storm_suppression(network):
    x = network
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x)
    p2 = prepared(x, req(x, kind="update", update_number=1))
    approve(x.db, p2.id, x.admin.id, NOW)
    drain(x, now=NOW + timedelta(seconds=10))
    assert (
        sms_rows(x)[-1].last_error == "incident_cooldown"
        and sms_rows(x)[-1].state == "suppressed"
    )


def test_upstream_parent_is_probable_and_redundancy_is_respected(network):
    x = network
    up = Incident(
        title="Synthetic upstream backhaul",
        link_id=x.up.id,
        started_at=NOW - timedelta(hours=1),
    )
    x.db.add(up)
    x.db.flush()
    assert up.id in probable_upstream(
        x.db,
        __import__("app.customers.impact", fromlist=["Target"]).Target(site_id=x.a.id),
        x.incident.id,
    )
    x.db.add(
        NetworkLink(
            name="NOC96 redundant path",
            a_site_id=x.root.id,
            z_site_id=x.a.id,
            status="active",
        )
    )
    x.db.flush()
    assert (
        probable_upstream(
            x.db,
            __import__("app.customers.impact", fromlist=["Target"]).Target(
                site_id=x.a.id
            ),
            x.incident.id,
        )
        == []
    )


def maintenance(x):
    row = NotificationMaintenance(
        title="Synthetic planned power work",
        target={"site_id": x.a.id},
        starts_at=NOW + timedelta(hours=1),
        ends_at=NOW + timedelta(hours=2),
        status="scheduled",
        created_by=x.admin.id,
    )
    x.db.add(row)
    x.db.flush()
    return row


def test_maintenance_advance_and_cancellation_only_prior_recipients(network):
    x = network
    m = maintenance(x)
    r = PlanRequest(
        maintenance_id=m.id,
        kind="maintenance",
        evidence=Evidence(reference="SYN-MAINT", event_type="planned_maintenance"),
    )
    p = prepared(x, r)
    assert "Planned maintenance" in p.message
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x)
    m.status = "cancelled"
    m.revision += 1
    x.db.flush()
    r.kind = "cancelled"
    cancel = prepared(x, r)
    approve(x.db, cancel.id, x.admin.id, NOW)
    drain(x)
    assert sms_rows(x)[-1].state == "simulated" and "cancelled" in cancel.message


def test_maintenance_change_invalidates_scheduled_notices(network):
    x = network
    m = maintenance(x)
    r = PlanRequest(
        maintenance_id=m.id,
        kind="maintenance",
        due_at=NOW + timedelta(minutes=10),
        evidence=Evidence(reference="SYN-MAINT", event_type="planned_maintenance"),
    )
    p = prepared(x, r)
    approve(x.db, p.id, x.admin.id, NOW)
    m.status = "cancelled"
    m.revision += 1
    x.db.flush()
    drain(x, now=NOW + timedelta(minutes=11))
    assert sms_rows(x)[0].state == "suppressed"


@pytest.mark.parametrize("bad", ["open", "samples", "stale"])
def test_false_recovery_rejected(network, bad):
    x = network
    x.incident.status = "resolved"
    x.incident.resolved_at = NOW - timedelta(seconds=1)
    r = req(x, kind="restoration")
    r.evidence.recovery_verified = True
    r.evidence.recovery_samples = 3
    r.evidence.verified_at = NOW
    if bad == "open":
        x.incident.status = "open"
    if bad == "samples":
        r.evidence.recovery_samples = 1
    if bad == "stale":
        r.evidence.verified_at = NOW - timedelta(minutes=10)
    x.db.flush()
    with pytest.raises(ValueError, match="Restoration"):
        prepared(x, r)


def test_verified_restoration_only_notifies_previous_outage_recipients(network):
    x = network
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x)
    x.incident.status = "resolved"
    x.incident.resolved_at = NOW
    x.db.flush()
    r = req(x, kind="restoration")
    r.evidence.recovery_verified = True
    r.evidence.recovery_samples = 3
    r.evidence.verified_at = NOW
    restored = prepared(x, r)
    approve(x.db, restored.id, x.admin.id, NOW)
    drain(x, now=NOW + timedelta(seconds=2))
    assert sms_rows(x)[-1].state == "simulated"


@pytest.mark.parametrize(
    "mode,status,code",
    [
        ("webhook", 200, None),
        ("api", 200, None),
        ("webhook", 429, "rate_limited"),
        ("api", 401, "authentication_failed"),
        ("webhook", 500, "provider_unavailable"),
    ],
)
def test_slack_webhook_and_api_contracts_are_mock_only(mode, status, code):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(status, json={"ok": True})

    provider = MockSlack(httpx.MockTransport(handler), mode)
    if code:
        with pytest.raises(DeliveryFailure, match=code):
            provider.send(
                key="synthetic",
                destination="noc-simulation",
                payload={"text": "synthetic"},
            )
    else:
        assert provider.send(
            key="synthetic", destination="noc-simulation", payload={"text": "synthetic"}
        ).startswith("mock-slack-")
    assert calls[0].method == "POST"


def test_slack_payload_never_includes_customer_contacts(network):
    x = network
    x.a.name = "NOC tower synthetic@example.invalid +15555550101 <@everyone>"
    x.db.flush()
    p = prepared(x)
    encoded = json.dumps(p.slack_payload)
    assert (
        x.account.phone not in encoded
        and "synthetic@example.invalid" not in encoded
        and x.account.name not in encoded
        and "<@" not in encoded
    )
    assert "potential_customer_count" in encoded and "/?incident=" in encoded


@pytest.mark.parametrize(
    "bad", ["{customer_phone}", "{brand.__class__}", "{event_id!r}", "{support:>1000}"]
)
def test_template_fields_cannot_access_private_data(bad):
    with pytest.raises(ValueError):
        validate_template("{brand} {support} {opt_out} " + bad)


def test_encrypted_credentials_private_key_and_no_plaintext(tmp_path):
    path = tmp_path / "key"
    path.write_bytes(Fernet.generate_key())
    path.chmod(0o600)
    value = "synthetic-provider-secret"
    encrypted = seal(path, value)
    assert value not in encrypted and unseal(path, encrypted) == value
    path.chmod(0o644)
    with pytest.raises(ValueError):
        seal(path, value)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        seal(link, value)


def test_mock_callback_rejects_tampering_and_replay():
    key = b"synthetic-only"
    body = b'{"status":"simulated"}'
    stamp = 12345
    sig = hmac.new(key, str(stamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    assert verified_callback(key, body, sig, stamp, stamp)
    assert not verified_callback(key, body + b"x", sig, stamp, stamp)
    assert not verified_callback(key, body, sig, stamp, stamp + 301)


def test_auto_discovery_only_drafts_and_deduplicates(network):
    x = network
    assert discover(x.db, NOW) == 1 and discover(x.db, NOW) == 0
    p = x.db.scalar(select(NotificationPlan))
    assert p.status == "draft" and p.confidence == "suspected" and not sms_rows(x)


@pytest.fixture
def api(network):
    x = network
    x.db.commit()
    app.dependency_overrides[get_db] = lambda: x.db
    with TestClient(app) as client:
        yield x, client, {
            "Authorization": "Bearer "
            + create_access_token(x.admin.username, user_id=x.admin.id)
        }
    app.dependency_overrides.clear()


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
@pytest.mark.parametrize(
    "path,method,body",
    [
        ("/api/notifications", "GET", None),
        ("/api/notifications/mock-test", "POST", None),
        ("/api/notifications/policy", "PUT", Policy().model_dump()),
        (
            "/api/notifications/plans/99999/approve",
            "POST",
            {"confirm_simulation": True, "fingerprint": "a" * 64},
        ),
    ],
)
def test_notification_permissions(api, role, path, method, body):
    x, c, h = api
    x.admin.role = role
    x.db.commit()
    r = c.request(method, path, headers=h, json=body)
    expected = (
        403
        if role == "viewer"
        or role == "operator"
        and path in {"/api/notifications/mock-test", "/api/notifications/policy"}
        else (404 if "99999" in path else 200)
    )
    assert r.status_code == expected


def test_approval_confirmation_audit_and_safe_views(api):
    x, c, h = api
    p = prepared(x)
    x.db.commit()
    assert (
        c.post(
            f"/api/notifications/plans/{p.id}/approve",
            headers=h,
            json={"confirm_simulation": False, "fingerprint": p.fingerprint},
        ).status_code
        == 409
    )
    assert (
        c.post(
            f"/api/notifications/plans/{p.id}/approve",
            headers=h,
            json={"confirm_simulation": True, "fingerprint": p.fingerprint},
        ).status_code
        == 200
    )
    result = c.get(f"/api/notifications/plans/{p.id}", headers=h)
    assert (
        result.status_code == 200
        and x.account.phone not in result.text
        and x.account.email not in result.text
    )
    assert any(a["action"] == "notification.approved" for a in result.json()["audit"])
    assert c.get("/api/notifications").status_code == 401


def test_private_provider_validation_never_echoes_secret(api):
    _, c, h = api
    r = c.put(
        "/api/notifications/credentials/sms",
        headers=h,
        json={"value": "s" * 5000, "password": "never-echo-me"},
    )
    assert (
        r.status_code == 422
        and "never-echo-me" not in r.text
        and "ssssss" not in r.text
    )


def test_maintenance_reschedule_cancellation_and_stale_edit(api):
    x, c, h = api
    r = c.post(
        "/api/notifications/maintenance",
        headers=h,
        json={
            "title": "Synthetic power maintenance",
            "sector_id": x.sector.id,
            "starts_at": (NOW + timedelta(hours=1)).isoformat(),
            "ends_at": (NOW + timedelta(hours=2)).isoformat(),
        },
    )
    assert r.status_code == 200
    ident = r.json()["id"]
    r = c.patch(
        f"/api/notifications/maintenance/{ident}",
        headers=h,
        json={
            "revision": 1,
            "action": "reschedule",
            "starts_at": (NOW + timedelta(hours=3)).isoformat(),
            "ends_at": (NOW + timedelta(hours=4)).isoformat(),
        },
    )
    assert r.status_code == 200 and r.json()["revision"] == 2
    assert (
        c.patch(
            f"/api/notifications/maintenance/{ident}",
            headers=h,
            json={"revision": 1, "action": "cancel"},
        ).status_code
        == 409
    )
    assert (
        c.patch(
            f"/api/notifications/maintenance/{ident}",
            headers=h,
            json={"revision": 2, "action": "cancel"},
        ).status_code
        == 200
    )


def test_old_preview_fingerprint_cannot_approve_new_draft(network):
    x = network
    p = prepared(x)
    old = p.fingerprint
    r = req(x)
    r.evidence.reference = "SYN-REVIEW-NEW"
    prepared(x, r)
    with pytest.raises(ValueError, match="Draft changed"):
        approve(x.db, p.id, x.admin.id, NOW, expected_fingerprint=old)
    assert p.status == "draft" and not sms_rows(x)


def test_recipient_volume_limit_is_not_silent_truncation(network):
    x = network
    for i in range(2, 4):
        a = CustomerAccount(
            external_id=f"SYN-NOC96-C{i}",
            name="Synthetic",
            phone=f"+1555555099{i}",
            sms_consent="granted",
            communication_preference="sms",
            consent_record="SYN",
            consent_recorded_at=NOW,
            source_updated_at=NOW,
            last_synced_at=NOW,
        )
        x.db.add(a)
        x.db.flush()
        x.db.add(
            CustomerService(
                external_id=f"SYN-NOC96-S{i}",
                customer_id=a.id,
                status="active",
                service_type="wireless",
                site_id=x.a.id,
                device_id=x.ap.id,
                mapping_state="verified",
                source_updated_at=NOW,
                last_synced_at=NOW,
                source_refs={},
            )
        )
    p = Policy.model_validate(x.db.get(NotificationPolicy, 1).config)
    p.max_sms_per_plan = 1
    x.db.get(NotificationPolicy, 1).config = p.model_dump()
    x.db.flush()
    with pytest.raises(ValueError, match="storm"):
        prepared(x)
    assert not x.db.scalar(select(NotificationPlan.id))


def test_hourly_rate_limit_defers_without_retry_or_cost(network):
    x = network
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x)
    m = maintenance(x)
    r = PlanRequest(
        maintenance_id=m.id,
        kind="maintenance",
        evidence=Evidence(reference="SYN", event_type="planned_maintenance"),
    )
    new = prepared(x, r)
    pol = Policy.model_validate(x.db.get(NotificationPolicy, 1).config)
    pol.max_sms_per_hour = 1
    x.db.get(NotificationPolicy, 1).config = pol.model_dump()
    x.db.flush()
    new = prepared(x, r)
    approve(x.db, new.id, x.admin.id, NOW)
    drain(x)
    assert sms_rows(x)[-1].attempts == 0 and utc(sms_rows(x)[-1].next_attempt_at) > NOW


def test_queue_transaction_rollback_resumes_same_delivery(api):
    x, c, h = api
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    x.db.commit()
    drain(x)
    x.db.rollback()
    x.db.expire_all()
    d = sms_rows(x)[0]
    assert d.state == "queued" and d.attempts == 0
    drain(x)
    x.db.commit()
    d = sms_rows(x)[0]
    assert d.state == "simulated" and d.attempts == 1


def test_queue_claim_is_serialized_across_workers(api):
    import psycopg

    x, c, h = api
    x.db.commit()
    url = os.environ["RF_INTEGRATION_TEST_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://"
    )
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(960001)")
            with pytest.raises(ValueError, match="in progress"):
                drain(x)
    x.db.rollback()


def test_encrypted_vault_api_does_not_expose_credentials(api, tmp_path, monkeypatch):
    x, c, h = api
    key = tmp_path / "vault"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "notifications_vault_key_file", str(key))
    secret = "synthetic-private-provider-token"
    r = c.put("/api/notifications/credentials/slack", headers=h, json={"value": secret})
    assert r.status_code == 200 and secret not in r.text
    assert secret not in c.get("/api/notifications", headers=h).text
    row = x.db.get(NotificationCredential, "slack")
    assert secret not in row.ciphertext and unseal(key, row.ciphertext) == secret


def test_dead_letter_retry_requires_current_fingerprint(api):
    x, c, h = api
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x, sms=MockSender(failures=1, permanent=True))
    x.db.commit()
    d = sms_rows(x)[0]
    assert (
        c.post(
            f"/api/notifications/deliveries/{d.id}/retry",
            headers=h,
            json={"confirm_simulation": True, "fingerprint": "0" * 64},
        ).status_code
        == 409
    )
    assert (
        c.post(
            f"/api/notifications/deliveries/{d.id}/retry",
            headers=h,
            json={"confirm_simulation": True, "fingerprint": p.fingerprint},
        ).status_code
        == 200
    )
    assert sms_rows(x)[0].attempts == 1


def test_retention_redacts_content_and_keeps_dedupe(api):
    x, c, h = api
    p = prepared(x)
    p.severity = "warning"
    approve(x.db, p.id, x.admin.id, NOW)
    drain(x)
    p.status = "complete"
    p.created_at = NOW - timedelta(days=40)
    x.db.commit()
    result = c.post("/api/notifications/redact-expired", headers=h)
    assert result.status_code == 200 and result.json()["redacted"] == 1
    x.db.expire_all()
    assert (
        x.db.get(NotificationPlan, p.id).message == "[redacted]"
        and x.db.get(NotificationPlan, p.id).snapshot == {}
    )
    assert x.db.scalar(select(NotificationRecipient)).contact_hash is None


@pytest.mark.parametrize(
    "status,error",
    [
        (201, None),
        (401, "authentication_failed"),
        (429, "rate_limited"),
        (503, "provider_unavailable"),
    ],
)
def test_interchangeable_sms_wire_adapter_uses_only_mock_transport(status, error):
    from app.notifications.providers import TwilioMockAdapter

    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(status, json={"sid": "SM" + "a" * 32, "status": "queued"})

    provider = TwilioMockAdapter(
        httpx.MockTransport(handler),
        account_sid="AC" + "b" * 32,
        token="synthetic-private-token",
        from_number="+15555550996",
    )
    if error:
        with pytest.raises(DeliveryFailure, match=error):
            provider.send(
                key="synthetic-idempotency",
                destination="+15555550101",
                payload={"body": "Synthetic operational message"},
            )
    else:
        assert provider.send(
            key="synthetic-idempotency",
            destination="+15555550101",
            payload={"body": "Synthetic operational message"},
        ).startswith("mock-sms-")
    assert calls[0].method == "POST" and calls[0].url.host == "sms-fixture.invalid"
    assert provider.calls == ["synthetic-idempotency"]


def test_sms_adapter_rejects_real_network_transport():
    from app.notifications.providers import TwilioMockAdapter

    with pytest.raises(ValueError):
        TwilioMockAdapter(
            None,
            account_sid="AC" + "b" * 32,
            token="synthetic",
            from_number="+15555550996",
        )


def test_large_cost_estimate_cannot_overflow_or_bypass_budget(network):
    x = network
    for i in range(2, 101):
        a = CustomerAccount(
            external_id=f"SYN-COST-C{i}",
            phone=f"+155550{i:05d}",
            sms_consent="granted",
            communication_preference="sms",
            consent_record="SYN",
            consent_recorded_at=NOW,
            source_updated_at=NOW,
            last_synced_at=NOW,
        )
        x.db.add(a)
        x.db.flush()
        x.db.add(
            CustomerService(
                external_id=f"SYN-COST-S{i}",
                customer_id=a.id,
                status="active",
                service_type="wireless",
                site_id=x.a.id,
                device_id=x.ap.id,
                mapping_state="verified",
                source_refs={},
                source_updated_at=NOW,
                last_synced_at=NOW,
            )
        )
    x.db.get(NotificationPolicy, 1).config = Policy(
        sms_price_microunits=10000000, daily_budget_microunits=1000000000
    ).model_dump()
    x.db.flush()
    p = prepared(x)
    assert p.estimated_cost_microunits > 2147483647
    with pytest.raises(ValueError, match="budget"):
        approve(x.db, p.id, x.admin.id, NOW)
    assert not sms_rows(x)


def test_notification_audit_cursor_preserves_full_history(api):
    x, c, h = api
    p = prepared(x)
    for i in range(110):
        x.db.add(
            AuditLog(
                user_id=x.admin.id,
                action="notification.synthetic_history",
                entity_type="notification",
                entity_id=str(p.id),
                details={"sequence": i},
            )
        )
    x.db.commit()
    first = c.get(f"/api/notifications/plans/{p.id}", headers=h).json()
    assert len(first["audit"]) == 100 and first["audit_has_more"]
    second = c.get(
        f'/api/notifications/plans/{p.id}?audit_before={first["audit_next"]}', headers=h
    ).json()
    assert second["audit"] and not second["audit_has_more"]
    assert not {a["id"] for a in first["audit"]} & {a["id"] for a in second["audit"]}


@pytest.mark.parametrize(
    "wall,status,expected",
    [
        ("2026-11-14T15:00", 200, "2026-11-14T20:00:00+00:00"),
        ("2026-11-01T01:30", 409, None),
        ("2026-03-08T02:30", 409, None),
    ],
)
def test_configured_local_time_rejects_dst_ambiguity(api, wall, status, expected):
    x, c, h = api
    x.db.get(NotificationPolicy, 1).config = Policy(
        timezone="America/New_York"
    ).model_dump()
    x.db.commit()
    r = c.post(
        "/api/notifications/local-time",
        headers=h,
        json={"value": wall, "timezone": "America/New_York"},
    )
    assert r.status_code == status
    if expected:
        assert r.json()["utc"] == expected


def test_site_maintenance_reuses_operations_suppression_window(api):
    from app.models.entities import MaintenanceWindow

    x, c, h = api
    r = c.post(
        "/api/notifications/maintenance",
        headers=h,
        json={
            "title": "Synthetic shared schedule",
            "site_id": x.a.id,
            "starts_at": (NOW + timedelta(hours=1)).isoformat(),
            "ends_at": (NOW + timedelta(hours=2)).isoformat(),
        },
    )
    assert r.status_code == 200
    n = x.db.get(NotificationMaintenance, r.json()["id"])
    window = x.db.get(MaintenanceWindow, n.operations_window_id)
    assert window.site_id == x.a.id and window.status == "scheduled"
    assert (
        c.patch(
            f"/api/notifications/maintenance/{n.id}",
            headers=h,
            json={"revision": 1, "action": "cancel"},
        ).status_code
        == 200
    )
    x.db.expire_all()
    assert x.db.get(MaintenanceWindow, window.id).status == "cancelled"


def test_external_operations_edit_invalidates_notification_context(api):
    from app.models.entities import MaintenanceWindow

    x, c, h = api
    r = c.post(
        "/api/notifications/maintenance",
        headers=h,
        json={
            "title": "Synthetic linked schedule",
            "site_id": x.a.id,
            "starts_at": (NOW + timedelta(hours=1)).isoformat(),
            "ends_at": (NOW + timedelta(hours=2)).isoformat(),
        },
    )
    m = x.db.get(NotificationMaintenance, r.json()["id"])
    x.db.get(MaintenanceWindow, m.operations_window_id).status = "cancelled"
    x.db.commit()
    with pytest.raises(ValueError, match="Operations maintenance changed"):
        prepared(
            x,
            PlanRequest(
                maintenance_id=m.id,
                kind="maintenance",
                evidence=Evidence(reference="SYN", event_type="planned_maintenance"),
            ),
        )


@pytest.mark.parametrize(
    "bad",
    [
        "http://noc.example.invalid",
        "https://user:secret@noc.example.invalid",
        "https://noc.example.invalid/?token=secret",
    ],
)
def test_dashboard_links_cannot_include_credentials_or_insecure_origins(bad):
    with pytest.raises(ValueError):
        Policy(dashboard_base_url=bad)


def test_auto_discovery_does_not_starve_new_incidents_behind_existing_drafts(network):
    x = network
    cfg = Policy.model_validate(x.db.get(NotificationPolicy, 1).config)
    cfg.max_plans_per_hour = 100
    x.db.get(NotificationPolicy, 1).config = cfg.model_dump()
    x.db.flush()
    for i in range(21):
        incident = (
            x.incident
            if i == 0
            else Incident(
                title=f"Synthetic historical draft {i}",
                site_id=x.a.id,
                started_at=NOW - timedelta(hours=3),
            )
        )
        if i:
            x.db.add(incident)
            x.db.flush()
        plan = create_plan(
            x.db,
            PlanRequest(
                incident_id=incident.id,
                evidence=Evidence(reference="SYN-OLD-DRAFT", event_type="tower_outage"),
            ),
            x.admin.id,
            NOW,
        )
        plan.created_at = NOW - timedelta(hours=2)
    latest = Incident(
        title="Synthetic newly arrived incident",
        site_id=x.a.id,
        started_at=NOW - timedelta(minutes=1),
    )
    x.db.add(latest)
    x.db.flush()
    assert discover(x.db, NOW) == 1
    assert (
        x.db.scalar(
            select(NotificationPlan).where(NotificationPlan.incident_id == latest.id)
        ).status
        == "draft"
    )
