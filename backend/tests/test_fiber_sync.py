"""Mock approved exports and real disposable PostgreSQL; no Designer/network equipment access."""

import json, time
from uuid import uuid4
from types import SimpleNamespace
from datetime import timedelta
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from app.main import app
from app.core.database import get_db
from app.core.config import settings
from app.core.security import create_access_token
from app.field.models import now, WorkOrder
from app.models.entities import User, Site, Device, Incident, AuditLog
from app.customers.models import CustomerAccount, CustomerService
from app.unified import models as m, service as u, contracts as uc
from app.fiber_sync import service as s, security, worker
from app.fiber_sync.models import *
from app.fiber_sync.contracts import *
from test_rf_postgres_integration import postgres_db

R = "Synthetic approved engineering export reconciliation, no live changes"


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


def rec(kind, id, **kw):
    return {
        "kind": kind,
        "external_id": id,
        "revision": 1,
        "previous_revision": None,
        "publication": "approved",
        "infrastructure_status": (
            "completed" if kind == "construction" else "commissioned"
        ),
        "approval_ref": "SYNTHETIC-ASBUILT-APPROVAL",
        "approved_at": (now() - timedelta(seconds=1)).isoformat(),
        **kw,
    }


def envelope(x, records=None, **kw):
    return Envelope(
        schema_version="nwo.fiber-operational.v1",
        envelope_id=uuid4(),
        instance_id=x.source.instance_id,
        project_id=x.source.project_id,
        from_cursor=0,
        to_cursor=1,
        generated_at=now() - timedelta(seconds=1),
        records=records or x.records,
        **kw,
    )


@pytest.fixture
def x(postgres_db, monkeypatch, tmp_path):
    db = postgres_db
    user = User(
        username="fiber-admin",
        email="fiber@example.invalid",
        password_hash="unusable",
        role="admin",
        is_active=True,
    )
    a = Site(
        name="Synthetic fiber core",
        site_type="core-pop",
        latitude=41.5,
        longitude=-83.5,
    )
    z = Site(name="Synthetic fiber-fed tower", latitude=41.51, longitude=-83.49)
    db.add_all([user, a, z])
    db.flush()
    account = CustomerAccount(
        external_id="synthetic-fiber-account",
        source_updated_at=now(),
        last_synced_at=now(),
    )
    db.add(account)
    db.flush()
    service = CustomerService(
        external_id="synthetic-fiber-service",
        customer_id=account.id,
        status="active",
        service_type="wireless",
        site_id=z.id,
        mapping_state="reviewed",
        source_updated_at=now(),
        last_synced_at=now(),
    )
    db.add(service)
    db.flush()
    src = s.source_create(
        db,
        user,
        b(
            SourceInput,
            code="SYN-FD",
            name="Synthetic Fiber Designer",
            instance_id="synthetic-designer",
            project_id="approved-project",
        ),
    )
    source = db.get(Source, src["id"])
    s.configure(
        db,
        user,
        source,
        b(ConfigureInput, expected_revision=source.revision, enabled=True),
    )
    for key, kind, id in [
        ("core", "site", a.id),
        ("tower", "site", z.id),
        ("customer", "service", service.id),
    ]:
        s.bind(
            db,
            user,
            source,
            b(
                BindingInput,
                kind="node",
                external_id=key,
                reference_kind=kind,
                reference_id=id,
            ),
        )
    records = [
        rec("node", "core", role="core", label="Synthetic core"),
        rec("node", "tower", role="tower", label="Synthetic tower"),
        rec("node", "customer", role="customer", label="Synthetic service endpoint"),
        rec(
            "connection",
            "feed",
            a_ref="core",
            z_ref="tower",
            label="Approved fiber feed",
        ),
        rec(
            "cable",
            "feed-cable",
            connection_ref="feed",
            code="FEED-CABLE",
            strand_count=4,
            geometry=[[-83.5, 41.5], [-83.49, 41.51]],
        ),
        rec(
            "route",
            "route-group",
            name="Approved operational route",
            geometry=[[-83.5, 41.5], [-83.49, 41.51]],
            segment_refs=["feed-cable"],
        ),
        rec(
            "construction",
            "completion",
            state="completed",
            completed_at=(now() - timedelta(seconds=1)).isoformat(),
            component_refs=["feed-cable"],
        ),
        rec("as_built", "as-built", component_refs=["feed-cable", "route-group"]),
    ]
    monkeypatch.setattr(settings, "fiber_designer_ingest_enabled", False)
    monkeypatch.setattr(settings, "fiber_designer_worker_enabled", False)
    key = tmp_path / "vault.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "fiber_designer_key_file", str(key))
    return SimpleNamespace(
        db=db,
        user=user,
        a=a,
        z=z,
        source=source,
        service=service,
        account=account,
        records=records,
    )


def preview(x, env=None):
    return s.preview(x.db, x.user, x.source, env or envelope(x), R)


def apply(x, env=None):
    r = preview(x, env)
    assert r["status"] == "preview", r["report"]
    out = s.approve(
        x.db,
        x.user,
        x.db.get(Run, r["id"]),
        b(ApprovalInput, plan_digest=r["plan_digest"], approve_documentation_only=True),
    )
    assert out["status"] == "applied", out
    return x.db.get(Run, r["id"])


def binding(x, kind, id):
    return x.db.scalar(
        select(Binding).where(
            Binding.source_id == x.source.id,
            Binding.kind == kind,
            Binding.external_id == id,
        )
    )


def auth(user):
    return {
        "Authorization": "Bearer " + create_access_token(user.username, user_id=user.id)
    }


def client(x):
    x.db.commit()
    app.dependency_overrides[get_db] = lambda: x.db
    return TestClient(app, base_url="https://noc.example.invalid")


def test_preview_has_no_operational_effect_and_requires_approval(x):
    r = preview(x)
    assert r["status"] == "preview"
    assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 0
    assert x.db.scalar(select(func.count()).select_from(Version)) == 0
    assert x.source.applied_cursor == 0
    assert all(not a.get("target_id") for a in r["report"]["changes"])
    assert r["report"]["dry_run"]


def test_approved_import_uses_canonical_inventory_and_provenance(x):
    r = apply(x)
    assert x.source.applied_cursor == 1
    assert x.db.scalar(select(func.count()).select_from(Site)) == 2
    cable = x.db.get(m.Cable, binding(x, "cable", "feed-cable").cable_id)
    assert cable.strand_count == 4
    assert cable.geometry == [[-83.5, 41.5], [-83.49, 41.51]]
    assert x.db.scalar(select(func.count()).select_from(m.End)) == 8
    assert x.db.scalar(select(func.count()).select_from(Version)) == len(x.records)
    assert (
        s.provenance(x.db, x.user, node_id=binding(x, "node", "tower").node_id)[
            "items"
        ][0]["approved_revision"]
        == 1
    )
    assert all(
        x.db.get(m.Node, b.node_id).state == "installed"
        for b in x.db.scalars(select(Binding).where(Binding.kind == "node"))
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"publication": "draft"},
        {"publication": "unpublished"},
        {"infrastructure_status": "planned"},
        {"infrastructure_status": "under_construction"},
        {"revision": 0},
        {"previous_revision": 1},
        {"approved_at": "2025-01-01T00:00:00"},
        {"password": "never-accepted"},
    ],
)
def test_schema_rejects_drafts_unapproved_or_unsafe_fields(bad):
    with pytest.raises(ValidationError):
        NodeRecord.model_validate(
            rec("node", "n", role="tower", label="Synthetic", **bad)
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"schema_version": "v0"},
        {"to_cursor": 0},
        {"generated_at": "2999-01-01T00:00:00Z"},
        {"from_cursor": -1},
    ],
)
def test_invalid_envelope_contract(x, bad):
    d = envelope(x).model_dump(mode="json")
    d.update(bad)
    with pytest.raises(ValidationError):
        Envelope.model_validate(d)


def test_duplicate_identifiers_and_strand_budget_rejected(x):
    data = envelope(x).model_dump(mode="json")
    data["records"] = [x.records[0]] * 2
    with pytest.raises(ValidationError):
        Envelope.model_validate(data)
    data["records"] = [
        rec(
            "cable",
            str(i),
            connection_ref="edge",
            code="SYN-CABLE",
            strand_count=1728,
            geometry=[[0, 0], [1, 1]],
        )
        for i in range(6)
    ]
    with pytest.raises(ValidationError):
        Envelope.model_validate(data)


def test_duplicate_delivery_and_revision_replays_do_not_duplicate_records(x):
    env = envelope(x)
    r = apply(x, env)
    assert s.preview(x.db, x.user, x.source, env, R)["id"] == r.id
    again = s.approve(
        x.db,
        x.user,
        r,
        b(ApprovalInput, plan_digest=r.plan_digest, approve_documentation_only=True),
    )
    assert again["status"] == "applied"
    assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 1
    data = env.model_dump(mode="json")
    data.update(envelope_id=str(uuid4()), from_cursor=1, to_cursor=2)
    nextenv = Envelope.model_validate(data)
    apply(x, nextenv)
    assert x.source.applied_cursor == 2
    assert x.db.scalar(select(func.count()).select_from(Version)) == len(x.records)


def updated(x):
    original = next(r for r in x.records if r["kind"] == "cable")
    r = {
        **original,
        "revision": 2,
        "previous_revision": 1,
        "geometry": [[-83.5, 41.5], [-83.495, 41.507], [-83.49, 41.51]],
    }
    return Envelope(
        schema_version="nwo.fiber-operational.v1",
        envelope_id=uuid4(),
        instance_id=x.source.instance_id,
        project_id=x.source.project_id,
        from_cursor=1,
        to_cursor=2,
        generated_at=now(),
        records=[r],
    )


def test_incremental_updates_preserve_operational_state(x):
    apply(x)
    edge = x.db.get(m.Edge, binding(x, "connection", "feed").edge_id)
    edge.state = "out_of_service"
    x.db.flush()
    r = apply(x, updated(x))
    assert edge.state == "out_of_service"
    assert (
        len(x.db.get(m.Cable, binding(x, "cable", "feed-cable").cable_id).geometry) == 3
    )
    assert r.approved_by == x.user.id


def test_operator_change_is_a_conflict_not_silent_overwrite(x):
    apply(x)
    cable = x.db.get(m.Cable, binding(x, "cable", "feed-cable").cable_id)
    cable.geometry = [[-83.5, 41.5], [-83.48, 41.52]]
    cable.revision += 1
    x.db.flush()
    r = preview(x, updated(x))
    assert r["status"] == "conflict"
    assert (
        r["report"]["conflicts"][0]["code"] == "operator_target_changed_review_mapping"
    )
    assert cable.geometry[-1] == [-83.48, 41.52]


def test_stale_preview_and_explicit_repreview(x):
    env = envelope(x)
    r = preview(x, env)
    x.a.latitude = 41.52
    x.db.flush()
    with pytest.raises(s.Conflict, match="stale_preview"):
        s.approve(
            x.db,
            x.user,
            x.db.get(Run, r["id"]),
            b(
                ApprovalInput,
                plan_digest=r["plan_digest"],
                approve_documentation_only=True,
            ),
        )
    fresh = s.preview(x.db, x.user, x.source, env, R, new_attempt=True)
    assert fresh["id"] != r["id"]
    assert fresh["attempt"] == 2
    assert fresh["plan_digest"] != r["plan_digest"]


def test_missing_mapping_and_bad_topology_are_atomic_conflicts(x):
    r = rec("node", "unmapped", role="cabinet", label="Synthetic cabinet")
    env = Envelope.model_validate(
        {**envelope(x).model_dump(mode="json"), "records": [r]}
    )
    out = preview(x, env)
    assert out["status"] == "conflict"
    assert x.db.scalar(select(func.count()).select_from(m.Node)) == 0
    env = Envelope.model_validate(
        {
            **envelope(x).model_dump(mode="json"),
            "records": x.records
            + [
                rec(
                    "cable",
                    "invalid",
                    connection_ref="missing",
                    code="SYN-MISSING",
                    strand_count=2,
                    geometry=[[0, 0], [1, 1]],
                )
            ],
        }
    )
    out = preview(x, env)
    assert out["status"] == "conflict"
    assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 0


def test_same_revision_with_different_content_and_wrong_cursor_conflict(x):
    apply(x)
    d = envelope(x).model_dump(mode="json")
    d.update(envelope_id=str(uuid4()), from_cursor=1, to_cursor=2)
    d["records"][0]["label"] = "Changed without revision"
    r = preview(x, Envelope.model_validate(d))
    assert r["report"]["conflicts"][0]["code"] == "same_revision_different_content"
    d["from_cursor"] = 0
    r = preview(x, Envelope.model_validate({**d, "envelope_id": str(uuid4())}))
    assert r["report"]["conflicts"][0]["code"] == "source_cursor_mismatch"


def test_rollback_update_restores_geometry_without_rewinding_revision_or_cursor(x):
    apply(x)
    cable = x.db.get(m.Cable, binding(x, "cable", "feed-cable").cable_id)
    old = cable.geometry
    r = apply(x, updated(x))
    rev = cable.revision
    p = s.rollback_preview(x.db, x.user, r)
    assert not p["conflicts"]
    s.rollback(
        x.db,
        x.user,
        r,
        b(ApprovalInput, plan_digest=p["plan_digest"], approve_documentation_only=True),
    )
    assert cable.geometry == old
    assert binding(x, "cable", "feed-cable").operational_revision == 1
    assert binding(x, "cable", "feed-cable").current_revision == 2
    assert cable.revision > rev
    assert x.source.applied_cursor == 2
    assert x.source.recovery_required
    assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 1


def test_rollback_new_records_retains_history_and_disables_documented_infrastructure(x):
    r = apply(x)
    p = s.rollback_preview(x.db, x.user, r)
    assert not p["conflicts"]
    s.rollback(
        x.db,
        x.user,
        r,
        b(ApprovalInput, plan_digest=p["plan_digest"], approve_documentation_only=True),
    )
    assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 1
    assert x.db.scalar(select(func.count()).select_from(Version)) == len(x.records)
    assert (
        x.db.get(m.Edge, binding(x, "connection", "feed").edge_id).state
        == "out_of_service"
    )
    assert x.source.recovery_required
    assert not binding(x, "node", "core").active
    assert binding(x, "node", "core").operational_revision == 0


def test_rollback_after_operator_change_is_blocked(x):
    r = apply(x)
    edge = x.db.get(m.Edge, binding(x, "connection", "feed").edge_id)
    edge.state = "out_of_service"
    x.db.flush()
    p = s.rollback_preview(x.db, x.user, r)
    assert p["conflicts"]
    with pytest.raises(s.Conflict):
        s.rollback(
            x.db,
            x.user,
            r,
            b(
                ApprovalInput,
                plan_digest=p["plan_digest"],
                approve_documentation_only=True,
            ),
        )


def test_native_history_append_only_and_binding_foreign_key(x):
    r = apply(x)
    v = x.db.scalar(select(Version).limit(1))
    with pytest.raises(DBAPIError):
        with x.db.begin_nested():
            x.db.execute(
                text("update fiber_sync_versions set revision=99 where id=:id"),
                {"id": v.id},
            )
    with pytest.raises(DBAPIError):
        with x.db.begin_nested():
            x.db.execute(
                text("delete from fiber_sync_changes where run_id=:id"), {"id": r.id}
            )


@pytest.mark.parametrize("role", ["viewer", "technician", "operator", "admin"])
def test_private_authentication_and_approval_roles(x, role):
    x.user.role = role
    c = client(x)
    try:
        assert c.get("/api/fiber-designer/v1/sources").status_code == 401
        assert c.get(
            "/api/fiber-designer/v1/sources", headers=auth(x.user)
        ).status_code == (200 if role in {"admin", "operator"} else 403)
        payload = b(
            SourceInput,
            code="SYN-OTHER",
            name="Synthetic other source",
            instance_id="other-instance",
            project_id="other-project",
        ).model_dump(mode="json")
        assert c.post(
            "/api/fiber-designer/v1/sources", json=payload, headers=auth(x.user)
        ).status_code == (200 if role == "admin" else 403)
    finally:
        app.dependency_overrides.clear()
        c.close()


def test_signed_ingest_is_stage_only_and_credential_is_encrypted(x, monkeypatch):
    monkeypatch.setattr(settings, "fiber_designer_ingest_enabled", True)
    x.source.ingest_enabled = True
    secret = "synthetic-source-signing-secret-1234567890"
    security.set_secret(x.source, secret)
    assert secret not in x.source.encrypted_secret
    c = client(x)
    try:
        raw = json.dumps(
            envelope(x).model_dump(mode="json"), separators=(",", ":")
        ).encode()
        stamp = str(int(time.time()))
        nonce = str(uuid4())
        headers = {
            "content-type": "application/json",
            "x-fiber-timestamp": stamp,
            "x-fiber-nonce": nonce,
            "x-fiber-signature": security.sign(secret, x.source.id, stamp, nonce, raw),
        }
        r = c.post(
            f"/api/fiber-designer-ingest/v1/sources/{x.source.id}/exports",
            content=raw,
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "preview"
        assert x.source.applied_cursor == 0
        assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 0
        assert (
            c.post(
                f"/api/fiber-designer-ingest/v1/sources/{x.source.id}/exports",
                content=raw,
                headers=headers,
            ).status_code
            == 409
        )
        assert (
            secret
            not in c.get("/api/fiber-designer/v1/sources", headers=auth(x.user)).text
        )
    finally:
        app.dependency_overrides.clear()
        c.close()


@pytest.mark.parametrize(
    "failure", ["bad_signature", "expired", "unknown_source", "plain_http", "disabled"]
)
def test_signed_transport_failures_never_apply(x, monkeypatch, failure):
    monkeypatch.setattr(settings, "fiber_designer_ingest_enabled", True)
    x.source.ingest_enabled = True
    secret = "synthetic-source-signing-secret-1234567890"
    security.set_secret(x.source, secret)
    c = client(x)
    try:
        raw = json.dumps(envelope(x).model_dump(mode="json")).encode()
        stamp = str(int(time.time()) - (1000 if failure == "expired" else 0))
        nonce = str(uuid4())
        source = x.source.id if failure != "unknown_source" else 99999
        sig = security.sign(secret, source, stamp, nonce, raw)
        sig = "0" * 64 if failure == "bad_signature" else sig
        if failure == "disabled":
            monkeypatch.setattr(settings, "fiber_designer_ingest_enabled", False)
        url = f"/api/fiber-designer-ingest/v1/sources/{source}/exports"
        if failure == "plain_http":
            url = "http://noc.example.invalid" + url
        r = c.post(
            url,
            content=raw,
            headers={
                "x-fiber-timestamp": stamp,
                "x-fiber-nonce": nonce,
                "x-fiber-signature": sig,
            },
        )
        assert r.status_code in {401, 403, 409}
        assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 0
    finally:
        app.dependency_overrides.clear()
        c.close()


def test_scheduler_disabled_by_default_and_stages_without_applying(x, monkeypatch):
    assert worker.tick(x.db)["staged"] == 0
    monkeypatch.setattr(settings, "fiber_designer_worker_enabled", True)
    monkeypatch.setattr(settings, "offline_mode", True)
    monkeypatch.setattr(settings, "synthetic_monitoring_enabled", True)
    monkeypatch.setattr(settings, "app_env", "staging")
    x.source.adapter = "mock"
    x.source.schedule_enabled = True
    x.source.next_sync_at = now() - timedelta(seconds=1)
    x.source.fixture = envelope(x).model_dump(mode="json")
    x.db.commit()
    r = worker.tick(x.db)
    assert r["staged"] == 1
    assert not r["automatic_apply"]
    assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 0
    assert x.source.applied_cursor == 0
    # Durable repeat delivery deduplicates the existing preview, not a second import.
    x.source.next_sync_at = now() - timedelta(seconds=1)
    x.db.commit()
    again = worker.tick(x.db)
    assert again["run_id"] == r["run_id"]


def test_scheduler_recovers_after_malformed_export(x, monkeypatch):
    monkeypatch.setattr(settings, "fiber_designer_worker_enabled", True)
    monkeypatch.setattr(settings, "offline_mode", True)
    monkeypatch.setattr(settings, "synthetic_monitoring_enabled", True)
    monkeypatch.setattr(settings, "app_env", "staging")
    x.source.adapter = "mock"
    x.source.schedule_enabled = True
    x.source.next_sync_at = now()
    x.source.fixture = {"malformed": True}
    x.db.commit()
    assert worker.tick(x.db)["error_code"] == "scheduled_export_validation_failed"
    assert x.source.applied_cursor == 0
    x.source.fixture = envelope(x).model_dump(mode="json")
    x.source.next_sync_at = now()
    x.db.commit()
    assert worker.tick(x.db)["staged"] == 1


def connected_customer(x):
    return x.records + [
        rec(
            "connection",
            "access",
            a_ref="tower",
            z_ref="customer",
            label="Approved customer fiber drop",
        ),
        rec(
            "cable",
            "drop-cable",
            connection_ref="access",
            code="DROP-CABLE",
            strand_count=2,
            geometry=[[-83.49, 41.51], [-83.488, 41.511]],
        ),
        rec(
            "circuit",
            "service-circuit",
            code="SERVICE-CIRCUIT",
            service_node_ref="customer",
            purpose="residential",
            bandwidth_mbps=100,
        ),
        rec(
            "assignment",
            "feed-strand",
            cable_ref="feed-cable",
            strand=1,
            circuit_ref="service-circuit",
        ),
        rec(
            "assignment",
            "drop-strand",
            cable_ref="drop-cable",
            strand=1,
            circuit_ref="service-circuit",
        ),
        rec(
            "splice",
            "tower-splice",
            node_ref="tower",
            splice_kind="splice",
            ends=[
                {"cable_ref": "feed-cable", "strand": 1, "side": "z"},
                {"cable_ref": "drop-cable", "strand": 1, "side": "a"},
            ],
        ),
        rec(
            "path",
            "service-path",
            circuit_ref="service-circuit",
            source_ref="core",
            destination_ref="customer",
            hops=[
                {"connection_ref": "feed", "cable_ref": "feed-cable", "strand": 1},
                {"connection_ref": "access", "cable_ref": "drop-cable", "strand": 1},
            ],
        ),
    ]


def test_strand_splice_customer_paths_and_incident_impact_reuse(x):
    apply(x, envelope(x, connected_customer(x)))
    path = x.db.get(m.Path, binding(x, "path", "service-path").path_id)
    assert path.verified_until is None
    edge = binding(x, "connection", "feed").edge_id
    impact = u.impact(x.db, failed_edges=[edge])
    assert x.service.id in impact["estimated_services"]
    assert not impact["preserved_services"]
    assert impact["model_not_observed_outage"]
    incident = Incident(
        title="Synthetic monitoring failure, cause unconfirmed",
        site_id=x.z.id,
        severity="critical",
        started_at=now(),
    )
    x.db.add(incident)
    x.db.flush()
    r = u.incident_impact(x.db, x.user, incident.id)
    assert x.service.id in r["estimated_services"]
    assert r["root_cause"] == "not established by topology analysis"
    assert x.service.site_id == x.z.id


def test_splice_mapping_cannot_claim_optical_continuity_or_rewire_silently(x):
    apply(x, envelope(x, connected_customer(x)))
    j = x.db.get(m.Join, binding(x, "splice", "tower-splice").join_id)
    assert j.optical_tested_at is None
    changed = rec(
        "splice",
        "tower-splice",
        revision=2,
        previous_revision=1,
        node_ref="tower",
        splice_kind="patch",
        ends=[
            {"cable_ref": "feed-cable", "strand": 1, "side": "z"},
            {"cable_ref": "drop-cable", "strand": 1, "side": "a"},
        ],
    )
    env = Envelope.model_validate(
        {
            **envelope(x).model_dump(mode="json"),
            "from_cursor": 1,
            "to_cursor": 2,
            "records": [changed],
        }
    )
    r = preview(x, env)
    assert r["status"] == "conflict"
    assert j.kind == "splice"


def test_operator_strand_reservation_blocks_source_assignment(x):
    apply(x)
    cable = x.db.get(m.Cable, binding(x, "cable", "feed-cable").cable_id)
    strand = x.db.scalar(
        select(m.Strand).where(m.Strand.cable_id == cable.id, m.Strand.number == 1)
    )
    circuit = u.circuit(
        x.db,
        x.user,
        b(
            uc.CircuitInput,
            code="NOC-RESERVATION",
            purpose="infrastructure",
            state="active",
            completeness_evidence=R,
        ),
    )
    u.reserve(
        x.db,
        x.user,
        b(
            uc.StrandInput,
            strand_ids=[strand.id],
            expected_revisions={strand.id: strand.revision},
            action="reserve",
            circuit_id=circuit["id"],
        ),
    )
    env = Envelope.model_validate(
        {
            **envelope(x).model_dump(mode="json"),
            "from_cursor": 1,
            "to_cursor": 2,
            "records": [
                rec(
                    "circuit",
                    "planned-circuit",
                    code="SYN-PLANNED",
                    purpose="transport",
                ),
                rec(
                    "assignment",
                    "claim",
                    cable_ref="feed-cable",
                    strand=1,
                    circuit_ref="planned-circuit",
                ),
            ],
        }
    )
    r = preview(x, env)
    assert r["status"] == "conflict"
    assert r["report"]["conflicts"][0]["code"] == "strand_reserved_or_assigned_by_noc"
    assert strand.state == "reserved"


def test_atomic_failure_is_reviewable_and_recoverable(x, monkeypatch):
    r = preview(x)
    native = u.create_cable

    def fail(*a, **k):
        raise ValueError("Synthetic import interruption")

    monkeypatch.setattr(u, "create_cable", fail)
    run = x.db.get(Run, r["id"])
    out = s.approve(
        x.db,
        x.user,
        run,
        b(ApprovalInput, plan_digest=r["plan_digest"], approve_documentation_only=True),
    )
    assert out["status"] == "failed"
    assert x.db.scalar(select(func.count()).select_from(m.Node)) == 0
    assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 0
    assert x.source.applied_cursor == 0
    monkeypatch.setattr(u, "create_cable", native)
    r2 = s.preview(
        x.db,
        x.user,
        x.source,
        Envelope.model_validate(run.payload),
        R,
        new_attempt=True,
    )
    assert r2["status"] == "preview"
    assert (
        s.approve(
            x.db,
            x.user,
            x.db.get(Run, r2["id"]),
            b(
                ApprovalInput,
                plan_digest=r2["plan_digest"],
                approve_documentation_only=True,
            ),
        )["status"]
        == "applied"
    )


def equipment(x, tag):
    from app.equipment import service as es
    from app.equipment.contracts import (
        CategoryInput,
        ProductInput,
        LocationInput,
        AssetInput,
    )

    if not hasattr(x, "product"):
        category = es.create_category(
            x.db,
            CategoryInput(
                code="fiber-test", name="Synthetic fiber equipment", reason=R
            ),
            x.user.id,
        )
        x.product = es.create_product(
            x.db,
            ProductInput(
                manufacturer="Synthetic",
                model="Synthetic passive/GPON asset",
                category_id=category.id,
                reason=R,
            ),
            x.user.id,
        )
        x.location = es.create_location(
            x.db,
            LocationInput(
                code="fiber-site",
                name="Synthetic installed fiber stock",
                kind="tower",
                site_id=x.z.id,
                reason=R,
            ),
            x.user.id,
        )
    return es.create_asset(
        x.db,
        AssetInput(
            product_id=x.product.id,
            asset_tag=tag,
            location_id=x.location.id,
            state="installed",
            condition="good",
            reason=R,
        ),
        x.user.id,
    )


def test_gpon_olt_splitter_ont_uses_existing_assets_and_ports(x):
    records = list(x.records)
    for name, role in [("olt", "olt"), ("splitter", "splitter"), ("ont", "ont")]:
        asset = equipment(x, "SYN-" + name.upper())
        if role == "ont":
            from app.equipment import service as es
            from app.equipment.contracts import LocationInput

            location = es.create_location(
                x.db,
                LocationInput(
                    code="fiber-cpe",
                    name="Synthetic customer premises",
                    kind="customer_premises",
                    service_id=x.service.id,
                    reason=R,
                ),
                x.user.id,
            )
            asset.location_id = location.id
            asset.state = "failed"
            x.db.flush()
        s.bind(
            x.db,
            x.user,
            x.source,
            b(
                BindingInput,
                kind="node",
                external_id=name,
                reference_kind="asset",
                reference_id=asset.id,
            ),
        )
        records.append(rec("node", name, role=role, label="Synthetic " + role))
    records += [
        rec(
            "port",
            "pon",
            node_ref="olt",
            label="PON1",
            port_kind="pon",
            capacity_mbps=2500,
            subscriber_limit=32,
        ),
        rec(
            "port",
            "split-in",
            node_ref="splitter",
            label="IN",
            port_kind="splitter_input",
            split_ratio=8,
        ),
        rec(
            "port",
            "split-out",
            node_ref="splitter",
            label="OUT1",
            port_kind="splitter_output",
        ),
        rec("port", "ont-in", node_ref="ont", label="ONT-PON", port_kind="ont"),
        rec(
            "connection",
            "pon-link",
            a_ref="olt",
            z_ref="splitter",
            a_port_ref="pon",
            z_port_ref="split-in",
            label="Approved PON relationship",
            technology="gpon",
            relation="pon_splitter",
        ),
        rec(
            "connection",
            "ont-link",
            a_ref="splitter",
            z_ref="ont",
            a_port_ref="split-out",
            z_port_ref="ont-in",
            label="Approved ONT relationship",
            technology="gpon",
            relation="splitter_ont",
        ),
    ]
    apply(x, envelope(x, records))
    assert x.db.get(m.Port, binding(x, "port", "split-in").port_id).split_ratio == 8
    assert (
        x.db.get(m.Edge, binding(x, "connection", "pon-link").edge_id).relation
        == "pon_splitter"
    )
    from app.equipment.models import Asset

    assert x.db.scalar(select(func.count()).select_from(Asset)) == 3
    # An approved export cannot silently install warehouse/retired stock in canonical NOC inventory.
    mapped = binding(x, "node", "olt")
    asset = x.db.get(Asset, mapped.asset_id)
    asset.state = "removed"
    x.db.flush()
    asset.state = "available"
    x.db.flush()
    changed = next(
        r for r in records if r["kind"] == "node" and r["external_id"] == "olt"
    )
    changed = {**changed, "revision": 2, "previous_revision": 1}
    update = Envelope.model_validate(
        {
            **envelope(x).model_dump(mode="json"),
            "from_cursor": 1,
            "to_cursor": 2,
            "records": [changed],
        }
    )
    conflict = preview(x, update)
    assert (
        conflict["report"]["conflicts"][0]["code"]
        == "canonical_asset_not_installed_reconcile_inventory"
    )
    assert asset.state == "available"


def test_hybrid_path_can_reference_existing_noc_wireless_without_importing_engineering(
    x,
):
    # Explicit NOC mappings can reference existing wireless transports, which remain NOC-owned.
    core = u.create_node(x.db, x.user, b(uc.NodeInput, kind="core", site_id=x.a.id))
    tower = u.create_node(x.db, x.user, b(uc.NodeInput, kind="tower", site_id=x.z.id))
    customer = u.create_node(
        x.db, x.user, b(uc.NodeInput, kind="customer", service_id=x.service.id)
    )
    link = u.create_edge(
        x.db,
        x.user,
        b(
            uc.EdgeInput,
            label="Existing NOC wireless access",
            a_id=tower["id"],
            z_id=customer["id"],
            layer="physical",
            relation="access",
            technology="wireless",
            state="installed",
            evidence=R,
        ),
    )
    s.bind(
        x.db,
        x.user,
        x.source,
        b(
            BindingInput,
            kind="connection",
            external_id="noc-wireless",
            reference_kind="connection",
            reference_id=link["id"],
        ),
    )
    records = x.records + [
        rec(
            "circuit",
            "hybrid-service",
            code="HYBRID-SERVICE",
            service_node_ref="customer",
            purpose="residential",
        ),
        rec(
            "assignment",
            "hybrid-strand",
            cable_ref="feed-cable",
            strand=1,
            circuit_ref="hybrid-service",
        ),
        rec(
            "path",
            "hybrid-path",
            circuit_ref="hybrid-service",
            source_ref="core",
            destination_ref="customer",
            hops=[
                {"connection_ref": "feed", "cable_ref": "feed-cable", "strand": 1},
                {"connection_ref": "noc-wireless"},
            ],
        ),
    ]
    apply(x, envelope(x, records))
    assert x.db.get(m.Edge, link["id"]).technology == "wireless"
    assert (
        x.db.get(m.Path, binding(x, "path", "hybrid-path").path_id).verified_until
        is None
    )


def test_existing_powercode_customer_mapping_and_current_verified_alternate(x):
    apply(x, envelope(x, connected_customer(x)))
    circuit = x.db.get(m.Circuit, binding(x, "circuit", "service-circuit").circuit_id)
    source = binding(x, "node", "core").node_id
    dest = binding(x, "node", "customer").node_id
    alt = u.create_edge(
        x.db,
        x.user,
        b(
            uc.EdgeInput,
            label="NOC verified wireless alternative",
            a_id=source,
            z_id=dest,
            technology="wireless",
            layer="physical",
            relation="transport",
            state="installed",
            evidence=R,
        ),
    )
    u.create_path(
        x.db,
        x.user,
        b(
            uc.PathInput,
            circuit_id=circuit.id,
            name="NOC independently verified alternate",
            state="active",
            source_id=source,
            destination_id=dest,
            hops=[uc.HopInput(edge_id=alt["id"])],
            verified_until=now() + timedelta(minutes=5),
            verification_evidence=R,
        ),
    )
    impact = u.impact(x.db, failed_edges=[binding(x, "connection", "feed").edge_id])
    assert x.service.id in impact["preserved_services"]
    assert x.service.id not in impact["estimated_services"]
    assert x.account.id not in impact["customer_ids_for_approval"]


def test_compensation_cannot_revive_imported_or_previous_functioning_proof(x):
    apply(x, envelope(x, connected_customer(x)))
    p = x.db.get(m.Path, binding(x, "path", "service-path").path_id)
    assert p.verified_until is None
    source = next(r for r in connected_customer(x) if r["kind"] == "path")
    source.update(revision=2, previous_revision=1)
    env = Envelope.model_validate(
        {
            **envelope(x).model_dump(mode="json"),
            "from_cursor": 1,
            "to_cursor": 2,
            "records": [source],
        }
    )
    r = apply(x, env)
    new = x.db.get(m.Path, binding(x, "path", "service-path").path_id)
    assert new.id != p.id
    plan = s.rollback_preview(x.db, x.user, r)
    assert not plan["conflicts"]
    s.rollback(
        x.db,
        x.user,
        r,
        b(
            ApprovalInput,
            plan_digest=plan["plan_digest"],
            approve_documentation_only=True,
        ),
    )
    assert p.verified_until is None
    assert new.verified_until is None


def test_future_rewire_and_strand_count_changes_require_migration(x):
    apply(x)
    bad = updated(x).model_dump(mode="json")
    bad["records"][0]["strand_count"] = 8
    r = preview(x, Envelope.model_validate(bad))
    assert r["status"] == "conflict"
    assert (
        r["report"]["conflicts"][0]["code"]
        == "cable_rewire_or_strand_count_requires_migration"
    )


def test_bad_signed_draft_keeps_nonce_and_failure_history_without_operational_writes(
    x, monkeypatch
):
    monkeypatch.setattr(settings, "fiber_designer_ingest_enabled", True)
    x.source.ingest_enabled = True
    secret = "synthetic-source-signing-secret-1234567890"
    security.set_secret(x.source, secret)
    c = client(x)
    try:
        data = envelope(x).model_dump(mode="json")
        data["records"][0]["publication"] = "draft"
        raw = json.dumps(data).encode()
        stamp = str(int(time.time()))
        nonce = str(uuid4())
        headers = {
            "x-fiber-timestamp": stamp,
            "x-fiber-nonce": nonce,
            "x-fiber-signature": security.sign(secret, x.source.id, stamp, nonce, raw),
        }
        url = f"/api/fiber-designer-ingest/v1/sources/{x.source.id}/exports"
        assert c.post(url, content=raw, headers=headers).status_code == 409
        assert x.db.get(Nonce, (x.source.id, nonce))
        assert (
            x.db.scalar(select(Run).where(Run.status == "failed")).report[
                "failure_code"
            ]
            == "signed_contract_rejected"
        )
        assert x.db.scalar(select(func.count()).select_from(m.Cable)) == 0
        assert c.post(url, content=raw, headers=headers).status_code == 409
    finally:
        app.dependency_overrides.clear()
        c.close()


def test_signed_key_is_not_an_operator_approval_credential(x, monkeypatch):
    c = client(x)
    try:
        payload = b(
            ApprovalInput, plan_digest="a" * 64, approve_documentation_only=True
        ).model_dump(mode="json")
        assert (
            c.post(
                "/api/fiber-designer/v1/runs/1/approve",
                headers={"Authorization": "Bearer synthetic-source-signing-secret"},
                json=payload,
            ).status_code
            == 401
        )
    finally:
        app.dependency_overrides.clear()
        c.close()


def test_document_work_order_and_ipam_references_reuse_existing_noc_records(x):
    from app.maintenance_ops.models import Category, Document
    from app.ipam.models import VRF, Prefix, Interface
    from app.field import service as fs
    from app.field.contracts import WorkInput

    category = Category(code="asbuilt-tests", name="Synthetic approved as-built")
    x.db.add(category)
    vrf = VRF(name="Synthetic documented fiber VRF")
    x.db.add(vrf)
    device = Device(
        source="manual",
        external_id="fiber-ref-device",
        name="Synthetic operational OLT monitor",
        wisp_site_id=x.z.id,
        enabled=False,
    )
    x.db.add(device)
    x.db.flush()
    doc = Document(
        site_id=x.z.id,
        category_id=category.id,
        title="Synthetic approved drawing",
        visibility="admin",
        approved_revision=1,
        created_by=x.user.id,
    )
    prefix = Prefix(
        vrf_id=vrf.id,
        network="192.0.2.0/24",
        site_id=x.z.id,
        kind="subnet",
        scope="private",
    )
    interface = Interface(
        device_id=device.id,
        vrf_id=vrf.id,
        external_id="fiber-if",
        name="Synthetic fiber interface",
    )
    x.db.add_all([doc, prefix, interface])
    x.db.flush()
    work = fs.create_work(
        x.db,
        x.user,
        b(
            WorkInput,
            site_id=x.z.id,
            description="Existing approved fiber inspection, no automatic dispatch",
        ),
    )
    s.bind(
        x.db,
        x.user,
        x.source,
        b(
            BindingInput,
            kind="node",
            external_id="monitor",
            reference_kind="device",
            reference_id=device.id,
        ),
    )
    records = x.records + [
        rec("node", "monitor", role="equipment", label="Existing NOC monitoring device")
    ]
    for external, kind, id, component in [
        ("drawing", "document", doc.id, "tower"),
        ("work", "work_order", work["id"], "tower"),
        ("prefix", "ipam_prefix", prefix.id, "tower"),
        ("interface", "ipam_interface", interface.id, "monitor"),
    ]:
        s.bind(
            x.db,
            x.user,
            x.source,
            b(
                BindingInput,
                kind="reference",
                external_id=external,
                reference_kind=kind,
                reference_id=id,
            ),
        )
        records.append(
            rec(
                "reference",
                external,
                component_kind="node",
                component_ref=component,
                category=kind,
            )
        )
    records.append(
        rec(
            "as_built",
            "documented-revision",
            component_refs=["feed-cable"],
            document_ref="drawing",
        )
    )
    apply(x, envelope(x, records))
    assert x.db.scalar(select(func.count()).select_from(m.Association)) == 2
    assert prefix.network == "192.0.2.0/24" or str(prefix.network) == "192.0.2.0/24"
    assert interface.device_id == device.id
    assert x.db.get(WorkOrder, work["id"]).status == "open"
    operator = User(
        username="fiber-ref-operator",
        email="ref-operator@example.invalid",
        password_hash="unusable",
        role="operator",
        is_active=True,
    )
    x.db.add(operator)
    x.db.flush()
    node = binding(x, "node", "tower").node_id
    result = u.node_context(x.db, operator, node)
    assert all(a["document_id"] != doc.id for a in result["associations"])


def test_preview_receipts_are_scoped_to_source_not_just_body(x):
    c = client(x)
    try:
        other = s.source_create(
            x.db,
            x.user,
            b(
                SourceInput,
                code="SYN-OTHER",
                name="Other approved source",
                instance_id="other",
                project_id="other-project",
            ),
        )
        x.db.commit()
        payload = b(
            BindingInput,
            kind="node",
            external_id="extra-mapping",
            reference_kind="site",
            reference_id=x.z.id,
        ).model_dump(mode="json")
        assert (
            c.post(
                f"/api/fiber-designer/v1/sources/{x.source.id}/bindings",
                headers=auth(x.user),
                json=payload,
            ).status_code
            == 200
        )
        assert (
            c.post(
                f'/api/fiber-designer/v1/sources/{other["id"]}/bindings',
                headers=auth(x.user),
                json=payload,
            ).status_code
            == 409
        )
        assert (
            x.db.scalar(select(Binding).where(Binding.source_id == other["id"])) is None
        )
    finally:
        app.dependency_overrides.clear()
        c.close()


def test_compensation_blocks_operator_optical_termination_and_new_paths(x):
    r = apply(x)
    strand = x.db.scalar(
        select(m.Strand).where(
            m.Strand.cable_id == binding(x, "cable", "feed-cable").cable_id
        )
    )
    end = x.db.scalar(
        select(m.End).where(m.End.strand_id == strand.id, m.End.side == "a")
    )
    port = u.create_port(
        x.db,
        x.user,
        b(
            uc.PortInput,
            node_id=end.node_id,
            label="NOC-owned optical termination",
            kind="fiber",
            evidence=R,
        ),
    )
    u.map_end(
        x.db,
        x.user,
        b(
            uc.EndMapping,
            end_id=end.id,
            port_id=port["id"],
            expected_strand_revision=strand.revision,
        ),
    )
    plan = s.rollback_preview(x.db, x.user, r)
    assert any(
        c["code"] == "operator_optical_terminations_block_compensation"
        for c in plan["conflicts"]
    )


def test_infrastructure_state_and_schema_are_required(x):
    data = envelope(x).model_dump(mode="json")
    del data["records"][0]["infrastructure_status"]
    with pytest.raises(ValidationError):
        Envelope.model_validate(data)
    c = client(x)
    try:
        r = c.get("/api/fiber-designer/v1/contract", headers=auth(x.user))
        assert r.status_code == 200
        assert not r.json()["live_adapter_available"]
        assert not r.json()["automatic_apply"]
    finally:
        app.dependency_overrides.clear()
        c.close()


def test_monitoring_telemetry_remains_noc_owned_and_does_not_invalidate_preview(x):
    device = Device(
        source="manual",
        external_id="synthetic-polling-device",
        name="Synthetic observed device",
        wisp_site_id=x.z.id,
        enabled=False,
        status="online",
    )
    x.db.add(device)
    x.db.flush()
    s.bind(
        x.db,
        x.user,
        x.source,
        b(
            BindingInput,
            kind="node",
            external_id="observed-device",
            reference_kind="device",
            reference_id=device.id,
        ),
    )
    env = envelope(
        x,
        x.records
        + [
            rec(
                "node",
                "observed-device",
                role="equipment",
                label="Approved identity, not measured health",
            )
        ],
    )
    p = preview(x, env)
    assert p["status"] == "preview"
    device.status = "offline"
    x.db.flush()
    result = s.approve(
        x.db,
        x.user,
        x.db.get(Run, p["id"]),
        b(ApprovalInput, plan_digest=p["plan_digest"], approve_documentation_only=True),
    )
    assert result["status"] == "applied"
    assert device.status == "offline"


def test_compensated_new_source_adoption_does_not_claim_current_operational_revision(x):
    # Canonical nodes predate the import; compensation must not deactivate them or claim its version remains operational.
    node = u.create_node(x.db, x.user, b(uc.NodeInput, kind="core", site_id=x.a.id))
    r = apply(x)
    plan = s.rollback_preview(x.db, x.user, r)
    assert not plan["conflicts"]
    s.rollback(
        x.db,
        x.user,
        r,
        b(
            ApprovalInput,
            plan_digest=plan["plan_digest"],
            approve_documentation_only=True,
        ),
    )
    bind = binding(x, "node", "core")
    assert bind.current_revision == 1
    assert bind.operational_revision == 0
    assert not bind.active
    assert x.db.get(m.Node, node["id"]).state == "installed"
    assert not s.provenance(x.db, x.user, node_id=node["id"])["items"]
