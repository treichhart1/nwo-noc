"""Synthetic acquisition extensions; actual PostgreSQL tests remain mandatory in CI."""

from datetime import timedelta
from uuid import uuid4
import json
from types import SimpleNamespace
from pathlib import Path
import importlib.util
import pytest
from sqlalchemy import select, func, text
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from app.core.config import settings
from app.signup import service as s, public_service as ps, crypto
from app.signup.models import Application, Quote, QuoteResponse
from app.signup.contracts import (
    SignupInput,
    QuoteInput,
    Control,
    NotifyInput,
    QuoteAccessInput,
    QualifyInput,
)
from app.signup.acquisition import prepare_quote_access, quote_token, analytics
from app.signup import communications
from app.notifications.models import (
    NotificationPlan,
    NotificationRecipient,
    NotificationDelivery,
    NotificationContactSuppression,
)
from app.notifications import service as ns
from app.notifications.sms_identity import contact_hash as native_hash
from app.notifications.sms_models import SMSWebhookEvent
from app.field.models import now
from test_signup import signup, form, b, R
from test_field import field
from test_equipment import stock
from test_notifications import network
from test_powercode_customers import inventory, imported
from test_rf_postgres_integration import postgres_db


@pytest.mark.parametrize("technology", ["wireless", "fiber", "hybrid", "lte", "5g"])
def test_supported_signup_technologies_remain_explicit(technology):
    raw = {
        "submission_key": str(uuid4()),
        "audience": "residential",
        "customer_name": "Synthetic Applicant",
        "first_name": "Synthetic",
        "last_name": "Applicant",
        "service_address": {
            "street": "129 Synthetic Road",
            "city": "Synthetic Town",
            "state": "OH",
            "postal_code": "43500",
        },
        "email": "test@example.com",
        "phone": "+15555550129",
        "preferred_contact": "none",
        "privacy_consent": True,
        "technology": technology,
        "plan_id": 1,
        "turnstile_token": "synthetic",
    }
    assert SignupInput(**raw).technology == technology
    raw["last_name"] = ""
    with pytest.raises(ValueError):
        SignupInput(**raw)


def test_quote_discount_validation():
    with pytest.raises(ValueError):
        QuoteInput(
            operation_key=uuid4(),
            reason=R,
            expected_revision=1,
            plan_id=1,
            currency="USD",
            discount="-1",
            expires_at=now() + timedelta(days=1),
        )


def application(x, **kw):
    body = form(x, **kw)
    ps.submit(x.db, body, verify=lambda *args: True)
    x.db.flush()
    return x.db.scalar(
        select(Application).where(
            Application.submission_key == str(body.submission_key)
        )
    )


def quote(x, a):
    a.state = "qualified"
    q = s.quote_draft(
        x.db,
        x.admin,
        a,
        b(
            QuoteInput,
            expected_revision=a.revision,
            plan_id=a.plan_id,
            currency="USD",
            monthly="65.00",
            installation="100.00",
            equipment="20.00",
            construction="0.00",
            discount="10.00",
            expires_at=now() + timedelta(days=1),
        ),
    )
    for action in ["approve_quote", "issue_quote"]:
        s.control(
            x.db,
            x.admin,
            a,
            b(
                Control,
                expected_revision=a.revision,
                action=action,
                quote_id=q["id"],
                evidence="Synthetic independently reviewed quote",
            ),
        )
    prepare_quote_access(
        x.db,
        x.admin,
        a,
        b(QuoteAccessInput, expected_revision=a.revision, quote_id=q["id"]),
    )
    x.db.flush()
    return x.db.get(Quote, q["id"]), quote_token(x.db, x.admin, a.id, q["id"])["token"]


def test_postgres_quote_capability_response_is_immutable_and_retry_safe(signup):
    x = signup
    a = application(x)
    q, token = quote(x, a)
    hashed = crypto.digest("quote_access", token)
    result = x.db.scalar(
        text("SELECT public.noc_signup_quote_read(:hash)"), {"hash": hashed}
    )
    assert result["available"] and result["terms"]["discount"] == "10.00"
    assert not any(
        k in json.dumps(result)
        for k in ["customer_name", "email", "service_address", "powercode_plan_ref"]
    )
    first = x.db.scalar(
        text("SELECT public.noc_signup_quote_respond(:hash,:decision)"),
        {"hash": hashed, "decision": "accepted"},
    )
    retry = x.db.scalar(
        text("SELECT public.noc_signup_quote_respond(:hash,:decision)"),
        {"hash": hashed, "decision": "accepted"},
    )
    assert first == retry and first["decision"] == "accepted"
    opposite = x.db.scalar(
        text("SELECT public.noc_signup_quote_respond(:hash,:decision)"),
        {"hash": hashed, "decision": "rejected"},
    )
    assert opposite["available"] is False
    assert (
        x.db.scalar(
            select(func.count(QuoteResponse.id)).where(QuoteResponse.quote_id == q.id)
        )
        == 1
    )
    x.db.refresh(a)
    assert a.state == "customer_accepted"
    with pytest.raises(Exception):
        with x.db.begin_nested():
            x.db.execute(
                text(
                    "UPDATE signup_quote_responses SET decision='rejected' WHERE quote_id=:q"
                ),
                {"q": q.id},
            )


def test_postgres_expired_or_superseded_quotes_are_not_public(signup):
    x = signup
    a = application(x)
    q, token = quote(x, a)
    assert not x.db.scalar(
        text("SELECT public.noc_signup_quote_read(:hash)"), {"hash": "a" * 64}
    )["available"]
    a.state = "under_review"
    x.db.flush()
    assert not x.db.scalar(
        text("SELECT public.noc_signup_quote_read(:hash)"),
        {"hash": crypto.digest("quote_access", token)},
    )["available"]


def test_postgres_lte_5g_qualification_is_manual(signup):
    x = signup
    a = application(x)
    a.technology = "lte"
    r = s.qualify(
        x.db,
        x.admin,
        a,
        b(
            QualifyInput,
            expected_revision=a.revision,
            latitude=41.4,
            longitude=-84.2,
            coordinate_source="Synthetic supplied coordinates",
        ),
    )
    assert r["snapshot"]["status"] == "manual_review_required"


def test_postgres_prospect_plan_uses_canonical_queue_and_consent(
    signup, tmp_path, monkeypatch
):
    x = signup
    key = tmp_path / "sms.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "sms_callback_key_file", str(key))
    a = application(x, preferred_contact="sms", consent_sms=True)
    d = s.notification(
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
    result = communications.prepare(x.db, x.admin, d["id"])
    plan = x.db.get(NotificationPlan, result["id"])
    assert communications.prepare(x.db, x.admin, d["id"])["id"] == plan.id
    assert plan.incident_id is None and plan.maintenance_id is None
    r = x.db.scalar(
        select(NotificationRecipient).where(NotificationRecipient.plan_id == plan.id)
    )
    assert r.customer_id is None and r.application_id == a.id
    assert ns.digest(communications.snapshot(x.db, plan)) == plan.fingerprint
    ns.approve(x.db, plan.id, x.admin.id, expected_fingerprint=plan.fingerprint)
    deliveries = list(
        x.db.scalars(
            select(NotificationDelivery).where(NotificationDelivery.plan_id == plan.id)
        )
    )
    assert sum(v.channel == "sms" for v in deliveries) == 1 and not any(
        v.channel == "slack" for v in deliveries
    )
    identity = native_hash(s.pii(a)["phone"])
    # A verified provider STOP blocks this prospect independently of provider choice.
    from app.notifications.sms_models import SMSProviderConfig

    x.db.add(
        SMSProviderConfig(
            name="twilio", config={}, revision=1, enabled=False, live_authorized=False
        )
    )
    x.db.flush()
    x.db.add(
        SMSWebhookEvent(
            key="f" * 64,
            provider="twilio",
            message_id="synthetic",
            kind="stop",
            body_hash="d" * 64,
            contact_hash=identity,
        )
    )
    x.db.flush()
    with pytest.raises(ValueError):
        communications.snapshot(x.db, plan)


def test_postgres_opt_out_invalidates_existing_prospect_plan(
    signup, tmp_path, monkeypatch
):
    x = signup
    key = tmp_path / "sms.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "sms_callback_key_file", str(key))
    a = application(x, preferred_contact="sms", consent_sms=True)
    d = s.notification(
        x.db,
        x.admin,
        a,
        b(
            NotifyInput,
            expected_revision=a.revision,
            template="installation_reminder",
            channel="sms",
        ),
    )
    p = communications.prepare(x.db, x.admin, d["id"])
    s.control(
        x.db,
        x.admin,
        a,
        b(
            Control,
            expected_revision=a.revision,
            action="record_opt_out",
            evidence="Synthetic explicit contact withdrawal",
        ),
    )
    with pytest.raises(ValueError):
        communications.snapshot(x.db, x.db.get(NotificationPlan, p["id"]))
    assert x.db.get(NotificationContactSuppression, ns.contact_hash(s.pii(a)["phone"]))


def test_postgres_analytics_are_prospective_and_bounded(signup):
    x = signup
    application(x)
    r = analytics(x.db, x.admin, now() - timedelta(days=1), now() + timedelta(days=1))
    assert r["total"] == 1 and r["prospective_monthly_revenue"] == {"USD": "65.00"}
    assert "not billed" in r["revenue_basis"]


def test_signup_plugin_zip_is_deterministic_and_does_not_embed_credentials(tmp_path):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "signup_builder", root / "scripts/signup/build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = module.build(root, tmp_path / "a")
    second = module.build(root, tmp_path / "b")
    assert first == second and first["version"] == "129.0.0"
    import zipfile

    with zipfile.ZipFile(tmp_path / "a/nwo-customer-signup.zip") as archive:
        assert len(archive.namelist()) == 7
        assert all(not n.endswith((".env", ".key", ".pem")) for n in archive.namelist())


from test_sms_postgres import native, receipt, NOW, PHONE
from app.notifications import sms_service as native_service
from app.notifications.sms_models import SMSProviderConfig, SMSRouting
from app.notifications.models import NotificationAttempt
from app.signup.models import Plan as SignupPlan


@pytest.mark.parametrize("provider", ["twilio", "telnyx"])
@pytest.mark.parametrize("withdraw", [False, True])
def test_postgres_prospect_native_contract_rechecks_consent_and_preserves_receipt(
    signup, native, provider, withdraw
):
    x = signup
    # Both fixtures share the canonical network; retain the legacy plan and replace
    # only this synthetic test's active delivery, not any queue architecture.
    legacy = native.delivery
    legacy.state = "cancelled"
    x.plan = x.db.scalar(
        select(SignupPlan).where(SignupPlan.name == "Synthetic configured plan")
    )
    a = application(x, phone=PHONE, preferred_contact="sms", consent_sms=True)
    draft = s.notification(
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
    result = communications.prepare(x.db, x.admin, draft["id"])
    plan = x.db.get(NotificationPlan, result["id"])
    plan.due_at = NOW
    ns.approve(
        x.db, plan.id, x.admin.id, expected_fingerprint=plan.fingerprint, now=NOW
    )
    delivery = x.db.scalar(
        select(NotificationDelivery).where(
            NotificationDelivery.plan_id == plan.id,
            NotificationDelivery.channel == "sms",
        )
    )
    routing = x.db.get(SMSRouting, 1)
    routing.config = {"primary": provider, "secondary": None, "failover": False}
    routing.revision += 1
    for name in ["twilio", "telnyx"]:
        row = x.db.get(SMSProviderConfig, name)
        row.config = row.config | {
            "categories": ["outage", "maintenance", "restoration", "signup_application"]
        }
        row.revision += 1
    native_service.authorize_delivery(
        x.db, delivery.id, x.admin.id, "synthetic-prospect-release", plan.fingerprint
    )
    if withdraw:
        s.control(
            x.db,
            x.admin,
            a,
            b(
                Control,
                expected_revision=a.revision,
                action="record_opt_out",
                evidence="Explicit synthetic withdrawal before worker claim",
            ),
        )
    x.db.flush()
    native_service.run_native_batch(
        native.factory,
        transports={provider: receipt(provider, plan.message)},
        now=NOW,
        limit=1,
    )
    x.db.expire_all()
    delivery = x.db.get(NotificationDelivery, delivery.id)
    attempts = list(
        x.db.scalars(
            select(NotificationAttempt).where(
                NotificationAttempt.delivery_id == delivery.id
            )
        )
    )
    if withdraw:
        assert delivery.state == "suppressed" and not attempts
    else:
        assert (
            delivery.state == "accepted"
            and len(attempts) == 1
            and attempts[0].acceptance == "accepted"
        )
        assert (
            native_service.run_native_batch(
                native.factory, transports={}, now=NOW, limit=1
            )["processed"]
            == 0
        )


def test_postgres_install_asset_preserves_canonical_inventory_history(signup):
    from app.signup.contracts import WorkInput, InstallAssetInput
    from app.signup.acquisition import install_asset
    from app.equipment.models import Asset, Location, Movement
    from test_equipment import asset as create_asset
    from app.field.models import WorkOrder

    x = signup
    a = application(x)
    a.state = "installation_pending"
    a.proposed_site_id = x.a.id
    x.db.flush()
    work = s.make_work(
        x.db,
        x.admin,
        a,
        b(
            WorkInput,
            expected_revision=a.revision,
            kind="installation",
            site_id=x.a.id,
            technician_id=x.tech.id,
            equipment=["Synthetic radio"],
            scheduled_at=now() + timedelta(days=1),
        ),
    )
    w = x.db.get(WorkOrder, work["work_order_id"])
    w.status = "in_progress"
    spare = create_asset(x)
    row = spare
    location = Location(
        code="SYN-129-CPE", name="Synthetic customer premises", kind="customer_premises"
    )
    x.db.add(location)
    x.db.flush()
    request = b(
        InstallAssetInput,
        expected_revision=a.revision,
        work_order_id=w.id,
        asset_id=row.id,
        asset_revision=row.revision,
        customer_location_id=location.id,
    )
    with pytest.raises(PermissionError):
        install_asset(x.db, x.other, a, request)
    result = install_asset(x.db, x.tech, a, request)
    assert (
        result["network_changes"] == 0
        and w.asset_id == row.id
        and row.state == "installed"
    )
    assert (
        x.db.scalar(select(func.count(Movement.id)).where(Movement.asset_id == row.id))
        >= 2
    )
    with pytest.raises(ValueError):
        install_asset(x.db, x.tech, a, request)
