"""Synthetic applicants only: public boundary, privacy, qualification and installation."""

import json, os, base64
from uuid import uuid4
from datetime import timedelta
from types import SimpleNamespace
import pytest, httpx
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from cryptography.fernet import Fernet
from app.main import app
from app.core.config import settings
from app.core.database import get_db
from app.signup.public_app import app as public_app
from app.signup import service as s, public_service as ps, crypto, providers
from app.signup.models import *
from app.signup.contracts import *
from app.field.models import WorkOrder
from app.models.entities import User, Site, Sector
from app.customers.models import CustomerAccount, CustomerService
from test_field import field, auth
from test_equipment import stock
from test_notifications import network
from test_powercode_customers import inventory, imported
from test_rf_postgres_integration import postgres_db

R = "Synthetic authorized application workflow documentation"


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=kw.pop("reason", R), **kw)


@pytest.fixture
def signup(field, tmp_path, monkeypatch):
    x = field
    key = tmp_path / "pii.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "signup_pii_key_file", str(key))
    monkeypatch.setattr(settings, "signup_public_enabled", True)
    monkeypatch.setattr(
        settings, "signup_public_origin", "https://signup.example.invalid"
    )
    monkeypatch.setattr(settings, "signup_public_test_mode", True)
    monkeypatch.setattr(settings, "signup_turnstile_mode", "fixture")
    monkeypatch.setattr(settings, "signup_geocoder", "disabled")
    monkeypatch.setattr(settings, "synthetic_monitoring_enabled", True)
    x.plan = Plan(
        name="Synthetic configured plan",
        audience="both",
        technology="wireless",
        active=True,
        public=True,
        currency="USD",
        monthly="65.00",
        installation="0.00",
        equipment=[],
        eligibility="Synthetic subject to field review",
    )
    x.db.add(x.plan)
    x.db.flush()
    return x


def form(x, **kw):
    key = uuid4()
    data = {
        "submission_key": str(key),
        "audience": "residential",
        "request_kind": "new",
        "customer_name": "Synthetic Applicant",
        "service_address": {
            "street": "123 Synthetic Road",
            "unit": "Apt 2",
            "city": "Synthetic Town",
            "state": "OH",
            "postal_code": "43500",
        },
        "email": "applicant@example.com",
        "phone": "+15555550103",
        "preferred_contact": "email",
        "consent_email": True,
        "privacy_consent": True,
        "technology": "wireless",
        "plan_id": x.plan.id,
        "notes": "Synthetic test request only",
        "turnstile_token": "synthetic-pass:" + str(key),
    }
    data.update(kw)
    return SignupInput(**data)


def application(x, **kw):
    r = ps.submit(x.db, form(x, **kw))
    return x.db.scalar(
        select(Application).where(Application.reference == r["reference"])
    )


@pytest.fixture
def clients(signup):
    x = signup
    x.db.commit()
    app.dependency_overrides[get_db] = lambda: x.db
    public_app.dependency_overrides[get_db] = lambda: x.db
    with TestClient(
        public_app, base_url="https://signup.example.invalid"
    ) as public, TestClient(app) as private:
        yield x, public, private
    app.dependency_overrides.clear()
    public_app.dependency_overrides.clear()


def headers(public):
    r = public.get("/public/signup/config")
    assert r.status_code == 200, r.text
    return {
        "Origin": "https://signup.example.invalid",
        "X-Signup-CSRF": r.json()["csrf_token"],
    }


def test_residential_and_business_signup_encrypted(signup):
    x = signup
    a = application(x)
    data = s.pii(a)
    assert (
        data["customer_name"] == "Synthetic Applicant"
        and data["service_address"]["unit"] == "Apt 2"
    )
    assert (
        "Synthetic Applicant" not in a.pii_ciphertext
        and "applicant@example.com" not in a.pii_ciphertext
    )
    a2 = application(
        x, audience="business", business_name="Synthetic Broadband Business"
    )
    assert s.pii(a2)["business_name"] == "Synthetic Broadband Business"


@pytest.mark.parametrize(
    "changes",
    [
        {"customer_name": ""},
        {"privacy_consent": False},
        {"audience": "business"},
        {"email": "invalid"},
        {"phone": "invalid"},
        {"consent_email": False},
        {"notes": "ssn=123-45-6789"},
        {"notes": "card number=4111111111111111"},
        {
            "service_address": {
                "street": "PO Box 4",
                "city": "Town",
                "state": "OH",
                "postal_code": "43500",
            }
        },
    ],
)
def test_required_fields_addresses_and_sensitive_input_rejected(signup, changes):
    with pytest.raises(ValueError):
        form(signup, **changes)


def test_address_normalization_preserves_units():
    a = {
        "street": "123 Main Street",
        "unit": "Apartment 2",
        "city": "Town",
        "state": "OH",
        "postal_code": "43500",
        "country": "US",
    }
    b2 = {**a, "street": "123 MAIN ST", "unit": "UNIT 2"}
    assert ps.normalized_address(a) == ps.normalized_address(b2)
    assert ps.normalized_address(a) != ps.normalized_address({**a, "unit": "UNIT 3"})


def test_public_idempotence_duplicate_privacy_and_lookup(clients):
    x, p, c = clients
    h = headers(p)
    body = form(x).model_dump(mode="json")
    a = p.post("/public/signup/applications", headers=h, json=body)
    r = p.post("/public/signup/applications", headers=h, json=body)
    assert a.status_code == 200, a.text
    assert (
        a.json() == r.json()
        and x.db.scalar(select(func.count()).select_from(Application)) == 1
    )
    assert "email" not in a.text and "tower" not in a.text
    lookup = p.post(
        "/public/signup/lookup",
        headers=h,
        json={"token": a.json()["lookup_token"], "turnstile_token": "synthetic-lookup"},
    )
    assert lookup.json()["status"] == "received"
    assert (
        p.post(
            "/public/signup/lookup",
            headers=h,
            json={"token": "f" * 64, "turnstile_token": "synthetic-lookup"},
        ).json()["status"]
        == "not_available"
    )
    changed = {**body, "customer_name": "Different Synthetic Person"}
    assert (
        p.post("/public/signup/applications", headers=h, json=changed).status_code
        == 409
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/devices",
        "/api/applications",
        "/api/auth/login",
        "/docs",
        "/openapi.json",
        "/api/customers",
        "/api/field/work-orders",
    ],
)
def test_public_boundary_contains_no_private_routes(clients, path):
    assert clients[1].get(path).status_code == 404


def test_origin_csrf_spam_and_public_redaction(clients):
    x, p, c = clients
    body = form(x).model_dump(mode="json")
    assert p.post("/public/signup/applications", json=body).status_code == 403
    h = headers(p)
    bad = {**h, "Origin": "https://attacker.example.invalid"}
    assert (
        p.post("/public/signup/applications", headers=bad, json=body).status_code == 403
    )
    body["website"] = "bot filled honeypot"
    r = p.post("/public/signup/applications", headers=h, json=body)
    assert (
        r.status_code == 200
        and x.db.scalar(select(func.count()).select_from(Application)) == 0
    )
    badbody = {**form(x).model_dump(mode="json"), "phone": "secret-private-value"}
    r = p.post("/public/signup/applications", headers=h, json=badbody)
    assert r.status_code == 422 and "secret-private-value" not in r.text


def test_public_rate_limit_shared(clients):
    x, p, c = clients
    h = headers(p)
    for _ in range(8):
        assert (
            p.post(
                "/public/signup/applications",
                headers=h,
                json=form(x).model_dump(mode="json"),
            ).status_code
            == 200
        )
    assert (
        p.post(
            "/public/signup/applications",
            headers=h,
            json=form(x).model_dump(mode="json"),
        ).status_code
        == 429
    )


def test_turnstile_mock_validates_hostname_action_time(signup, monkeypatch, tmp_path):
    x = signup
    key = tmp_path / "turnstile.secret"
    key.write_text("synthetic-provider-secret")
    key.chmod(0o600)
    monkeypatch.setattr(settings, "signup_turnstile_mode", "turnstile")
    monkeypatch.setattr(settings, "signup_turnstile_secret_file", str(key))
    monkeypatch.setattr(settings, "offline_mode", False)

    def handler(req):
        assert (
            req.url.path == "/turnstile/v0/siteverify"
            and b"idempotency_key" in req.content
        )
        return httpx.Response(
            200,
            json={
                "success": True,
                "hostname": "signup.example.invalid",
                "action": "service_signup",
                "challenge_ts": now().isoformat(),
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert providers.turnstile("mock-token", uuid4(), client)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "success": True,
                    "hostname": "evil",
                    "action": "service_signup",
                    "challenge_ts": now().isoformat(),
                },
            )
        )
    ) as client:
        assert not providers.turnstile("mock-token", uuid4(), client)


def test_disabled_and_fixture_verification_fail_closed(signup, monkeypatch):
    monkeypatch.setattr(settings, "signup_turnstile_mode", "disabled")
    assert not providers.turnstile("x", uuid4())
    monkeypatch.setattr(settings, "signup_turnstile_mode", "fixture")
    monkeypatch.setattr(settings, "offline_mode", False)
    assert not providers.turnstile("synthetic-pass", uuid4())


def test_geocoder_mock_retries_and_malformed_ambiguity(signup, monkeypatch):
    x = signup
    monkeypatch.setattr(settings, "signup_geocoder", "nominatim")
    monkeypatch.setattr(settings, "offline_mode", False)
    monkeypatch.setattr(
        settings, "signup_geocoder_origin", "https://geocoder.example.invalid"
    )
    monkeypatch.setattr(
        settings, "signup_geocoder_approved_origins", "https://geocoder.example.invalid"
    )
    monkeypatch.setattr(settings, "signup_geocoder_admin_approved", True)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            503 if len(calls) == 1 else 200,
            json=[] if len(calls) == 1 else [{"lat": "41.4", "lon": "-84.2"}],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r = providers.geocode(form(x).service_address.model_dump(), client)
    assert len(calls) == 2 and r["latitude"] == 41.4
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[{}, {}]))
    ) as client:
        assert providers.geocode(form(x).service_address.model_dump(), client) is None


def test_missing_coverage_and_preliminary_wireless(signup):
    x = signup
    a = application(x)
    out = s.qualify(x.db, x.admin, a, b(QualifyInput, expected_revision=a.revision))
    assert out["snapshot"]["status"] == "requires_site_survey"
    x.a.latitude = 41.4
    x.a.longitude = -84.2
    x.db.flush()
    out = s.qualify(
        x.db,
        x.admin,
        a,
        b(
            QualifyInput,
            expected_revision=a.revision,
            latitude=41.401,
            longitude=-84.201,
            coordinate_source="Synthetic operator coordinates",
        ),
    )
    assert (
        out["snapshot"]["preliminary"]
        and out["snapshot"]["signal_estimate"] == "unavailable"
        and out["snapshot"]["line_of_sight"] == "unverified"
    )
    assert (
        any("tower_id" in r for r in out["snapshot"]["evidence"])
        and a.state == "submitted"
    )


def test_fiber_planned_active_and_outside_boundary(signup):
    x = signup
    a = application(x)
    poly = [[-85, 41], [-84, 41], [-84, 42], [-85, 42], [-85, 41]]
    area = Area(
        name="Synthetic planned fiber",
        technology="fiber",
        status="planned",
        polygon=poly,
        evidence=R,
        updated_by=x.admin.id,
    )
    x.db.add(area)
    x.db.flush()
    out = s.qualify(
        x.db,
        x.admin,
        a,
        b(QualifyInput, expected_revision=a.revision, latitude=41.4, longitude=-84.2),
    )
    assert (
        out["snapshot"]["status"] == "requires_site_survey"
        and out["snapshot"]["fiber_construction"] == "review_required"
    )
    area.status = "active"
    x.db.flush()
    out = s.qualify(
        x.db,
        x.admin,
        a,
        b(QualifyInput, expected_revision=a.revision, latitude=41.4, longitude=-84.2),
    )
    assert out["snapshot"]["status"] == "potentially_serviceable"
    area.technology = "service_boundary"
    x.db.flush()
    out = s.qualify(
        x.db,
        x.admin,
        a,
        b(QualifyInput, expected_revision=a.revision, latitude=40, longitude=-83),
    )
    assert out["snapshot"]["status"] == "outside_current_service_area"


def test_duplicate_matching_read_only_powercode(signup):
    x = signup
    a = application(x)
    other = application(x)
    x.account.email = "applicant@example.com"
    x.db.flush()
    before = x.db.scalar(select(func.count()).select_from(CustomerAccount))
    m = s.matches(x.db, a)
    assert (
        any(r["id"] == other.id for r in m["applications"])
        and any(r["id"] == x.account.id for r in m["customers"])
        and m["production_writes"] == 0
    )
    s.control(
        x.db,
        x.admin,
        a,
        b(
            Control,
            expected_revision=a.revision,
            action="approve_match",
            customer_id=x.account.id,
            evidence=R,
        ),
    )
    assert a.matched_customer_id == x.account.id
    assert x.db.scalar(select(func.count()).select_from(CustomerAccount)) == before


def test_application_transition_authorization_and_no_automatic_qualification(signup):
    x = signup
    a = application(x)
    with pytest.raises(ValueError):
        s.update(
            x.db,
            x.admin,
            a,
            b(Update, expected_revision=a.revision, state="installed", evidence=R),
        )
    s.update(
        x.db,
        x.admin,
        a,
        b(Update, expected_revision=a.revision, state="under_review", evidence=R),
    )
    s.update(
        x.db,
        x.admin,
        a,
        b(
            Update,
            expected_revision=a.revision,
            state="qualification_pending",
            evidence=R,
        ),
    )
    with pytest.raises(ValueError):
        s.update(
            x.db,
            x.admin,
            a,
            b(Update, expected_revision=a.revision, state="qualified", evidence=R),
        )
    s.update(
        x.db,
        x.admin,
        a,
        b(
            Update,
            expected_revision=a.revision,
            state="qualified",
            proposed_site_id=x.a.id,
            approved_qualification=True,
            evidence=R,
        ),
    )
    assert a.state == "qualified"


def survey_work(x, a):
    a.state = "site_survey_required"
    x.db.flush()
    r = s.make_work(
        x.db,
        x.admin,
        a,
        b(
            WorkInput,
            expected_revision=a.revision,
            kind="survey",
            site_id=x.a.id,
            technician_id=x.tech.id,
            scheduled_at=now() + timedelta(days=1),
        ),
    )
    return x.db.get(AppWork, r["id"])


def test_site_survey_scoped_and_not_auto_approved(signup):
    x = signup
    a = application(x)
    m = survey_work(x, a)
    body = b(
        SurveyInput,
        expected_revision=a.revision,
        app_work_id=m.id,
        surveyed_at=now(),
        proposed_site_id=x.a.id,
        technology="wireless",
        line_of_sight="Synthetic unobstructed observation",
        signal_observation="No measured RSSI available",
        mounting="Approved survey observation only",
        cable_route="Synthetic visual route",
        complexity="Requires qualified installation review",
        outcome="qualified_preliminary",
    )
    with pytest.raises(PermissionError):
        s.survey(x.db, x.other, a, body)
    r = s.survey(x.db, x.tech, a, body)
    assert r["outcome"] == "qualified_preliminary" and a.state == "survey_scheduled"
    assert (
        s.work_context(x.db, x.tech, m.work_order_id)["service_address"]["street"]
        == "123 Synthetic Road"
    )


def test_installation_work_order_duplicate_and_pii_not_in_generic_work(signup):
    x = signup
    a = application(x)
    a.state = "installation_pending"
    a.proposed_site_id = x.a.id
    x.db.flush()
    body = b(
        WorkInput,
        expected_revision=a.revision,
        kind="installation",
        site_id=x.a.id,
        technician_id=x.tech.id,
        scheduled_at=now() + timedelta(days=1),
    )
    r = s.make_work(x.db, x.admin, a, body)
    body.expected_revision = a.revision
    again = s.make_work(x.db, x.admin, a, body)
    assert r["work_order_id"] == again["work_order_id"] and again["duplicate"]
    w = x.db.get(WorkOrder, r["work_order_id"])
    assert "Applicant" not in w.description and "5555" not in w.description
    with pytest.raises(PermissionError):
        s.work_context(x.db, x.other, w.id)


def quote(x, a, **kw):
    return s.quote_draft(
        x.db,
        x.admin,
        a,
        b(
            QuoteInput,
            **(
                {
                    "expected_revision": a.revision,
                    "plan_id": x.plan.id,
                    "currency": "USD",
                    "monthly": "65.00",
                    "installation": "0.00",
                    "equipment": "0.00",
                    "construction": "0.00",
                    "expires_at": now() + timedelta(days=7),
                }
                | kw
            )
        ),
    )


def test_quote_revisions_explicit_approval_and_acceptance(signup):
    x = signup
    a = application(x)
    a.state = "qualified"
    x.db.flush()
    q = quote(x, a)
    with pytest.raises(ValueError):
        s.control(
            x.db,
            x.admin,
            a,
            b(
                Control,
                expected_revision=a.revision,
                action="issue_quote",
                quote_id=q["id"],
                evidence=R,
            ),
        )
    s.control(
        x.db,
        x.admin,
        a,
        b(
            Control,
            expected_revision=a.revision,
            action="approve_quote",
            quote_id=q["id"],
            evidence=R,
        ),
    )
    s.control(
        x.db,
        x.admin,
        a,
        b(
            Control,
            expected_revision=a.revision,
            action="issue_quote",
            quote_id=q["id"],
            evidence="Operator recorded authorized manual customer quote delivery",
        ),
    )
    assert a.state == "quote_sent"
    q2 = quote(x, a, monthly="70.00")
    assert q2["number"] == 2 and x.db.get(Quote, q["id"]).snapshot["monthly"] == "65.00"
    with pytest.raises(ValueError):
        s.control(
            x.db,
            x.admin,
            a,
            b(
                Control,
                expected_revision=a.revision,
                action="record_acceptance",
                quote_id=q["id"],
                evidence=R,
            ),
        )
    for action in ["approve_quote", "issue_quote", "record_acceptance"]:
        s.control(
            x.db,
            x.admin,
            a,
            b(
                Control,
                expected_revision=a.revision,
                action=action,
                quote_id=q2["id"],
                evidence="Explicit synthetic customer response recorded manually by administrator",
            ),
        )
    assert a.state == "customer_accepted"
    s.control(
        x.db,
        x.admin,
        a,
        b(
            Control,
            expected_revision=a.revision,
            action="prepare_provisioning",
            evidence=R,
        ),
    )
    assert x.db.scalar(select(ProvisionDraft)).status == "documentation_only"


def test_unknown_quote_charges_not_approved(signup):
    x = signup
    a = application(x)
    a.state = "qualified"
    x.db.flush()
    q = quote(x, a, construction=None)
    with pytest.raises(ValueError):
        s.control(
            x.db,
            x.admin,
            a,
            b(
                Control,
                expected_revision=a.revision,
                action="approve_quote",
                quote_id=q["id"],
                evidence=R,
            ),
        )


def test_notification_consent_and_imported_optout(signup):
    x = signup
    a = application(x)
    out = s.notification(
        x.db,
        x.admin,
        a,
        b(
            NotifyInput,
            expected_revision=a.revision,
            template="application_received",
            channel="sms",
        ),
    )
    assert not out["eligible"] and not out["policy"]["external_delivery"]
    out = s.notification(
        x.db,
        x.admin,
        a,
        b(
            NotifyInput,
            expected_revision=a.revision,
            template="application_received",
            channel="email",
        ),
    )
    assert out["eligible"] and "applicant@example.com" not in json.dumps(out)


def test_retention_destroys_per_application_key_and_blocks_lookup(signup):
    x = signup
    a = application(x)
    oldkey = a.wrapped_key
    a.state = "cancelled"
    a.closed_at = now() - timedelta(days=400)
    x.db.flush()
    s.control(
        x.db,
        x.admin,
        a,
        b(Control, expected_revision=a.revision, action="redact", evidence=R),
    )
    assert (
        not a.wrapped_key
        and not a.pii_ciphertext
        and not a.lookup_hash
        and s.pii(a) == {"redacted": True}
    )
    other = application(x)
    assert other.wrapped_key and other.wrapped_key != oldkey


def test_private_authorization_and_redacted_audits(clients):
    x, p, c = clients
    a = application(x)
    x.db.commit()
    assert c.get("/api/applications").status_code == 401
    assert c.get("/api/applications", headers=auth(x.tech)).status_code == 403
    r = c.get("/api/applications/" + str(a.id), headers=auth(x.admin))
    assert (
        r.status_code == 200
        and r.json()["applicant"]["customer_name"] == "Synthetic Applicant"
    )
    assert "wrapped_key" not in r.text and "lookup_hash" not in r.text


def test_encrypted_data_key_missing_fails_closed(signup, monkeypatch, tmp_path):
    x = signup
    a = application(x)
    key = tmp_path / "different.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "signup_pii_key_file", str(key))
    with pytest.raises(ValueError):
        s.pii(a)


def test_chunked_payload_limit_and_invalid_phone_are_safe(clients):
    x, p, c = clients
    h = headers(p)
    assert (
        p.post(
            "/public/signup/applications",
            headers=h,
            content=iter([b"x" * 20000, b"x" * 20000]),
        ).status_code
        == 413
    )
    data = form(x).model_dump(mode="json")
    data["phone"] = "private-invalid-phone"
    r = p.post("/public/signup/applications", headers=h, json=data)
    assert r.status_code == 422 and "private-invalid-phone" not in r.text


@pytest.mark.parametrize(
    "polygon",
    [
        [[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]],
        [[0, 0], [1, 1], [2, 2], [0, 0]],
        [[0, 0], [1, 0], [1, 0], [0, 1], [0, 0]],
    ],
)
def test_invalid_coverage_geometry_rejected(polygon):
    with pytest.raises(ValueError):
        b(
            AreaInput,
            name="Invalid synthetic area",
            technology="fiber",
            status="planned",
            polygon=polygon,
            evidence=R,
        )


def test_application_reasons_do_not_leak_to_global_audit(clients):
    from app.models.entities import AuditLog

    x, p, c = clients
    a = application(x)
    x.db.commit()
    secret = "Applicant personal note that belongs in encrypted history"
    r = c.post(
        "/api/applications/" + str(a.id) + "/state",
        headers=auth(x.admin),
        json=b(
            Update,
            expected_revision=a.revision,
            state="under_review",
            evidence=R,
            reason=secret,
        ).model_dump(mode="json"),
    )
    assert r.status_code == 200, r.text
    assert secret not in str([row.__dict__ for row in x.db.scalars(select(AuditLog))])
    assert secret in str(s.detail(x.db, x.admin, a)["events"])


def test_survey_photo_encrypted_scoped_and_scanner_gated(signup, tmp_path, monkeypatch):
    from app.maintenance_ops import storage

    x = signup
    monkeypatch.setattr(settings, "maintenance_document_root", str(tmp_path / "docs"))
    monkeypatch.setattr(settings, "maintenance_document_scanner", "synthetic")
    monkeypatch.setattr(settings, "app_env", "staging")
    monkeypatch.setattr(settings, "offline_mode", True)
    a = application(x)
    a.state = "site_survey_required"
    x.db.flush()
    work = s.make_work(
        x.db,
        x.admin,
        a,
        b(
            WorkInput,
            expected_revision=a.revision,
            kind="survey",
            site_id=x.root.id,
            technician_id=x.tech.id,
            scheduled_at=now() + timedelta(days=1),
        ),
    )
    # Validated tiny PNG fixture shared with existing attachment tests.
    import struct, zlib

    def chunk(kind, data):
        return (
            struct.pack("!I", len(data))
            + kind
            + data
            + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
        + chunk(b"IEND", b"")
    )
    photo = s.upload_photo(
        x.db,
        x.tech,
        a,
        b(
            PhotoInput,
            app_work_id=work["id"],
            filename="survey.png",
            media_type="image/png",
            contents_base64=base64.b64encode(raw).decode(),
        ),
    )
    row = x.db.get(Photo, photo["id"])
    assert storage.read(row.storage_key) != raw
    with pytest.raises(PermissionError):
        s.photo_bytes(a, row)
    row.approved = True
    assert s.photo_bytes(a, row) == raw
    a.wrapped_key = None
    with pytest.raises((ValueError, PermissionError)):
        s.photo_bytes(a, row)


def test_unknown_search_is_safe_and_field_bridge_not_public(clients):
    x, p, c = clients
    r = c.get("/api/applications?q=Unknown", headers=auth(x.admin))
    assert r.status_code == 200, r.text
    r = c.post(
        "/api/field/work-orders",
        headers=auth(x.other),
        json={"customer_application_authorized": True},
    )
    assert r.status_code in {403, 422}


def test_concurrent_exact_submissions_use_one_postgres_application(signup):
    """Independent real connections; create/drop only this test's new disposable DB."""
    import subprocess, sys, concurrent.futures
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    x = signup
    source = make_url(os.environ["RF_INTEGRATION_TEST_DATABASE_URL"])
    if source.database != "nwo_noc" or source.host not in {
        "localhost",
        "127.0.0.1",
        "db",
    }:
        pytest.fail("Disposable local integration database required")
    name = "noc103_concurrent_" + uuid4().hex
    admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    role = name + "_gateway"
    role_created = False
    created = False
    try:
        with admin.connect() as c:
            c.execute(text("CREATE DATABASE " + name))
            created = True
        url = source.set(database=name)
        env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            check=True,
            capture_output=True,
            timeout=60,
        )
        engine = create_engine(url)
        with Session(engine) as db:
            p = Plan(
                name="Synthetic concurrent plan",
                audience="both",
                technology="wireless",
                active=True,
                public=True,
                eligibility=R,
                equipment=[],
            )
            db.add(p)
            db.commit()
            plan_id = p.id
        from pathlib import Path

        grant_sql = (
            (Path(__file__).resolve().parents[2] / "examples/signup/public-role.sql")
            .read_text()
            .replace("noc_signup", role)
            .replace("ON DATABASE nwo_noc", "ON DATABASE " + name)
        )
        with engine.begin() as c:
            c.exec_driver_sql(grant_sql)
        role_created = True
        body = form(x, plan_id=plan_id)

        def write(_):
            with Session(engine) as db:
                db.execute(text("SET ROLE " + role))
                result = ps.submit(db, body, verify=lambda *_: True)
                db.commit()
                return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(write, range(8)))
        assert all(r == receipts[0] for r in receipts)
        with engine.connect() as c:
            c.execute(text("RESET ROLE"))
            c.commit()
            for table in [
                "users",
                "sites",
                "customer_accounts",
                "field_work_orders",
                "signup_quotes",
                "signup_coverage_areas",
            ]:
                c.execute(text("SET ROLE " + role))
                try:
                    c.execute(text("SELECT * FROM " + table + " LIMIT 1"))
                    pytest.fail("Restricted signup role could read private table")
                except __import__("sqlalchemy").exc.ProgrammingError as e:
                    assert e.orig.sqlstate == "42501"
                    c.rollback()
                c.execute(text("RESET ROLE"))
                c.commit()
        with engine.connect() as c:
            c.execute(text("SET ROLE " + role))
            with pytest.raises(__import__("sqlalchemy").exc.ProgrammingError) as denied:
                c.execute(
                    text(
                        "SELECT pii_ciphertext,wrapped_key,email_hash FROM signup_applications"
                    )
                )
            assert denied.value.orig.sqlstate == "42501"
            c.rollback()
            c.execute(text("RESET ROLE"))
            c.commit()
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Application)) == 1
            assert db.scalar(select(func.count()).select_from(Event)) == 1
    finally:
        if engine:
            engine.dispose()
        if created:
            with admin.connect() as c:
                c.execute(text("DROP DATABASE " + name))
        if role_created:
            with admin.connect() as c:
                c.execute(text("DROP ROLE " + role))
        admin.dispose()


def test_plan_revision_requires_fresh_catalog_snapshot(clients):
    x, p, c = clients
    payload = b(
        PlanUpdate,
        expected_revision=x.plan.revision,
        name="Synthetic revised plan",
        audience="both",
        technology="wireless",
        eligibility=R,
        public=True,
    ).model_dump(mode="json")
    first = c.post(
        "/api/applications/plans/" + str(x.plan.id), headers=auth(x.admin), json=payload
    )
    assert first.status_code == 200, first.text
    payload["operation_key"] = str(uuid4())
    assert (
        c.post(
            "/api/applications/plans/" + str(x.plan.id),
            headers=auth(x.admin),
            json=payload,
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "amount", ["not-money", "NaN", "Infinity", "-1", "1.001", None]
)
def test_optional_quote_charges_fail_validation(amount):
    with pytest.raises(ValueError):
        b(
            QuoteInput,
            expected_revision=1,
            plan_id=1,
            currency="USD",
            expires_at=now() + timedelta(days=1),
            optional_services=[
                {"description": "Synthetic optional service", "amount": amount}
            ],
        )


def test_access_log_drops_sensitive_query_values():
    import logging
    from app.signup.privacy import RedactQuery

    record = logging.LogRecord(
        "uvicorn.access",
        20,
        "synthetic",
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "loopback",
            "GET",
            "/api/applications?q=private@example.com&token=secret",
            "1.1",
            200,
        ),
        None,
    )
    assert RedactQuery().filter(record)
    assert "private@example.com" not in record.getMessage()
    assert "secret" not in record.getMessage()
    assert "/api/applications" in record.getMessage()
