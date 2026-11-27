"""NOC-owned synthetic contracts; never native or production Powercode evidence."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path
import json
import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import get_db
from app.core.config import settings
from app.customers import powercode_service as s
from app.customers.powercode_contracts import (
    ConnectionInput,
    CredentialInput,
    MappingInput,
    ProvisionInput,
    DecisionInput,
    ActivationInput,
    PackageRecord,
    ProvisionResult,
)
from app.customers.powercode_transport import NativePowercode, FixtureProvisioner
from app.customers.powercode_models import *
from app.customers.models import CustomerAccount, CustomerService
from app.customers.providers import parse
from app.customers.sync import synchronize
from app.signup import crypto, public_service
from app.signup.models import (
    Application,
    Plan,
    Quote,
    QuoteResponse,
    Qualification,
    AppWork,
    ProvisionDraft,
)
from app.field.models import WorkOrder, now
from test_powercode_customers import batch, Provider, inventory, imported
from test_signup import signup, form
from test_field import field, auth
from test_equipment import stock
from test_notifications import network
from test_rf_postgres_integration import postgres_db


def package(**kw):
    return dict(
        external_id="SYN-P130",
        name="Synthetic configurable package",
        technology="wireless",
        audience="both",
        available=True,
        currency="USD",
        monthly="65.00",
        installation="0.00",
        download_mbps="100",
        upload_mbps="20",
        updated_at=(now() - timedelta(minutes=20)).isoformat(),
        **kw,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://powercode.example.com",
        "https://u:p@powercode.example.com",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://powercode.example.com/private",
        "https://powercode.example.com?key=secret",
        "https://localhost",
        "https://powercode.example.com:8080",
    ],
)
def test_connection_rejects_unsafe_origins(url):
    with pytest.raises(ValidationError):
        ConnectionInput(endpoint=url, allowed_origin=url)


def test_endpoint_origin_and_auth_are_not_guessed():
    c = ConnectionInput(
        endpoint="https://powercode.example.com:444/",
        allowed_origin="https://powercode.example.com:444",
    )
    assert c.endpoint == "https://powercode.example.com:444" and c.emergency_disabled
    with pytest.raises(ValidationError):
        ConnectionInput(
            endpoint="https://a.example.com", allowed_origin="https://b.example.com"
        )
    with pytest.raises(ValueError):
        NativePowercode()
    with pytest.raises(ValueError):
        FixtureProvisioner(httpx.HTTPTransport())
    with pytest.raises(ValidationError):
        ConnectionInput(native_enabled=True)


@pytest.mark.parametrize(
    "value",
    [
        {"outcome": "accepted"},
        {"outcome": "accepted", "external_id": "x", "private": "bad"},
        {"outcome": "not_accepted", "external_id": "x"},
        {"outcome": "accepted", "external_id": ""},
    ],
)
def test_provider_response_contract_rejects_unverified_acceptance(value):
    with pytest.raises(ValidationError):
        ProvisionResult.model_validate(value)


@pytest.mark.parametrize(
    "outcome", ["authentication_failed", "rate_limited", "validation_failed"]
)
def test_explicit_mock_nonacceptance_has_safe_error(outcome):
    p = FixtureProvisioner(
        httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"outcome": "not_accepted", "error_code": outcome}
            )
        )
    )
    r = p.submit("account", {}, str(uuid4()))
    assert r.outcome == "not_accepted" and r.error_code == outcome


@pytest.mark.parametrize(
    "kind", ["timeout", "malformed", "redirect", "unexpected", "accepted_without_id"]
)
def test_uncertain_submissions_never_retry(kind):
    calls = []

    def send(req):
        calls.append(req)
        if kind == "timeout":
            raise httpx.ReadTimeout("private source error")
        if kind == "malformed":
            return httpx.Response(200, content=b"not json")
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://private.invalid"})
        if kind == "unexpected":
            return httpx.Response(429)
        return httpx.Response(200, json={"outcome": "accepted"})

    r = FixtureProvisioner(httpx.MockTransport(send)).submit(
        "account", {}, str(uuid4())
    )
    assert r.outcome == "ambiguous" and len(calls) == 1
    assert calls[0].url.host == "powercode-fixture.invalid"


@pytest.mark.parametrize("operation", ["activate", "cancel", "invoice", "payment"])
def test_sensitive_operations_do_not_exist(operation):
    p = FixtureProvisioner(
        httpx.MockTransport(lambda req: pytest.fail("Must never call transport"))
    )
    with pytest.raises(ValueError):
        p.submit(operation, {}, str(uuid4()))


def test_fixture_revenue_and_lte_5g_are_explicit():
    v = batch()
    v["packages"] = [package()]
    v["services"][0].update(
        service_type="lte",
        package_id="SYN-P130",
        monthly_recurring="65.00",
        currency="USD",
        revenue_basis="contracted",
    )
    assert parse(v).services[0].service_type == "lte"
    v["services"][0]["revenue_basis"] = "collected"
    with pytest.raises(
        __import__(
            "app.customers.providers", fromlist=["ProviderFailure"]
        ).ProviderFailure
    ):
        parse(v)
    with pytest.raises(ValidationError):
        PackageRecord(**{**package(), "monthly": "NaN"})


def test_status_and_financial_history_preserve_consents(inventory):
    db = inventory.db
    v = batch()
    v["packages"] = [package()]
    v["services"][0].update(
        package_id="SYN-P130",
        monthly_recurring="65.00",
        currency="USD",
        revenue_basis="contracted",
    )
    assert synchronize(db, Provider(v))["status"] == "healthy"
    c = db.scalar(select(CustomerAccount))
    c.sms_opt_out = True
    c.sms_consent = "denied"
    sv = db.scalar(select(CustomerService))
    original = sv.id
    delta = batch(1, "incremental")
    delta["services"][0].update(
        status="suspended",
        package_id="SYN-P130",
        monthly_recurring="65.00",
        currency="USD",
        revenue_basis="contracted",
    )
    assert synchronize(db, Provider(delta))["status"] == "healthy"
    assert c.sms_opt_out and c.sms_consent == "denied" and sv.id == original
    history = list(
        db.scalars(select(PowercodeServiceHistory).order_by(PowercodeServiceHistory.id))
    )
    assert [h.status for h in history] == ["active", "suspended"]
    invalid = batch(2, "incremental")
    invalid["complete"] = False
    assert synchronize(db, Provider(invalid))["error_code"] == "incomplete_snapshot"
    assert sv.status == "suspended" and sv.source_deleted_at is None
    with pytest.raises(Exception):
        with db.begin_nested():
            db.execute(text("DELETE FROM powercode_service_history"))


def test_package_import_never_publishes_and_changes_invalidate_mapping(signup):
    x = signup
    db = x.db
    v = batch()
    v["packages"] = [package()]
    synchronize(db, Provider(v))
    p = db.get(PowercodePackage, "SYN-P130")
    public = x.plan.public
    price = x.plan.monthly
    b = MappingInput(
        plan_id=x.plan.id,
        package_id=p.external_id,
        expected_plan_revision=x.plan.revision,
        package_digest=s.package_digest(p),
        reviewed_differences=True,
    )
    s.map_package(db, x.admin, b)
    assert x.plan.public == public and x.plan.monthly == price
    assert s.reviewed_mapping(db, x.plan)[1].external_id == p.external_id
    p.monthly = "75.00"
    db.flush()
    with pytest.raises(ValueError):
        s.reviewed_mapping(db, x.plan)


def ready(x):
    db = x.db
    public_service.submit(db, form(x))
    db.flush()
    a = db.scalar(select(Application))
    # Synthetic operator-approved prerequisites, not actual identity verification.
    a.state = "installed"
    p = PowercodePackage(
        **{k: v for k, v in package().items() if k != "updated_at"},
        source_updated_at=now() - timedelta(minutes=20),
    )
    db.add(p)
    db.flush()
    s.map_package(
        db,
        x.admin,
        MappingInput(
            plan_id=x.plan.id,
            package_id=p.external_id,
            expected_plan_revision=x.plan.revision,
            package_digest=s.package_digest(p),
            reviewed_differences=True,
        ),
    )
    q = Quote(
        application_id=a.id,
        number=1,
        plan_id=x.plan.id,
        snapshot={"currency": "USD", "monthly": "65.00"},
        expires_at=now() + timedelta(days=2),
        status="accepted",
        approved_by=x.admin.id,
    )
    db.add(q)
    db.flush()
    db.add(
        QuoteResponse(quote_id=q.id, decision="accepted", evidence={"synthetic": True})
    )
    db.add(
        Qualification(
            application_id=a.id,
            actor_id=x.admin.id,
            snapshot={"status": "requires_site_survey", "synthetic": True},
        )
    )
    # Existing canonical field order, completed by documented fixture technician.
    w = x.w
    w.status = "completed"
    w.completed_at = now()
    w.completion_notes = "Synthetic installation prerequisites satisfied"
    db.add(AppWork(application_id=a.id, kind="installation", work_order_id=w.id))
    db.add(ProvisionDraft(application_id=a.id, approved_by=x.admin.id))
    db.flush()
    b = ProvisionInput(
        application_id=a.id,
        expected_application_revision=a.revision,
        identity_verified=True,
        address_reviewed=True,
        qualification_reviewed=True,
        duplicate_reviewed=True,
        evidence_reference="SYN-IDENTITY-130",
    )
    r = s.prepare(db, x.admin, b)
    return a, db.get(PowercodeProvision, r["id"]), b


def decision(r):
    return DecisionInput(
        expected_revision=r.revision, preview_digest=r.preview_digest, authorize=True
    )


def test_provision_preview_is_encrypted_unique_and_approval_bound(signup):
    x = signup
    a, r, b = ready(x)
    assert "Synthetic Applicant" not in r.preview_ciphertext
    assert s.prepare(x.db, x.admin, b)["id"] == r.id
    with pytest.raises(PermissionError):
        s.decide(x.db, x.tech, r.id, decision(r), "approve")
    assert r.status == "pending_review"
    s.decide(x.db, x.admin, r.id, decision(r), "approve")
    assert r.status == "approved"
    a.revision += 1
    x.db.flush()
    with pytest.raises(ValueError):
        s.decide(x.db, x.admin, r.id, decision(r), "queue")
    assert x.db.scalar(select(func.count()).select_from(PowercodeProvision)) == 1


@pytest.mark.parametrize(
    "missing",
    [
        "identity_verified",
        "address_reviewed",
        "qualification_reviewed",
        "duplicate_reviewed",
    ],
)
def test_provision_attestations_fail_closed(signup, missing):
    x = signup
    a, r, b = ready(x)
    raw = b.model_dump()
    raw[missing] = False
    with pytest.raises(ValueError):
        s.prepare(x.db, x.admin, ProvisionInput(**raw))


def test_public_quote_acceptance_is_valid_without_legacy_ciphertext(signup):
    x = signup
    a, r, b = ready(x)
    assert x.db.get(Quote, r.quote_id).acceptance_ciphertext is None
    assert s.preview(x.db, x.admin, r.id)["preview"]["quote_id"] == r.quote_id
    with pytest.raises(Exception):
        with x.db.begin_nested():
            x.db.execute(
                text("UPDATE powercode_provisions SET preview_digest=:v"),
                {"v": "a" * 64},
            )


def test_admin_api_and_credentials_are_private(signup, tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    x = signup
    a, r, b = ready(x)
    key = tmp_path / "vault.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "notifications_vault_key_file", str(key))
    s.credential(x.db, x.admin, CredentialInput(value="synthetic-private-api-key"))
    assert "synthetic-private-api-key" not in json.dumps(s.config_view(x.db))
    assert s.config_view(x.db)["native_enabled"] is False
    app.dependency_overrides[get_db] = lambda: x.db
    try:
        c = TestClient(app)
        assert c.get("/api/customers/powercode").status_code == 401
        x.other.role = "viewer"
        x.db.flush()
        assert (
            c.get("/api/customers/powercode", headers=auth(x.other)).status_code == 403
        )
        x.other.role = "operator"
        x.db.flush()
        assert (
            c.post(
                "/api/customers/powercode/configuration", headers=auth(x.other), json={}
            ).status_code
            == 403
        )
        assert (
            c.get("/api/customers/powercode", headers=auth(x.admin)).status_code == 200
        )
        assert (
            c.get(
                f"/api/customers/powercode/provisions/{r.id}", headers=auth(x.admin)
            ).headers["cache-control"]
            == "no-store"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def worker(signup, monkeypatch):
    x = signup
    a, r, b = ready(x)
    for k, v in [
        ("app_env", "staging"),
        ("offline_mode", True),
        ("powercode_fixture_enabled", True),
    ]:
        monkeypatch.setattr(settings, k, v)
    s.configure(x.db, x.admin, ConnectionInput(emergency_disabled=False))
    s.decide(x.db, x.admin, r.id, decision(r), "approve")
    s.decide(x.db, x.admin, r.id, decision(r), "queue")
    x.db.flush()
    # Independent sessions share only the disposable fixture connection; commits
    # are real SAVEPOINT releases under the fixture's rollback-only transaction.
    bind = x.db.connection()

    def factory():
        return Session(bind=bind, join_transaction_mode="create_savepoint")

    return x, r, factory


def test_worker_preserves_partial_acceptance_and_never_recreates(worker):
    x, r, factory = worker
    calls = []

    def send(req):
        calls.append(req.url.path)
        return httpx.Response(
            200,
            json={"outcome": "accepted", "external_id": "SYN-ACCOUNT"}
            if len(calls) == 1
            else {"outcome": "not_accepted", "error_code": "validation_failed"},
        )

    p = FixtureProvisioner(httpx.MockTransport(send))
    assert s.simulated_worker(factory, p)["status"] == "account_created"
    assert s.simulated_worker(factory, p)["status"] == "failed"
    assert s.simulated_worker(factory, p)["status"] == "idle"
    x.db.expire_all()
    assert x.db.get(PowercodeProvision, r.id).external_account_id == "SYN-ACCOUNT"
    assert calls == ["/noc-provision-v1/account", "/noc-provision-v1/service"]


def test_ambiguous_write_is_quarantined_without_retry(worker):
    x, r, factory = worker
    calls = []

    def send(req):
        calls.append(req)
        raise httpx.ReadTimeout("synthetic")

    p = FixtureProvisioner(httpx.MockTransport(send))
    assert s.simulated_worker(factory, p)["status"] == "reconciliation_required"
    assert s.simulated_worker(factory, p)["status"] == "idle" and len(calls) == 1


def test_worker_lease_restart_and_independent_activation(worker):
    x, r, factory = worker
    p = FixtureProvisioner(
        httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "outcome": "accepted",
                    "external_id": "SYN-A"
                    if req.url.path.endswith("account")
                    else "SYN-S",
                },
            )
        )
    )
    assert s.simulated_worker(factory, p)["status"] == "account_created"
    assert s.simulated_worker(factory, p)["status"] == "activation_pending"
    x.db.expire_all()
    r = x.db.get(PowercodeProvision, r.id)
    b = ActivationInput(
        **decision(r).model_dump(),
        independently_authorized=True,
        evidence_reference="SYN-ACTIVATION-REVIEW",
    )
    s.decide(x.db, x.admin, r.id, b, "approve_activation")
    assert r.status == "activation_pending" and r.activation_approved_by == x.admin.id
    assert s.simulated_worker(factory, p)["status"] == "idle"


def test_crashed_submission_is_not_resubmitted(worker):
    x, r, factory = worker
    r.status = "submitted"
    r.updated_at = now() - timedelta(seconds=60)
    x.db.flush()
    p = FixtureProvisioner(
        httpx.MockTransport(
            lambda req: pytest.fail("Ambiguous prior write must never repeat")
        )
    )
    assert s.simulated_worker(factory, p)["status"] == "reconciliation_required"


def test_current_inflight_submission_is_not_claimed(worker):
    x, r, factory = worker
    r.status = "submitted"
    r.updated_at = now()
    x.db.flush()
    p = FixtureProvisioner(
        httpx.MockTransport(lambda req: pytest.fail("Live lease must not be stolen"))
    )
    assert s.simulated_worker(factory, p)["status"] == "idle"


def test_interleaved_worker_cannot_repeat_inflight_account_creation(worker):
    x, r, factory = worker
    calls = []
    blocked = FixtureProvisioner(
        httpx.MockTransport(
            lambda req: pytest.fail("Second worker must not submit a live lease")
        )
    )

    def send(req):
        calls.append(req)
        assert s.simulated_worker(factory, blocked)["status"] == "idle"
        return httpx.Response(
            200, json={"outcome": "accepted", "external_id": "SYN-SINGLE-ACCOUNT"}
        )

    p = FixtureProvisioner(httpx.MockTransport(send))
    assert (
        s.simulated_worker(factory, p)["status"] == "account_created"
        and len(calls) == 1
    )


def test_emergency_disable_prevents_worker_transport(worker):
    x, r, factory = worker
    x.db.get(PowercodeConnection, 1).emergency_disabled = True
    x.db.flush()
    p = FixtureProvisioner(
        httpx.MockTransport(lambda req: pytest.fail("Emergency disabled"))
    )
    assert s.simulated_worker(factory, p)["status"] == "disabled"


def test_sanitized_source_fixture_has_multiple_statuses_and_duplicate_contacts():
    value = json.loads(
        (Path(__file__).parent / "fixtures/noc130-powercode.json").read_text()
    )
    b = parse(value)
    assert {r.status for r in b.services} == {
        "active",
        "suspended",
        "disconnected",
        "unknown",
    }
    assert {r.service_type for r in b.services} == {"wireless", "fiber", "lte", "5g"}
    assert b.accounts[0].phone == b.accounts[1].phone
    assert len(b.packages) == 4 and all(
        r.revenue_basis == "contracted" for r in b.services
    )
