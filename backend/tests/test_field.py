"""Synthetic field authorization, replay, inventory atomicity and QR acceptance."""

import base64, json
from uuid import uuid4
from datetime import timedelta
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.core.config import settings
from app.models.entities import User, Site, AuditLog
from app.field import service as s
from app.field.models import *
from app.field.contracts import *
from app.equipment import service as inv
from app.equipment.models import Asset, Movement, Maintenance
from app.equipment.contracts import AssetInput, Replacement
from test_equipment import stock, asset, bulk
from test_notifications import network
from test_powercode_customers import inventory, imported
from test_rf_postgres_integration import postgres_db

R = "Synthetic authorized field maintenance documentation"


def body(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


@pytest.fixture
def field(stock, monkeypatch):
    x = stock
    monkeypatch.setattr(
        settings, "field_public_base_url", "https://noc.example.invalid"
    )
    x.tech = User(
        username="field-tech",
        email="tech@example.invalid",
        password_hash=hash_password("synthetic-password"),
        role="technician",
        is_active=True,
    )
    x.other = User(
        username="field-other",
        email="other@example.invalid",
        password_hash="unusable",
        role="technician",
        is_active=True,
    )
    x.db.add_all([x.tech, x.other])
    x.db.flush()
    x.t = s.template(
        x.db,
        x.admin,
        body(
            TemplateInput,
            code="tower",
            name="Tower inspection",
            items=s.default_items(),
        ),
    )
    x.w = s.create_work(
        x.db,
        x.admin,
        body(
            WorkInput,
            site_id=x.a.id,
            technician_id=x.tech.id,
            template_id=x.t["id"],
            description=R,
            incident_id=x.incident.id,
        ),
    )
    return x


def row(x):
    return x.db.get(WorkOrder, x.w["id"])


def start(x):
    x.db.flush()
    return s.update_work(
        x.db,
        x.tech,
        row(x),
        body(Update, expected_revision=row(x).revision, status="in_progress", notes=R),
    )


def inspect(x, **kw):
    w = row(x)
    return s.inspect(
        x.db,
        x.tech,
        w,
        body(
            InspectionInput,
            **(
                {
                    "expected_revision": w.revision,
                    "template_id": x.t["id"],
                    "inspected_at": s.now(),
                    "results": [
                        {"key": i["key"], "outcome": "pass"} for i in s.default_items()
                    ],
                    "notes": R,
                }
                | kw
            ),
        ),
    )


def auth(u):
    return {"Authorization": "Bearer " + create_access_token(u.username, user_id=u.id)}


@pytest.fixture
def client(field):
    x = field
    x.db.commit()
    app.dependency_overrides[get_db] = lambda: x.db
    with TestClient(app) as c:
        yield x, c
    app.dependency_overrides.clear()


@pytest.mark.parametrize("target", ["tower", "asset"])
def test_qr_generation_rename_and_print(field, target):
    x = field
    a = asset(x)
    kw = {"site_id": x.a.id} if target == "tower" else {"asset_id": a.id}
    r = s.label(x.db, x.admin, body(LabelInput, **kw))
    label = x.db.get(QRLabel, r["id"])
    assert s.label_url(label) == "https://noc.example.invalid/?qr=" + label.identifier
    assert s.label_svg(label).startswith(b"<?xml")
    x.a.name = "Synthetic renamed tower"
    x.db.flush()
    if target == "tower":
        assert s.resolve(x.db, x.tech, label.identifier)["site_id"] == x.a.id
    else:
        assert s.resolve(x.db, x.admin, label.identifier)["asset_id"] == a.id
    assert str(a.id) not in label.identifier or len(label.identifier) == 36
    assert "10." not in s.label_url(label)


def test_qr_rotation_revocation_keeps_history(field):
    x = field
    start(x)
    i = inspect(x)
    a = s.label(x.db, x.admin, body(LabelInput, site_id=x.a.id))
    b = s.label(x.db, x.admin, body(LabelInput, site_id=x.a.id, regenerate=True))
    assert a["identifier"] != b["identifier"]
    with pytest.raises(LookupError):
        s.resolve(x.db, x.tech, a["identifier"])
    assert s.resolve(x.db, x.tech, b["identifier"])["site_id"] == x.a.id
    assert x.db.get(Inspection, i["id"]).snapshot["version"] == 1
    s.revoke(x.db, x.admin, b["id"])
    with pytest.raises(LookupError):
        s.resolve(x.db, x.admin, b["identifier"])


def test_unknown_qr(field):
    with pytest.raises(LookupError):
        s.resolve(field.db, field.admin, uuid4())


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "http://noc.example.invalid",
        "https://user:pass@noc.example.invalid",
        "https://noc.example.invalid/path",
        "https://noc.example.invalid?key=x",
        "https://noc.example.invalid:8443",
    ],
)
def test_print_requires_explicit_https_origin(field, monkeypatch, origin):
    monkeypatch.setattr(settings, "field_public_base_url", origin)
    with pytest.raises(ValueError):
        s.origin()


def test_template_version_and_snapshot_immutable(field):
    x = field
    start(x)
    i = inspect(x)
    t = s.template(
        x.db,
        x.admin,
        body(
            TemplateInput,
            code="tower",
            name="Changed inspection",
            items=[
                {
                    "key": "power_new",
                    "label": "Qualified electrical review",
                    "group": "power",
                }
            ],
        ),
    )
    assert t["version"] == 2 and not x.db.get(Template, x.t["id"]).active
    assert len(x.db.get(Inspection, i["id"]).snapshot["items"]) == 23
    assert inspect(x)["template_id"] == x.t["id"]


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown"])
def test_checklist_exact_keys(field, change):
    x = field
    start(x)
    results = [{"key": i["key"], "outcome": "pass"} for i in s.default_items()]
    if change == "missing":
        results.pop()
    if change == "duplicate":
        results.append(results[0])
    if change == "unknown":
        results[0] = {"key": "invented", "outcome": "pass"}
    with pytest.raises(ValueError):
        inspect(x, results=results)


def test_electrical_failure_requires_qualified_followup(field):
    x = field
    start(x)
    results = [
        {
            "key": i["key"],
            "outcome": "fail" if i["group"] == "power" else "not_applicable",
            "notes": "Qualified inspection required",
        }
        for i in s.default_items()
    ]
    assert inspect(x, results=results)["qualified_review"]
    w = row(x)
    with pytest.raises(ValueError):
        s.update_work(
            x.db,
            x.tech,
            w,
            body(Update, expected_revision=w.revision, status="completed", notes=R),
        )
    out = s.update_work(
        x.db,
        x.tech,
        w,
        body(
            Update,
            expected_revision=w.revision,
            status="completed",
            notes=R,
            follow_up="Qualified electrician and structural specialist must assess; no safety certification",
        ),
    )
    assert out["completed_at"] and out["status"] == "completed"


def test_required_inspection_and_closed_work(field):
    x = field
    start(x)
    w = row(x)
    with pytest.raises(ValueError):
        s.update_work(
            x.db,
            x.tech,
            w,
            body(Update, expected_revision=w.revision, status="completed", notes=R),
        )
    inspect(x)
    s.update_work(
        x.db,
        x.tech,
        w,
        body(Update, expected_revision=w.revision, status="completed", notes=R),
    )
    with pytest.raises(ValueError):
        s.note(x.db, x.tech, w, body(Note, expected_revision=w.revision, notes=R))


@pytest.mark.parametrize("status", ["waiting_for_parts", "blocked", "cancelled"])
def test_work_lifecycle(field, status):
    x = field
    start(x)
    w = row(x)
    assert (
        s.update_work(
            x.db,
            x.tech,
            w,
            body(
                Update,
                expected_revision=w.revision,
                status=status,
                notes=R,
                follow_up=R,
            ),
        )["status"]
        == status
    )


def test_admin_assign_schedule_and_stale_revision(field):
    x = field
    w = row(x)
    out = s.update_work(
        x.db,
        x.admin,
        w,
        body(
            Update,
            expected_revision=w.revision,
            status="scheduled",
            notes=R,
            scheduled_at=s.now() + timedelta(days=1),
            technician_id=x.other.id,
        ),
    )
    assert out["technician_id"] == x.other.id
    with pytest.raises(ValueError):
        s.note(x.db, x.admin, w, body(Note, expected_revision=1, notes=R))
    with pytest.raises(PermissionError):
        s.work_access(x.db, x.tech, w.id, True)


def test_technician_no_self_assignment(field):
    x = field
    with pytest.raises(PermissionError):
        s.update_work(
            x.db,
            x.tech,
            row(x),
            body(
                Update,
                expected_revision=1,
                status="assigned",
                notes=R,
                technician_id=x.other.id,
            ),
        )


def test_site_scope_and_grant_revocation(field):
    x = field
    with pytest.raises(PermissionError):
        s.site_access(x.db, x.other, x.a.id)
    s.grant(x.db, x.admin, body(GrantInput, site_id=x.a.id, technician_id=x.other.id))
    assert s.site_access(x.db, x.other, x.a.id).id == x.a.id
    s.grant(
        x.db,
        x.admin,
        body(GrantInput, site_id=x.a.id, technician_id=x.other.id, active=False),
    )
    with pytest.raises(PermissionError):
        s.site_access(x.db, x.other, x.a.id)


def test_incident_and_asset_cross_tower_rejected(field):
    x = field
    with pytest.raises(ValueError):
        s.create_work(
            x.db,
            x.admin,
            body(WorkInput, site_id=x.b.id, incident_id=x.incident.id, description=R),
        )
    a = asset(x)
    with pytest.raises(ValueError):
        s.create_work(
            x.db, x.admin, body(WorkInput, site_id=x.a.id, asset_id=a.id, description=R)
        )
    assert s.work_detail(x.db, x.tech, row(x))["incident"]["id"] == x.incident.id


def test_replay_same_response_and_conflicting_key(field):
    x = field
    start(x)
    b = body(Note, expected_revision=row(x).revision, notes=R)
    result = s.mutation(
        x.db, x.tech, "note", b, lambda: s.note(x.db, x.tech, row(x), b)
    )
    assert (
        s.mutation(x.db, x.tech, "note", b, lambda: pytest.fail("must not execute"))
        == result
    )
    b.notes = "Changed same operation key documentation"
    with pytest.raises(ValueError):
        s.mutation(x.db, x.tech, "note", b, lambda: None)
    assert (
        x.db.scalar(select(func.count(Event.id)).where(Event.kind == "technician_note"))
        == 1
    )


def test_asset_inspection_reuses_maintenance(field):
    x = field
    a = asset(x, location_id=x.tower.id, state="installed", condition="unknown")
    w = row(x)
    w.asset_id = a.id
    start(x)
    i = inspect(x)
    m = x.db.get(Maintenance, i["equipment_maintenance_id"])
    assert m.asset_id == a.id and m.technician_id == x.tech.id
    assert s.asset_detail(x.db, x.tech, a.id)["maintenance"][0]["id"] == m.id


def replacement_fixture(x):
    x.old = asset(
        x,
        location_id=x.tower.id,
        state="installed",
        condition="unknown",
        device_id=x.ap.id,
    )
    x.spare = asset(x)
    row(x).asset_id = x.old.id
    start(x)
    return body(
        ReplacementInput,
        expected_revision=row(x).revision,
        old_asset_id=x.old.id,
        replacement=Replacement(
            old_revision=x.old.revision,
            new_asset_id=x.spare.id,
            new_revision=x.spare.revision,
            removed_to_location_id=x.repair.id,
            reason=R,
        ),
        installation_notes=R,
        removal_notes=R,
        disposition="repair",
    )


def test_replacement_inventory_history_atomic(field):
    x = field
    b = replacement_fixture(x)
    r = s.replacement(x.db, x.tech, row(x), b)
    assert (
        x.old.state == "removed"
        and x.spare.device_id == x.ap.id
        and x.old.device_id is None
    )
    assert r["inventory"]["ipam_changes"] == 0 and x.old.disposition == "repair"
    assert (
        s.work_detail(x.db, x.tech, row(x))["events"][0]["kind"] == "equipment_replaced"
    )


def test_replacement_failed_completion_rolls_back(field):
    x = field
    b = replacement_fixture(x)
    b.complete = True
    with pytest.raises(ValueError):
        with x.db.begin_nested():
            s.replacement(x.db, x.tech, row(x), b)
    x.db.refresh(x.old)
    x.db.refresh(x.spare)
    assert (
        x.old.state == "installed"
        and x.old.device_id == x.ap.id
        and x.spare.state == "available"
    )
    assert not x.db.scalar(select(Event.id).where(Event.kind == "equipment_replaced"))


def test_attachment_reuses_secure_validator_and_metadata(field):
    x = field
    b = body(
        Upload,
        filename="inspection.txt",
        media_type="text/plain",
        contents_base64=base64.b64encode(b"Synthetic field inspection report").decode(),
    )
    r = s.upload(x.db, x.tech, row(x), b)
    assert r["unscanned"] and "data" not in r and r["sha256"]
    assert x.db.get(Attachment, r["id"]).data == b"Synthetic field inspection report"


@pytest.mark.parametrize(
    "filename,mime,content",
    [
        ("evil.png", "image/png", b"<script>"),
        ("x.txt", "text/plain", b"password=bad"),
        ("x.jpg", "image/jpeg", b"invalid"),
    ],
)
def test_invalid_upload(field, filename, mime, content):
    x = field
    with pytest.raises(ValueError):
        s.upload(
            x.db,
            x.tech,
            row(x),
            body(
                Upload,
                filename=filename,
                media_type=mime,
                contents_base64=base64.b64encode(content).decode(),
            ),
        )


def test_mobile_context_safe_unavailable_health(field):
    x = field
    a = asset(x, location_id=x.tower.id, state="installed", condition="unknown")
    d = s.tower(x.db, x.tech, x.a.id)
    assert d["assets"][0]["id"] == a.id and d["safety"]
    detail = s.asset_detail(x.db, x.tech, a.id)
    assert detail["availability"]["state"] == "unknown"
    assert "financial" not in detail and "ipam_assignments" not in detail


@pytest.mark.parametrize(
    "role,status",
    [("viewer", 403), ("operator", 403), ("technician", 403), ("admin", 200)],
)
def test_api_create_permissions(client, role, status):
    x, c = client
    x.admin.role = role
    x.db.commit()
    b = body(WorkInput, site_id=x.a.id, description=R).model_dump(mode="json")
    assert (
        c.post("/api/field/work-orders", headers=auth(x.admin), json=b).status_code
        == status
    )


def test_api_auth_lookup_assignment_and_private_download(client):
    x, c = client
    assert c.get("/api/field/work-orders").status_code == 401
    assert c.get("/api/field/qr/not-uuid", headers=auth(x.tech)).status_code == 422
    assert (
        c.get("/api/field/qr/" + str(uuid4()), headers=auth(x.tech)).status_code == 404
    )
    assert (
        c.get(
            "/api/field/work-orders/" + str(row(x).id), headers=auth(x.other)
        ).status_code
        == 403
    )
    b = body(
        Upload,
        filename="inspection.txt",
        media_type="text/plain",
        contents_base64=base64.b64encode(b"Synthetic tower photograph note").decode(),
    ).model_dump(mode="json")
    r = c.post(
        f"/api/field/work-orders/{row(x).id}/attachments", headers=auth(x.tech), json=b
    )
    assert r.status_code == 200, r.text
    id = r.json()["id"]
    assert c.get(f"/api/field/attachments/{id}").status_code == 401
    assert (
        c.get(f"/api/field/attachments/{id}", headers=auth(x.other)).status_code == 403
    )
    r = c.get(f"/api/field/attachments/{id}", headers=auth(x.tech))
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"
    assert x.db.scalar(
        select(AuditLog.id).where(AuditLog.action == "field.action_denied")
    )


def test_api_retry_and_operator_read_only(client):
    x, c = client
    start(x)
    x.db.commit()
    w = row(x)
    b = body(Note, expected_revision=w.revision, notes=R).model_dump(mode="json")
    url = f"/api/field/work-orders/{w.id}/notes"
    a = c.post(url, headers=auth(x.tech), json=b)
    r = c.post(url, headers=auth(x.tech), json=b)
    assert a.status_code == 200 and r.json() == a.json()
    x.other.role = "operator"
    x.db.commit()
    assert (
        c.get(f"/api/field/work-orders/{w.id}", headers=auth(x.other)).status_code
        == 200
    )
    assert c.post(url, headers=auth(x.other), json=b).status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/devices",
        "/api/sites",
        "/api/dashboard",
        "/api/equipment",
        "/api/ipam",
        "/api/customers",
    ],
)
def test_technician_cannot_bypass_field_scope_via_legacy_api(client, path):
    x, c = client
    assert c.get(path, headers=auth(x.tech)).status_code == 403
    assert c.get("/api/field/me", headers=auth(x.tech)).status_code == 200


def test_parts_consumption_reuses_inventory_and_audits(field):
    from app.equipment.contracts import LocationInput, BulkInput
    from app.equipment.models import BulkBalance

    x = field
    start(x)
    location = inv.create_location(
        x.db,
        LocationInput(
            code="FIELD-STOCK",
            name="Technician stock",
            kind="technician_stock",
            technician_id=x.tech.id,
            reason=R,
        ),
        x.admin.id,
    )
    inv.bulk_move(
        x.db,
        BulkInput(
            event_key="FIELD-RECEIVE",
            product_id=x.material.id,
            action="receive",
            quantity="5",
            to_location_id=location.id,
            to_state="available",
            reason=R,
        ),
        x.admin.id,
    )
    balance = x.db.scalar(
        select(BulkBalance).where(BulkBalance.location_id == location.id)
    )
    b = body(
        Parts,
        expected_revision=row(x).revision,
        movement=BulkInput(
            event_key="FIELD-INSTALL",
            product_id=x.material.id,
            action="install",
            quantity="2.5",
            from_location_id=location.id,
            from_state="available",
            to_location_id=x.tower.id,
            to_state="installed",
            expected_source_revision=balance.revision,
            reason=R,
        ),
    )
    result = s.parts(x.db, x.tech, row(x), b)
    assert result["movement"]["movement"]["id"]
    x.db.refresh(balance)
    assert str(balance.quantity) == "2.500"
    assert x.db.scalar(select(Event.id).where(Event.kind == "parts_consumed"))


def test_history_native_append_only(field):
    from sqlalchemy.exc import IntegrityError

    x = field
    start(x)
    i = inspect(x)
    with pytest.raises(IntegrityError):
        with x.db.begin_nested():
            x.db.get(Inspection, i["id"]).notes = "Mutated historical inspection"
            x.db.flush()
    assert x.db.get(Inspection, i["id"]).notes == R


def test_photo_upload_valid_png(field):
    import struct, zlib

    def chunk(k, b):
        return (
            struct.pack("!I", len(b))
            + k
            + b
            + struct.pack("!I", zlib.crc32(k + b) & 0xFFFFFFFF)
        )

    raw = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\0\0\0\xff"))
        + chunk(b"IEND", b"")
    )
    x = field
    r = s.upload(
        x.db,
        x.tech,
        row(x),
        body(
            Upload,
            filename="tower.png",
            media_type="image/png",
            contents_base64=base64.b64encode(raw).decode(),
        ),
    )
    assert r["size"] == len(raw) and x.db.get(Attachment, r["id"]).data == raw


def test_provision_every_tower_labels_retry_safe(client):
    x, c = client
    b = body(Mutation).model_dump(mode="json")
    r = c.post("/api/field/labels/provision-towers", headers=auth(x.admin), json=b)
    assert r.status_code == 200, r.text
    count = x.db.scalar(select(func.count()).select_from(Site))
    assert len(r.json()["labels"]) == count
    assert (
        c.post(
            "/api/field/labels/provision-towers", headers=auth(x.admin), json=b
        ).json()
        == r.json()
    )


def test_template_duplicate_keys_and_future_inspection(field):
    with pytest.raises(ValidationError):
        body(
            TemplateInput,
            code="test",
            name="Test template",
            items=[s.default_items()[0]] * 2,
        )
    with pytest.raises(ValidationError):
        body(
            InspectionInput,
            expected_revision=1,
            template_id=1,
            inspected_at=s.now() + timedelta(days=1),
            results=[{"key": "structure_0", "outcome": "pass"}],
            notes=R,
        )
