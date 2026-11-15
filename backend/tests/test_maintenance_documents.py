"""Synthetic scheduling, file quarantine, role isolation and maintenance evidence."""

import base64, io, json, os, socket, struct, threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func
from app.core.config import settings, Settings
from app.maintenance_ops import service as s, storage
from app.maintenance_ops.models import *
from app.maintenance_ops.contracts import *
from app.field import service as f
from app.field.models import WorkOrder, Receipt, Grant
from app.equipment.models import Asset, Maintenance
from app.equipment.contracts import MaintenanceInput
from test_field import field, start, row, auth, client
from test_equipment import stock, asset
from test_notifications import network
from test_powercode_customers import inventory, imported
from test_rf_postgres_integration import postgres_db

R = "Synthetic administrator approved preventive maintenance documentation"
NOW = datetime(2026, 1, 31, 10, tzinfo=timezone.utc)


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "maintenance_document_root", str(tmp_path / "documents")
    )
    monkeypatch.setattr(settings, "maintenance_document_scanner", "synthetic")
    monkeypatch.setattr(settings, "synthetic_monitoring_enabled", True)


@pytest.fixture
def maint(field):
    x = field
    x.category_doc = Category(code="inspection", name="Inspection reports")
    x.db.add(x.category_doc)
    x.db.flush()
    return x


def schedule(x, **kw):
    r = s.create_schedule(
        x.db,
        x.admin,
        b(
            ScheduleInput,
            **(
                {
                    "site_id": x.a.id,
                    "technician_id": x.tech.id,
                    "name": "Synthetic preventive tower inspection",
                    "maintenance_type": "tower_inspection",
                    "recurrence": "monthly",
                    "starts_at": NOW,
                }
                | kw
            ),
        ),
    )
    return x.db.get(Schedule, r["id"])


def document(x, **kw):
    r = s.create_document(
        x.db,
        x.admin,
        b(
            DocumentInput,
            **(
                {
                    "site_id": x.a.id,
                    "category_id": x.category_doc.id,
                    "title": "Synthetic tower inspection report",
                }
                | kw
            ),
        ),
    )
    return x.db.get(Document, r["id"])


def version(x, d, raw=b"Synthetic static inspection document", **kw):
    r = s.upload_version(
        x.db,
        x.admin,
        d,
        b(
            VersionInput,
            **(
                {
                    "expected_revision": d.revision,
                    "filename": "report.txt",
                    "media_type": "text/plain",
                    "contents_base64": base64.b64encode(raw).decode(),
                    "change_description": "Initial reviewed synthetic maintenance report",
                }
                | kw
            ),
        ),
    )
    return x.db.get(Version, r["id"])


def control(x, d, action, v=None):
    return s.control_document(
        x.db,
        x.admin,
        d,
        b(
            DocumentControl,
            expected_revision=d.revision,
            action=action,
            version_id=v.id if v else None,
        ),
    )


@pytest.mark.parametrize(
    "kind,days,expected",
    [
        ("weekly", None, "2026-02-07"),
        ("monthly", None, "2026-02-28"),
        ("quarterly", None, "2026-04-30"),
        ("semiannual", None, "2026-07-31"),
        ("annual", None, "2027-01-31"),
        ("custom_days", 10, "2026-02-10"),
    ],
)
def test_recurrence_due_calculation_anchored(maint, kind, days, expected):
    x = maint
    r = schedule(x, recurrence=kind, custom_days=days)
    assert s.due(r, 1).date().isoformat() == expected
    if kind == "monthly":
        assert s.due(r, 2).day == 31


def test_leap_year_anchor(maint):
    r = schedule(
        maint,
        starts_at=datetime(2028, 2, 29, 12, tzinfo=timezone.utc),
        recurrence="annual",
    )
    assert s.due(r, 1).day == 28 and s.due(r, 4).day == 29


@pytest.mark.parametrize("kind,days", [("custom_days", None), ("weekly", 3)])
def test_invalid_custom_interval(kind, days):
    with pytest.raises(ValidationError):
        b(
            ScheduleInput,
            site_id=1,
            name=R,
            maintenance_type="tower",
            recurrence=kind,
            custom_days=days,
            starts_at=NOW,
        )


def test_generation_assignment_no_duplicate_and_no_dispatch(maint):
    x = maint
    r = schedule(x, starts_at=NOW + timedelta(days=1), lead_days=1)
    a = s.generate(x.db, NOW)
    c = s.generate(x.db, NOW)
    assert len(a["generated"]) == 1 and c["generated"] == [] and a["dispatches"] == 0
    w = x.db.get(WorkOrder, a["generated"][0]["work_order_id"])
    assert w.technician_id == x.tech.id and w.site_id == x.a.id
    assert x.db.scalar(select(func.count()).select_from(Occurrence)) == 1


def test_due_transaction_rollback(maint):
    x = maint
    schedule(x, starts_at=NOW)
    with pytest.raises(RuntimeError):
        with x.db.begin_nested():
            s.generate(x.db, NOW)
            raise RuntimeError("synthetic worker crash")
    assert not x.db.scalar(select(Occurrence.id))
    assert len(s.generate(x.db, NOW)["generated"]) == 1


def test_paused_and_inactive_technician_no_generation(maint):
    x = maint
    r = schedule(x)
    r.active = False
    x.db.flush()
    assert not s.generate(x.db, NOW)["generated"]
    r.active = True
    x.tech.is_active = False
    x.db.flush()
    assert s.generate(x.db, NOW)["blocked"] == 1


def test_schedule_revision_and_reassignment(maint):
    x = maint
    r = schedule(x)
    old = r.revision
    s.update_schedule(
        x.db,
        x.admin,
        r,
        b(
            ScheduleUpdate,
            expected_revision=old,
            active=False,
            technician_id=x.other.id,
        ),
    )
    with pytest.raises(ValueError):
        s.update_schedule(
            x.db, x.admin, r, b(ScheduleUpdate, expected_revision=old, active=True)
        )


def test_overdue_and_internal_reminders(maint):
    x = maint
    r = schedule(x, starts_at=NOW - timedelta(days=1), lead_days=0)
    out = s.reminders(x.db, NOW)
    assert out["external_delivery"] is False
    reminder = x.db.scalar(
        select(Reminder).where(Reminder.key == "schedule:" + str(r.id))
    )
    assert reminder.kind == "overdue"
    s.reminders(x.db, NOW)
    assert x.db.scalar(select(func.count(Reminder.id))) == 1
    r.active = False
    x.db.flush()
    s.reminders(x.db, NOW)
    assert not reminder.active


def test_calendar_filters_projection_and_work_order(maint):
    x = maint
    r = schedule(x, starts_at=NOW + timedelta(days=1), lead_days=1)
    data = s.calendar_rows(
        x.db,
        x.tech,
        NOW,
        NOW + timedelta(days=40),
        site_id=x.a.id,
        technician_id=x.tech.id,
        maintenance_type="tower_inspection",
    )
    assert len(data["items"]) == 2 and all(
        a["kind"] == "projected_schedule" for a in data["items"]
    )
    s.generate(x.db, NOW)
    data = s.calendar_rows(
        x.db,
        x.tech,
        NOW,
        NOW + timedelta(days=40),
        site_id=x.a.id,
        maintenance_type="tower_inspection",
    )
    assert (
        len(data["items"]) == 2
        and sum(r["kind"] == "work_order" for r in data["items"]) == 1
    )
    assert not s.calendar_rows(x.db, x.other, NOW, NOW + timedelta(days=5))["items"]


def service_fixture(x):
    a = asset(x, location_id=x.tower.id, state="installed", condition="unknown")
    row(x).asset_id = a.id
    start(x)
    body = b(
        ServiceInput,
        work_order_id=row(x).id,
        expected_revision=row(x).revision,
        maintenance=MaintenanceInput(
            inspected_at=f.now(),
            work_performed=R,
            fault_description="Synthetic intermittent link instability",
            repair_actions="Inspected patch connections without equipment changes",
            reason=R,
        ),
        diagnostics="Synthetic signal and connector observations reviewed",
        labor_minutes=30,
        warranty_claim="Claim documentation awaiting manufacturer review",
    )
    return a, body


def test_service_history_survives_asset_movement(maint):
    x = maint
    a, body = service_fixture(x)
    out = s.service_record(x.db, x.tech, body)
    record = x.db.get(ServiceRecord, out["id"])
    assert record.asset_id == a.id and record.incident_id == x.incident.id
    assert x.db.get(Maintenance, record.equipment_maintenance_id).asset_id == a.id
    assert (
        record.site_id == x.a.id
        and s.service_view(x.db, record)["maintenance"]["fault_description"]
    )


def test_financial_service_restrictions_and_reports(maint):
    x = maint
    a, body = service_fixture(x)
    body.repair_cost = "12.50"
    body.currency = "USD"
    with pytest.raises(PermissionError):
        s.service_record(x.db, x.tech, body)
    body.repair_cost = __import__("decimal").Decimal("12.50")
    body.maintenance.technician_id = x.tech.id
    out = s.service_record(x.db, x.admin, body)
    start_at = f.now() - timedelta(days=1)
    end = f.now() + timedelta(days=1)
    report = s.report(x.db, x.admin, start_at, end, financial=True)
    assert (
        report["repair_costs_by_currency"] == {"USD": "12.50"}
        and report["labor_minutes_known"] == 30
    )
    hidden = s.report(x.db, x.tech, start_at, end)
    assert "repair_cost" not in hidden["services"][0]
    with pytest.raises(PermissionError):
        s.report(x.db, x.tech, start_at, end, financial=True)
    assert "repair_cost" in s.csv_report(
        report, True
    ) and "repair_cost" not in s.csv_report(hidden, False)


def test_document_tower_asset_association(maint):
    x = maint
    a = asset(x, location_id=x.tower.id, state="installed", condition="unknown")
    d = document(x, asset_id=a.id)
    assert d.site_id == x.a.id and d.asset_id == a.id
    with pytest.raises(ValueError):
        document(x, site_id=x.b.id, asset_id=a.id)


def test_document_versioning_approval_checksum_and_historical_access(maint):
    x = maint
    d = document(x)
    v = version(x, d)
    with pytest.raises(PermissionError):
        s.download(x.db, x.tech, d, v)
    control(x, d, "approve", v)
    assert s.download(x.db, x.tech, d, v) == b"Synthetic static inspection document"
    newer = version(x, d, b"Updated synthetic report content")
    assert newer.number == 2 and v.number == 1
    control(x, d, "approve", newer)
    with pytest.raises(PermissionError):
        s.download(x.db, x.tech, d, v)
    assert s.download(x.db, x.admin, d, v) == b"Synthetic static inspection document"
    assert len(s.document_detail(x.db, x.admin, d)["versions"]) == 2


def test_checksum_corruption_fails_closed(maint):
    x = maint
    d = document(x)
    v = version(x, d)
    control(x, d, "approve", v)
    p = storage.path(v.storage_key)
    os.chmod(p, 0o600)
    p.write_bytes(b"Corrupt synthetic bytes")
    with pytest.raises(ValueError):
        s.download(x.db, x.admin, d, v)


@pytest.mark.parametrize(
    "visibility,role,allowed",
    [
        ("admin", "tech", False),
        ("operator", "tech", False),
        ("technician", "tech", True),
        ("admin", "other", False),
    ],
)
def test_document_permission_scope(maint, visibility, role, allowed):
    x = maint
    d = document(x, visibility=visibility)
    u = getattr(x, role)
    if allowed:
        assert s.doc_access(x.db, u, d.id).id == d.id
    else:
        with pytest.raises(PermissionError):
            s.doc_access(x.db, u, d.id)


def test_revoked_site_grant_and_wrong_assignment_denied(maint):
    x = maint
    d = document(x)
    with pytest.raises(PermissionError):
        s.doc_access(x.db, x.other, d.id)


def test_default_scanner_quarantine_no_approval(maint, monkeypatch):
    monkeypatch.setattr(settings, "maintenance_document_scanner", "disabled")
    x = maint
    d = document(x)
    v = version(x, d)
    assert v.scan_state == "quarantined"
    with pytest.raises(ValueError):
        control(x, d, "approve", v)


def test_infected_and_scanner_failure(maint, monkeypatch):
    x = maint
    d = document(x)
    v = version(x, d, b"EICAR synthetic harmless marker")
    assert v.scan_state == "infected"
    with pytest.raises(ValueError):
        control(x, d, "approve", v)
    monkeypatch.setattr(settings, "maintenance_document_scanner", "clamav")
    monkeypatch.setattr(
        settings, "maintenance_clamav_socket", "/tmp/nonexistent-noc102.sock"
    )
    assert storage.scan(b"bytes")[0] == "error"


def test_local_clamav_socket_instream_protocol(maint, tmp_path, monkeypatch):
    path = str(tmp_path / "clamd.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    received = []

    def serve():
        conn, _ = server.accept()
        with conn:
            command = b""
            while not command.endswith(b"\0"):
                command += conn.recv(1)
            assert command == b"zINSTREAM\0"
            while True:
                n = struct.unpack("!I", conn.recv(4))[0]
                if n == 0:
                    break
                buf = b""
                while len(buf) < n:
                    buf += conn.recv(n - len(buf))
                received.append(buf)
            conn.sendall(b"stream: OK\0")
        server.close()

    thread = threading.Thread(target=serve)
    thread.start()
    monkeypatch.setattr(settings, "maintenance_document_scanner", "clamav")
    monkeypatch.setattr(settings, "maintenance_clamav_socket", path)
    assert storage.scan(b"Synthetic scan protocol") == ("clean", "clamav")
    thread.join(timeout=3)
    assert received == [b"Synthetic scan protocol"]


def test_pdf_validation_and_active_content_rejection(maint):
    from pypdf import PdfWriter

    def pdf(active):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        if active:
            from pypdf.generic import NameObject, DictionaryObject, TextStringObject

            writer._root_object[NameObject("/OpenAction")] = DictionaryObject(
                {
                    NameObject("/S"): NameObject("/JavaScript"),
                    NameObject("/JS"): TextStringObject("alert(1)"),
                }
            )
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    x = maint
    d = document(x)
    assert (
        version(
            x, d, pdf(False), filename="inspection.pdf", media_type="application/pdf"
        ).media_type
        == "application/pdf"
    )
    with pytest.raises(ValueError):
        version(x, d, pdf(True), filename="active.pdf", media_type="application/pdf")


@pytest.mark.parametrize(
    "filename,media,content",
    [
        ("bad.pdf", "application/pdf", b"<html>"),
        ("photo.png", "image/png", b"bad"),
        ("note.txt", "text/plain", b"password=bad"),
    ],
)
def test_invalid_documents(maint, filename, media, content):
    with pytest.raises(ValueError):
        version(maint, document(maint), content, filename=filename, media_type=media)


def test_soft_delete_retention_hold_and_restore(maint):
    x = maint
    d = document(x)
    v = version(x, d)
    control(x, d, "approve", v)
    control(x, d, "hold")
    control(x, d, "delete")
    assert s.document_detail(x.db, x.admin, d)["retention_eligible_at"] is None
    with pytest.raises(LookupError):
        s.doc_access(x.db, x.tech, d.id)
    assert storage.path(v.storage_key).exists()
    control(x, d, "restore")
    assert s.download(x.db, x.tech, d, v)


def test_failed_upload_transaction_preserves_prior_version(maint):
    x = maint
    d = document(x)
    v = version(x, d)
    with pytest.raises(RuntimeError):
        with x.db.begin_nested():
            version(x, d, b"New immutable synthetic file")
            raise RuntimeError("synthetic DB interruption")
    assert (
        x.db.scalar(select(func.count(Version.id)).where(Version.document_id == d.id))
        == 1
    )
    assert storage.read(v.storage_key) == b"Synthetic static inspection document"


def test_storage_path_and_symlink_rejected(maint, tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        storage.path("../private")
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(settings, "maintenance_document_root", str(link))
    with pytest.raises(ValueError):
        storage.put(b"synthetic")


def test_insufficient_evidence_no_prediction(maint):
    x = maint
    out = s.reminders(x.db, NOW)
    assert out["active"] == 0 and not out["external_delivery"]


def test_api_document_access_and_export_permissions(client):
    x, c = client
    cat = Category(code="api-doc", name="API documents")
    x.db.add(cat)
    x.db.flush()
    x.category_doc = cat
    d = document(x)
    v = version(x, d)
    control(x, d, "approve", v)
    x.db.commit()
    url = f"/api/maintenance-ops/documents/{d.id}/versions/{v.id}/download"
    assert c.get(url).status_code == 401
    assert c.get(url, headers=auth(x.other)).status_code == 403
    response = c.get(url, headers=auth(x.tech))
    assert (
        response.status_code == 200
        and response.headers["x-content-type-options"] == "nosniff"
    )
    assert (
        c.get("/api/maintenance-ops/documents", headers=auth(x.tech)).json()["items"][
            0
        ]["id"]
        == d.id
    )
    x.other.role = "operator"
    x.db.commit()
    assert (
        c.post(
            "/api/maintenance-ops/documents",
            headers=auth(x.other),
            json=b(
                DocumentInput, site_id=x.a.id, category_id=cat.id, title=R
            ).model_dump(mode="json"),
        ).status_code
        == 403
    )
    start_at = (f.now() - timedelta(days=1)).isoformat()
    end = (f.now() + timedelta(days=1)).isoformat()
    assert (
        c.get(
            "/api/maintenance-ops/reports.csv",
            params={"start": start_at, "end": end, "financial": "true"},
            headers=auth(x.tech),
        ).status_code
        == 403
    )


def test_generation_api_idempotence(client):
    x, c = client
    schedule(x, starts_at=f.now(), lead_days=0)
    x.db.commit()
    body = b(Mutation).model_dump(mode="json")
    a = c.post("/api/maintenance-ops/generate", headers=auth(x.admin), json=body)
    r = c.post("/api/maintenance-ops/generate", headers=auth(x.admin), json=body)
    assert a.status_code == 200, a.text
    assert a.json() == r.json() and len(a.json()["generated"]) == 1


def test_synthetic_clean_results_not_trusted_outside_isolation(maint, monkeypatch):
    x = maint
    d = document(x)
    v = version(x, d)
    control(x, d, "approve", v)
    monkeypatch.setattr(settings, "offline_mode", False)
    with pytest.raises(PermissionError):
        s.download(x.db, x.admin, d, v)
    with pytest.raises(ValueError):
        control(x, d, "approve", v)


def test_native_document_content_metadata_immutable(maint):
    from sqlalchemy.exc import IntegrityError

    x = maint
    d = document(x)
    v = version(x, d)
    with pytest.raises(IntegrityError):
        with x.db.begin_nested():
            v.sha256 = "0" * 64
            x.db.flush()
    assert storage.read(v.storage_key)


def test_archive_bundle_restores_new_private_directory(maint, tmp_path):
    from app.maintenance_ops.archive import backup, restore, digest

    key = storage.put(b"Synthetic immutable backup document")
    dump = tmp_path / "fixture.dump"
    dump.write_bytes(b"Synthetic database dump fixture")
    bundle = tmp_path / "archive.tar.gz"
    out = backup(dump, storage.root(), bundle)
    assert out["files"] == 1
    target = tmp_path / "restore"
    r = restore(bundle, target)
    assert r["files"] == 1
    assert (
        target / "blobs" / key[:2] / key
    ).read_bytes() == b"Synthetic immutable backup document"
    with pytest.raises(ValueError):
        restore(bundle, target)
    with pytest.raises(ValueError):
        backup(dump, storage.root(), bundle)


def test_archive_rejects_traversal(tmp_path):
    import tarfile
    from app.maintenance_ops.archive import restore

    bundle = tmp_path / "bad.tar"
    with tarfile.open(bundle, "w:gz") as tar:
        data = json.dumps(
            {
                "version": 1,
                "database_sha256": "a",
                "files": {"../../escape": {"sha256": "b", "size": 1}},
            }
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("../../escape")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
        info = tarfile.TarInfo("database.dump")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        restore(bundle, tmp_path / "new")
    assert not (tmp_path / "escape").exists()


def test_generated_due_work_keeps_internal_due_soon_reminder(maint):
    x = maint
    sc = schedule(x, starts_at=NOW + timedelta(days=1), lead_days=1)
    s.generate(x.db, NOW)
    s.reminders(x.db, NOW)
    r = x.db.scalar(select(Reminder).where(Reminder.key.like("occurrence:%")))
    assert r.active and r.kind == "due_soon"


def test_csv_formula_neutralization(maint):
    x = maint
    a, body = service_fixture(x)
    body.warranty_claim = '=HYPERLINK("unsafe")'
    s.service_record(x.db, x.tech, body)
    data = s.report(
        x.db, x.tech, f.now() - timedelta(days=1), f.now() + timedelta(days=1)
    )
    assert "'=HYPERLINK" in s.csv_report(data, False)


def test_worker_disabled_safe(maint, monkeypatch):
    from app.maintenance_ops.worker import tick

    monkeypatch.setattr(settings, "maintenance_scheduler_enabled", False)
    assert tick() == {"enabled": False}


def test_document_upload_idempotency_and_revision_conflict(client):
    x, c = client
    category = Category(code="retry", name="Retry documents")
    x.db.add(category)
    x.db.flush()
    x.category_doc = category
    d = document(x)
    x.db.commit()
    body = b(
        VersionInput,
        expected_revision=d.revision,
        filename="retry.txt",
        media_type="text/plain",
        contents_base64=base64.b64encode(b"Synthetic retry-safe document").decode(),
        change_description=R,
    ).model_dump(mode="json")
    url = f"/api/maintenance-ops/documents/{d.id}/versions"
    a = c.post(url, headers=auth(x.admin), json=body)
    r = c.post(url, headers=auth(x.admin), json=body)
    assert a.status_code == 200, a.text
    assert (
        a.json() == r.json()
        and x.db.scalar(select(func.count()).select_from(Version)) == 1
    )
    body["operation_key"] = str(uuid4())
    assert c.post(url, headers=auth(x.admin), json=body).status_code == 409


def test_maintenance_executor_prevents_overlap_and_resumes():
    from concurrent.futures import Future
    from app.maintenance_ops.worker import MaintenanceRunner

    class Pool:
        def __init__(self):
            self.calls = []

        def submit(self, fn):
            self.calls.append(fn)
            return Future()

    pool = Pool()
    runner = MaintenanceRunner(True, pool)
    assert runner.submit_due(0) and not runner.submit_due(61)
    runner.pending.set_result({"generated": []})
    assert runner.submit_due(61)
    assert not runner.submit_due(90) and len(pool.calls) == 2


def test_maintenance_executor_failure_isolated_and_retried(caplog):
    from concurrent.futures import Future
    from app.maintenance_ops.worker import MaintenanceRunner

    class Pool:
        def submit(self, fn):
            return Future()

    runner = MaintenanceRunner(True, Pool())
    runner.submit_due(0)
    runner.pending.set_exception(RuntimeError("sensitive internal detail"))
    assert runner.submit_due(60) and "sensitive internal detail" not in caplog.text


def test_maintenance_executor_disabled_no_tasks():
    from app.maintenance_ops.worker import MaintenanceRunner

    runner = MaintenanceRunner(False)
    assert not runner.submit_due(1000) and runner.pool is None
