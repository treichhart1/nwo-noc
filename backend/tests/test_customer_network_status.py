"""Synthetic approved publishing and native outbox integration; never sends externally."""

import json, copy, traceback
from datetime import timedelta
from uuid import uuid4
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app
from app.core.database import get_db
from app.core.security import create_access_token
from app.models.entities import MonitorCheck, MonitoringAlert, User, Incident, AuditLog
from app.notifications.models import (
    NotificationPlan,
    NotificationDelivery,
    NotificationRecipient,
    NotificationTemplate,
)
from app.notifications import service as ns
from app.notifications.worker import run_batch
from app.notifications.providers import MockSender
from app.customers.models import CustomerAccount, CustomerService
from app.customer_status import service as s
from app.customer_status.models import *
from app.customer_status.contracts import *
from app.customer_status.public_app import app as public_app
from test_notifications import network
from test_powercode_customers import imported, inventory
from test_rf_postgres_integration import postgres_db

R = "Synthetic approved customer communication; no network or provider transport"


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


@pytest.fixture
def x(network, monkeypatch, tmp_path):
    x = network
    file = tmp_path / "public-rate-key"
    file.write_text("c" * 64)
    file.chmod(0o600)
    monkeypatch.setattr(
        __import__("app.core.config", fromlist=["settings"]).settings,
        "customer_status_rate_key_file",
        str(file),
    )
    s.configure(
        x.db,
        x.admin,
        b(
            PolicyInput,
            expected_revision=0,
            enabled=True,
            baseline_status="all_systems_operational",
            public_baseline_reviewed=True,
        ),
    )
    x.area = s.area(
        x.db,
        x.admin,
        b(AreaInput, label="Northwest service area", publicly_approved=True),
    )
    x.p = s.get(
        x.db,
        Publication,
        s.create(x.db, x.admin, b(SourceInput, incident_id=x.incident.id))["id"],
    )
    return x


def draft(x, **kw):
    data = {
        "expected_version": x.p.version,
        "title": "Service interruption in northwest communities",
        "description": "Our technicians are investigating a service interruption. Further approved updates will follow.",
        "status": "service_disruption",
        "area_ids": [x.area["id"]],
        "privacy_reviewed": True,
    }
    data.update(kw)
    return s.get(
        x.db, Revision, s.draft(x.db, x.admin, x.p, b(DraftInput, **data))["id"]
    )


def step(x, r, action, user=None):
    return s.transition(
        x.db,
        user or x.admin,
        r,
        b(Transition, expected_version=x.p.version, action=action),
    )


def publish(x, r=None):
    r = r or draft(x)
    step(x, r, "request_approval")
    step(x, r, "approve")
    step(x, r, "publish")
    x.db.flush()
    return r


def client(x, public=False, role=None):
    target = public_app if public else app
    target.dependency_overrides[get_db] = lambda: x.db
    c = TestClient(target)
    if not public:
        if role:
            x.admin.role = role
            x.db.flush()
        c.headers["Authorization"] = "Bearer " + create_access_token(
            x.admin.username, user_id=x.admin.id
        )
    return c


@pytest.fixture(autouse=True)
def clean_overrides():
    yield
    app.dependency_overrides.clear()
    public_app.dependency_overrides.clear()


def test_disabled_public_status_never_claims_healthy(network):
    c = client(network, True)
    r = c.get("/public/network-status/v1")
    assert r.status_code == 503
    assert r.headers["cache-control"] == "no-store"


def test_public_status_requires_separate_approval(x):
    c = client(x, True)
    assert c.get("/public/network-status/v1").json()["incidents"] == []
    r = draft(x)
    step(x, r, "request_approval")
    assert c.get("/public/network-status/v1").json()["incidents"] == []
    step(x, r, "approve")
    assert c.get("/public/network-status/v1").json()["incidents"] == []
    step(x, r, "publish")
    data = c.get("/public/network-status/v1").json()
    assert data["overall_status"] == "service_disruption"
    assert len(data["incidents"]) == 1
    assert not x.db.scalar(select(NotificationDelivery.id))


def test_public_projection_has_no_internal_or_customer_fields(x):
    publish(x)
    r = client(x, True).get("/public/network-status/v1")
    assert r.status_code == 200
    raw = r.text
    for secret in [
        x.incident.title,
        x.a.name,
        x.account.name,
        x.account.phone,
        x.account.email,
        "incident_id",
        "site_id",
        "device_id",
        "customer_id",
        "source_digest",
        "fingerprint",
        "approved_by",
    ]:
        if secret:
            assert secret not in raw
    assert r.json()["incidents"][0]["id"] == x.p.identifier
    assert r.json()["incidents"][0]["areas"] == ["Northwest service area"]


@pytest.mark.parametrize(
    "text_value",
    [
        "<script>alert(1)</script>",
        "192.0.2.88",
        "2606:4700::1111",
        "contact@example.invalid",
        "https://private.example.invalid",
        "419-555-0111",
        "41.123456 -83.654321",
        "hello\x00world",
        "+15005550123",
        "41.12, -83.34",
    ],
)
def test_public_prose_rejects_sensitive_patterns(text_value):
    with pytest.raises(ValidationError):
        b(AreaInput, label=text_value, publicly_approved=True)


def test_public_cache_etag_and_unknown_queries(x):
    publish(x)
    c = client(x, True)
    r = c.get("/public/network-status/v1")
    assert r.headers["cache-control"] == "public, max-age=30, must-revalidate"
    assert (
        c.get(
            "/public/network-status/v1", headers={"If-None-Match": r.headers["etag"]}
        ).status_code
        == 304
    )
    assert c.head("/public/network-status/v1").content == b""
    assert c.get("/public/network-status/v1?customer_id=1").status_code == 422
    assert c.get("/public/network-status/v1?history_limit=100000").status_code == 422


def test_public_boundary_exposes_no_admin_signup_or_login_routes(x):
    c = client(x, True)
    for path in [
        "/api/devices",
        "/api/customer-status/v1/cases",
        "/api/auth/me",
        "/docs",
        "/openapi.json",
        "/public/signup/config",
    ]:
        assert c.get(path).status_code == 404
    assert c.post("/public/network-status/v1", json={}).status_code == 405


def test_public_rate_limits_ignore_forged_forwarded_ip(x, monkeypatch):
    import app.customer_status.public_app as pa
    from datetime import datetime, timezone

    at = datetime.now(timezone.utc).replace(second=10, microsecond=0)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return at

    monkeypatch.setattr(pa, "datetime", Frozen)
    c = client(x, True)
    for n in range(60):
        assert (
            c.get(
                "/public/network-status/v1", headers={"X-Forwarded-For": str(n)}
            ).status_code
            == 200
        )
    r = c.get("/public/network-status/v1")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "60"


def test_public_update_is_not_visible_before_new_approval(x):
    publish(x)
    r = draft(
        x,
        title="Investigation update for northwest communities",
        status="monitoring_recovery",
    )
    c = client(x, True)
    assert (
        c.get("/public/network-status/v1").json()["incidents"][0]["status"]
        == "service_disruption"
    )
    publish(x, r)
    assert r.state == "updated"
    assert (
        c.get("/public/network-status/v1").json()["incidents"][0]["status"]
        == "monitoring_recovery"
    )
    assert len(list(x.db.scalars(select(Revision)))) == 2


def test_withdrawal_requires_new_approved_revision(x):
    publish(x)
    r = draft(x, withdraw=True)
    step(x, r, "request_approval")
    step(x, r, "approve")
    assert client(x, True).get("/public/network-status/v1").json()["incidents"]
    step(x, r, "publish")
    assert client(x, True).get("/public/network-status/v1").json()["incidents"] == []
    assert r.state == "withdrawn"
    assert x.db.scalar(select(func.count()).select_from(Revision)) == 2


def test_source_changes_invalidate_approval(x):
    r = draft(x)
    step(x, r, "request_approval")
    step(x, r, "approve")
    x.incident.revision += 1
    x.db.flush()
    with pytest.raises(ValueError, match="changed"):
        step(x, r, "publish")


def test_public_approval_cannot_publish_older_draft(x):
    a = draft(x)
    step(x, a, "request_approval")
    draft(x)
    with pytest.raises(ValueError):
        step(x, a, "approve")


@pytest.mark.parametrize("role", ["operator", "technician", "viewer"])
def test_non_admin_cannot_approve_public_or_sms(x, role):
    r = draft(x)
    step(x, r, "request_approval")
    x.admin.role = role
    x.db.flush()
    with pytest.raises(PermissionError):
        step(x, r, "approve")


def test_native_sms_approval_is_independent_of_public(x):
    m = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    assert m["status"] == "draft"
    assert client(x, True).get("/public/network-status/v1").json()["incidents"] == []
    s.decide_message(
        x.db,
        x.admin,
        s.get(x.db, Message, m["id"]),
        b(MessageDecision, action="approve", fingerprint=m["fingerprint"]),
    )
    assert x.db.scalar(select(NotificationDelivery.id))
    assert client(x, True).get("/public/network-status/v1").json()["incidents"] == []


@pytest.mark.parametrize(
    "case",
    [
        "no_consent",
        "optout",
        "invalid_phone",
        "stale_service",
        "stale_account",
        "unknown_mapping",
        "estimated_upstream",
    ],
)
def test_customer_eligibility_is_conservative(x, case):
    if case == "no_consent":
        x.account.sms_consent = "unknown"
    if case == "optout":
        x.account.sms_opt_out = True
    if case == "invalid_phone":
        x.account.phone = None
    if case == "stale_service":
        x.service.last_synced_at = s.clock() - timedelta(days=2)
    if case == "stale_account":
        x.account.last_synced_at = s.clock() - timedelta(days=2)
    if case == "unknown_mapping":
        x.service.mapping_state = "unknown"
    if case == "estimated_upstream":
        x.incident.site_id = None
        x.incident.link_id = x.up.id
    x.db.flush()
    m = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    assert not any(r["eligible"] for r in m["recipients"])


@pytest.mark.parametrize("target", ["site", "sector", "device", "fiber"])
def test_reuses_native_customer_scope_mappings(x, target):
    if target == "sector":
        x.incident.site_id = None
        x.incident.sector_id = x.sector.id
    if target == "device":
        x.incident.site_id = None
        x.incident.device_id = x.ap.id
    if target == "fiber":
        x.incident.site_id = None
        x.incident.link_id = x.fiber.id
        x.service.service_type = "fiber"
        x.service.device_id = None
        x.service.sector_id = None
        x.service.fiber_link_id = x.fiber.id
    x.db.flush()
    data = s.impact_preview(x.db, x.p)
    assert data["eligible_count"] == 1
    assert data["classification_counts"]["confirmed"] == 1


def test_new_workflow_deduplicates_native_transition(x):
    a = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    z = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    assert a["id"] == z["id"]
    assert a["plan_id"] == z["plan_id"]
    assert x.db.scalar(select(func.count()).select_from(NotificationPlan)) == 1


def test_duplicate_customer_contact_is_suppressed(x):
    clone = CustomerAccount(
        external_id="syn-duplicate",
        name="Synthetic duplicate",
        phone=x.account.phone,
        source_updated_at=s.clock(),
        last_synced_at=s.clock(),
        sms_consent="granted",
        consent_record="synthetic",
        consent_recorded_at=s.clock(),
        communication_preference="sms",
    )
    x.db.add(clone)
    x.db.flush()
    m = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    assert not m["recipients"][0]["eligible"]
    assert m["recipients"][0]["reason"] == "shared_number_review"


def test_native_worker_mock_delivery_and_cancellation(x):
    m = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    row = s.get(x.db, Message, m["id"])
    s.decide_message(
        x.db,
        x.admin,
        row,
        b(MessageDecision, action="approve", fingerprint=m["fingerprint"]),
    )
    s.decide_message(
        x.db,
        x.admin,
        row,
        b(MessageDecision, action="cancel", fingerprint=m["fingerprint"]),
    )
    sender = MockSender()
    run_batch(x.db, sms=sender)
    assert not sender.calls
    assert set(x.db.scalars(select(NotificationDelivery.state))) == {"suppressed"}


def recovery(x, mode="stable"):
    now = s.clock()
    x.incident.status = "resolved"
    x.incident.resolved_at = now - timedelta(minutes=6)
    x.incident.revision += 1
    for ago in [300, 200, 100, 0]:
        x.db.add(
            MonitorCheck(
                device_id=x.ap.id,
                status="down" if mode == "down" and ago == 100 else "up",
                checked_at=now - timedelta(seconds=ago + 1),
            )
        )
    x.db.flush()
    return s.assess(x.db, x.admin, x.p, b(Mutation))


def test_restoration_requires_multiple_stable_samples(x):
    x.incident.status = "resolved"
    x.incident.resolved_at = s.clock()
    x.db.add(MonitorCheck(device_id=x.ap.id, status="up", checked_at=s.clock()))
    x.db.flush()
    p = s.assess(x.db, x.admin, x.p, b(Mutation))
    assert not p["verified"]
    with pytest.raises(ValueError):
        draft(x, status="resolved", recovery_id=p["id"])


def test_stable_restoration_is_independently_publishable(x):
    proof = recovery(x)
    assert proof["verified"]
    r = draft(x, status="resolved", recovery_id=proof["id"])
    publish(x, r)
    feed = client(x, True).get("/public/network-status/v1").json()
    assert len(feed["resolved"]) == 1
    assert not feed["incidents"]
    assert not x.db.scalar(select(NotificationDelivery.id))


def test_false_or_stale_recovery_cannot_publish(x):
    proof = recovery(x)
    r = draft(x, status="resolved", recovery_id=proof["id"])
    step(x, r, "request_approval")
    step(x, r, "approve")
    x.db.add(MonitorCheck(device_id=x.ap.id, status="down", checked_at=s.clock()))
    x.db.flush()
    with pytest.raises(ValueError, match="stable"):
        step(x, r, "publish")


def test_disconnected_recovery_not_verified(x):
    assert not recovery(x, "down")["verified"]


def test_templates_initialize_without_overwriting_operator_text(x):
    s.install_templates(x.db, x.admin, b(Mutation))
    original = x.db.get(NotificationTemplate, "outage")
    assert "We’re aware" in original.body
    original.body = "operator preserved text"
    x.db.flush()
    s.install_templates(x.db, x.admin, b(Mutation))
    assert original.body == "operator preserved text"


def test_public_eta_requires_explicit_approval(x):
    with pytest.raises(ValidationError):
        b(
            DraftInput,
            expected_version=1,
            title="Public outage update",
            description="Technicians are investigating.",
            area_ids=[x.area["id"]],
            status="investigating",
            privacy_reviewed=True,
            estimated_restoration_at=s.clock() + timedelta(hours=1),
        )
    r = draft(
        x,
        estimated_restoration_at=s.clock() + timedelta(hours=1),
        restoration_estimate_approved=True,
    )
    publish(x, r)
    assert (
        client(x, True)
        .get("/public/network-status/v1")
        .json()["incidents"][0]["estimated_restoration_at"]
    )


def test_immutable_public_revision_payload_and_audit_history(x):
    r = draft(x)
    x.db.flush()
    with pytest.raises(DBAPIError):
        with x.db.begin_nested():
            x.db.execute(
                text(
                    "UPDATE customer_status_revisions SET payload='{}'::json WHERE id=:id"
                ),
                {"id": r.id},
            )
    assert x.db.scalar(select(Event.id))
    assert x.db.scalar(
        select(AuditLog.id).where(AuditLog.action == "customer_status.public_drafted")
    )


def test_private_routes_require_authentication_and_roles(x):
    assert TestClient(app).get("/api/customer-status/v1/cases").status_code == 401
    c = client(x, role="technician")
    assert c.get("/api/customer-status/v1/cases").status_code == 403


def test_api_mutations_are_json_safe_and_retry_idempotent(x):
    c = client(x)
    req = b(
        AreaInput, label="Southern service area", publicly_approved=True
    ).model_dump(mode="json")
    a = c.post("/api/customer-status/v1/areas", json=req)
    assert a.status_code == 200, a.text
    z = c.post("/api/customer-status/v1/areas", json=req)
    assert z.json() == a.json()
    assert c.get("/api/customer-status/v1/cases/" + str(x.p.id)).status_code == 200
    assert (
        c.get("/api/customer-status/v1/cases/" + str(x.p.id) + "/impact").status_code
        == 200
    )


@pytest.mark.parametrize("verified", [True, False])
def test_unified_redundancy_overrides_legacy_tower_impact(x, verified):
    from test_unified import node, edge, circuit, path

    core = node(x, "core", site_id=x.root.id)
    tower = node(x, "tower", site_id=x.a.id)
    customer = node(x, "customer", service_id=x.service.id)
    wire = edge(x, core, tower, "microwave")
    access = edge(x, tower, customer)
    alt = edge(x, core, customer, "ethernet")
    c = circuit(x, x.service.id)
    path(x, c, core, customer, [wire, access])
    path(x, c, core, customer, [alt], verified)
    data = s.impact_preview(x.db, x.p)
    assert data["eligible_count"] == 0
    assert data["impacts"][0]["reason"] == (
        "verified_unified_alternate_path" if verified else "unified_path_uncertain"
    )


def maintenance(x, affecting=False):
    from app.notifications.models import NotificationMaintenance

    m = NotificationMaintenance(
        title="Private maintenance description",
        target={"site_id": x.a.id},
        targets=[{"site_id": x.a.id}],
        starts_at=s.clock() + timedelta(hours=3),
        ends_at=s.clock() + timedelta(hours=4),
        created_by=x.admin.id,
    )
    x.db.add(m)
    x.db.flush()
    p = s.get(
        x.db,
        Publication,
        s.create(
            x.db,
            x.admin,
            b(SourceInput, maintenance_id=m.id, service_affecting=affecting),
        )["id"],
    )
    return m, p


def test_non_interrupting_maintenance_cannot_send_customer_sms(x):
    m, p = maintenance(x)
    assert s.impact_preview(x.db, p)["eligible_count"] == 0
    with pytest.raises(ValueError, match="Non-interrupting"):
        s.prepare_message(
            x.db,
            x.admin,
            p,
            b(MessageInput, kind="maintenance", event_type="planned_maintenance"),
        )


def test_planned_maintenance_uses_approved_source_window(x):
    m, p = maintenance(x, True)
    x.p = p
    r = draft(
        x,
        status="investigating",
        title="Scheduled maintenance in northwest communities",
    )
    publish(x, r)
    feed = client(x, True).get("/public/network-status/v1").json()
    assert len(feed["maintenance"]) == 1
    assert feed["maintenance"][0]["starts_at"] == m.starts_at.isoformat().replace(
        "+00:00", "Z"
    )
    msg = s.prepare_message(
        x.db,
        x.admin,
        p,
        b(MessageInput, kind="maintenance", event_type="planned_maintenance"),
    )
    assert "not scheduled" not in msg["message"]
    assert msg["status"] == "draft"


def test_restoration_sms_requires_new_approval_and_prior_notice(x):
    msg = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    s.decide_message(
        x.db,
        x.admin,
        s.get(x.db, Message, msg["id"]),
        b(MessageDecision, action="approve", fingerprint=msg["fingerprint"]),
    )
    run_batch(x.db, sms=MockSender())
    x.db.flush()
    proof = recovery(x)
    assert proof["verified"]
    restored = s.prepare_message(
        x.db,
        x.admin,
        x.p,
        b(
            MessageInput,
            kind="restoration",
            impact_confirmed=True,
            event_type="restoration",
            recovery_id=proof["id"],
        ),
    )
    assert restored["status"] == "draft"
    assert restored["plan_id"] != msg["plan_id"]
    assert not x.db.scalar(
        select(NotificationDelivery.id).where(
            NotificationDelivery.plan_id == restored["plan_id"]
        )
    )


def test_ambiguous_mock_response_is_not_automatically_retried(x):
    from app.notifications.providers import DeliveryFailure

    class Ambiguous(MockSender):
        def send(self, **kw):
            self.calls.append(kw["key"])
            raise DeliveryFailure("timeout")

    m = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    s.decide_message(
        x.db,
        x.admin,
        s.get(x.db, Message, m["id"]),
        b(MessageDecision, action="approve", fingerprint=m["fingerprint"]),
    )
    sender = Ambiguous()
    run_batch(x.db, sms=sender)
    x.db.flush()
    run_batch(x.db, sms=sender, now=s.clock() + timedelta(minutes=1))
    assert len(sender.calls) == 1
    d = x.db.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.plan_id == m["plan_id"],
            NotificationDelivery.channel == "sms",
        )
    )
    assert d.state == "dead_letter"
    assert d.last_error == "ambiguous_requires_reconciliation"


def test_contact_changes_after_approval_prevent_delivery(x):
    m = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    s.decide_message(
        x.db,
        x.admin,
        s.get(x.db, Message, m["id"]),
        b(MessageDecision, action="approve", fingerprint=m["fingerprint"]),
    )
    x.account.sms_opt_out = True
    x.db.flush()
    sender = MockSender()
    run_batch(x.db, sms=sender)
    assert not sender.calls


def test_missing_baseline_approval_is_rejected():
    with pytest.raises(ValidationError):
        b(PolicyInput, expected_revision=0, enabled=True)


def test_public_gateway_role_cannot_read_sources_customers_or_private_drafts(x):
    from app.customer_status.public_role import install

    publish(x)
    install(x.db, "b" * 64)
    with x.db.begin_nested():
        x.db.execute(text("SET LOCAL ROLE noc_status_public"))
        assert (
            x.db.scalar(text("SELECT count(*) FROM noc_public_status.announcements"))
            == 1
        )
        for table in [
            "incidents",
            "customer_accounts",
            "customer_services",
            "customer_status_revisions",
            "notification_plans",
            "finance_entries",
        ]:
            with pytest.raises(DBAPIError):
                with x.db.begin_nested():
                    x.db.execute(text("SELECT * FROM " + table + " LIMIT 1"))
        with pytest.raises(DBAPIError):
            with x.db.begin_nested():
                x.db.execute(text("UPDATE customer_status_policy SET enabled=false"))
        x.db.execute(text("RESET ROLE"))


def test_native_sms_route_cannot_bypass_new_admin_requirement(x):
    m = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    x.admin.role = "operator"
    x.db.flush()
    with pytest.raises(PermissionError):
        ns.approve(
            x.db, m["plan_id"], x.admin.id, expected_fingerprint=m["fingerprint"]
        )


def test_public_estimate_absent_by_default_and_rejected_copy_not_visible(x):
    r = draft(x)
    step(x, r, "request_approval")
    step(x, r, "reject")
    feed = client(x, True).get("/public/network-status/v1").json()
    assert not feed["incidents"]
    r = draft(x)
    publish(x, r)
    assert (
        client(x, True)
        .get("/public/network-status/v1")
        .json()["incidents"][0]["estimated_restoration_at"]
        is None
    )


def test_non_interrupting_future_maintenance_does_not_declare_an_outage(x):
    m, p = maintenance(x, False)
    x.p = p
    r = draft(
        x,
        status="all_systems_operational",
        title="Scheduled maintenance in northwest communities",
    )
    publish(x, r)
    data = client(x, True).get("/public/network-status/v1").json()
    assert data["overall_status"] == "all_systems_operational"
    assert data["maintenance"]
    assert not data["incidents"]


def test_existing_notification_retention_does_not_break_communication_history(x):
    m = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    plan = x.db.get(NotificationPlan, m["plan_id"])
    plan.status = "complete"
    plan.created_at = s.clock() - timedelta(days=40)
    x.db.flush()
    c = client(x)
    assert c.post("/api/notifications/redact-expired").status_code == 200
    detail = c.get("/api/customer-status/v1/cases/" + str(x.p.id))
    assert detail.status_code == 200, detail.text
    assert detail.json()["messages"][0]["message"] == "[redacted]"
    assert detail.json()["messages"][0]["recipients"] == []
    with pytest.raises(ValueError, match="legacy notification"):
        s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))


def test_public_policy_baseline_audit_keeps_reviewed_values(x):
    audit = x.db.scalar(
        select(AuditLog).where(AuditLog.action == "customer_status.policy_reviewed")
    )
    assert (
        audit.details["reviewed_values"]["baseline_status"] == "all_systems_operational"
    )


def test_gateway_database_password_errors_are_sanitized():
    from app.customer_status.public_role import install
    from sqlalchemy.exc import ProgrammingError

    secret = "b" * 64

    class Denied:
        def scalar(self, *a, **kw):
            return False

        def execute(self, statement, *a, **kw):
            if "PASSWORD" in str(statement):
                raise ProgrammingError(
                    str(statement), {}, RuntimeError("synthetic permission failure")
                )

    try:
        install(Denied(), secret)
        pytest.fail("Expected sanitized failure")
    except ValueError as exc:
        assert secret not in "".join(traceback.format_exception(exc))


def test_concurrent_cancellation_cannot_overwrite_an_accepted_worker_receipt(
    postgres_db, monkeypatch
):
    import os, threading
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base
    from app.core.config import settings
    from app.models.entities import Site, Device
    from app.notifications.contracts import Policy as NotificationSettings
    from app.notifications.models import NotificationPolicy

    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(settings, "offline_mode", True)
    engine = create_engine(os.environ["RF_INTEGRATION_TEST_DATABASE_URL"])
    schema = "noc109_cancel_" + uuid4().hex[:12]
    with engine.begin() as c:
        assert c.scalar(text("SELECT current_database()")) == "nwo_noc"
        c.execute(text('CREATE SCHEMA "' + schema + '"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    factory = sessionmaker(scoped)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    attempted = threading.Event()
    errors = []
    outcomes = []
    threads = []

    class HeldSender(MockSender):
        def send(self, **kw):
            self.calls.append(kw["key"])
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Synthetic provider release not received")
            return "mock-accepted-once"

    try:
        Base.metadata.create_all(scoped)
        with factory() as db:
            now = datetime.now(timezone.utc)
            user = User(
                username="synthetic-cancel-admin",
                email="cancel@example.invalid",
                password_hash="unusable",
                role="admin",
                is_active=True,
            )
            site = Site(name="Synthetic cancellation POP")
            db.add_all([user, site])
            db.flush()
            device = Device(
                source="synthetic",
                external_id="cancel-device",
                name="Synthetic radio",
                wisp_site_id=site.id,
                enabled=True,
            )
            account = CustomerAccount(
                external_id="cancel-customer",
                name="Synthetic customer",
                phone="+15005550123",
                sms_consent="granted",
                sms_opt_out=False,
                communication_preference="sms",
                consent_record="synthetic consent",
                consent_recorded_at=now,
                source_updated_at=now,
                last_synced_at=now,
            )
            db.add_all([device, account])
            db.flush()
            db.add(
                CustomerService(
                    external_id="cancel-service",
                    customer_id=account.id,
                    status="active",
                    service_type="wireless",
                    site_id=site.id,
                    device_id=device.id,
                    mapping_state="verified",
                    source_updated_at=now,
                    last_synced_at=now,
                )
            )
            incident = Incident(
                title="Synthetic cancellation outage",
                site_id=site.id,
                severity="warning",
                started_at=now - timedelta(hours=1),
            )
            db.add(incident)
            db.add(
                NotificationPolicy(
                    id=1,
                    config=NotificationSettings(
                        timezone="UTC",
                        quiet_start=(now.hour + 12) % 24,
                        quiet_end=(now.hour + 13) % 24,
                    ).model_dump(),
                )
            )
            db.flush()
            pub = s.get(
                db,
                Publication,
                s.create(db, user, b(SourceInput, incident_id=incident.id))["id"],
            )
            msg = s.prepare_message(
                db, user, pub, b(MessageInput, impact_confirmed=True)
            )
            s.decide_message(
                db,
                user,
                s.get(db, Message, msg["id"]),
                b(MessageDecision, action="approve", fingerprint=msg["fingerprint"]),
            )
            uid = user.id
            mid = msg["id"]
            pid = msg["plan_id"]
            fingerprint = msg["fingerprint"]
            db.commit()

        def worker():
            try:
                with factory() as db:
                    run_batch(db, sms=HeldSender(), slack=MockSender())
                    db.commit()
            except Exception as exc:
                errors.append(repr(exc))
                entered.set()

        def cancel():
            try:
                with factory() as db:
                    attempted.set()
                    try:
                        s.decide_message(
                            db,
                            db.get(User, uid),
                            db.get(Message, mid),
                            b(
                                MessageDecision,
                                action="cancel",
                                fingerprint=fingerprint,
                            ),
                        )
                        db.commit()
                        outcomes.append("cancelled")
                    except ValueError as exc:
                        assert "in progress; retry" in str(exc)
                        db.rollback()
                        outcomes.append("queue_busy")
            except Exception as exc:
                errors.append(repr(exc))
            finally:
                finished.set()

        threads = [threading.Thread(target=worker), threading.Thread(target=cancel)]
        threads[0].start()
        assert entered.wait(10)
        assert not errors, errors
        threads[1].start()
        assert attempted.wait(5)
        assert finished.wait(5)
        assert outcomes == ["queue_busy"]
        release.set()
        for t in threads:
            t.join(15)
            assert not t.is_alive()
        assert not errors, errors
        assert outcomes == ["queue_busy"]
        with factory() as db:
            d = db.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.plan_id == pid,
                    NotificationDelivery.channel == "sms",
                )
            )
            assert d.state == "simulated"
            assert d.provider_receipt == "mock-accepted-once"
            assert d.attempts == 1
            assert db.get(NotificationPlan, pid).status == "complete"
    finally:
        release.set()
        for t in threads:
            if t.ident:
                t.join(15)
        with engine.begin() as c:
            c.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        engine.dispose()


def test_cancelling_future_escalation_preserves_already_accepted_sms(x):
    m = s.prepare_message(x.db, x.admin, x.p, b(MessageInput, impact_confirmed=True))
    row = s.get(x.db, Message, m["id"])
    s.decide_message(
        x.db,
        x.admin,
        row,
        b(MessageDecision, action="approve", fingerprint=m["fingerprint"]),
    )
    sender = MockSender()
    run_batch(x.db, sms=sender)
    x.db.flush()
    sms = x.db.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.plan_id == m["plan_id"],
            NotificationDelivery.channel == "sms",
        )
    )
    assert sms.state == "simulated"
    receipt = sms.provider_receipt
    s.decide_message(
        x.db,
        x.admin,
        row,
        b(MessageDecision, action="cancel", fingerprint=m["fingerprint"]),
    )
    x.db.flush()
    assert sms.state == "simulated"
    assert sms.provider_receipt == receipt
    assert len(sender.calls) == 1
    assert (
        x.db.scalar(
            select(func.count())
            .select_from(NotificationDelivery)
            .where(
                NotificationDelivery.plan_id == m["plan_id"],
                NotificationDelivery.state.in_(["queued", "retry"]),
            )
        )
        == 0
    )
