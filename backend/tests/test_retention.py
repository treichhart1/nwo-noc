"""Cancellation evidence uses synthetic customers and rollback-only PostgreSQL."""

import importlib.util, json, os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path
from decimal import Decimal
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from app.core.config import settings
from app.core.database import get_db
from app.models.entities import User
from app.customers.models import CustomerAccount, CustomerService
from app.customers.powercode_models import PowercodeServiceHistory
from app.signup import crypto
from app.signup.public_app import app as public
from app.retention import service as s
from app.retention import analytics as a
from app.retention.contracts import *
from app.retention.models import *
from app.retention.communications import prepare, snapshot, TEMPLATES
from app.notifications.models import (
    NotificationPlan,
    NotificationRecipient,
    NotificationContactSuppression,
)
from app.notifications.service import current_snapshot
from app.notifications.slack_sources import snapshot as slack_snapshot, payload
from test_signup import signup
from test_field import field, auth
from test_equipment import stock
from test_notifications import network
from test_powercode_customers import inventory, imported
from test_rf_postgres_integration import postgres_db

ROOT = Path(__file__).resolve().parents[2]
NOTE = "Synthetic reviewed cancellation evidence"


def form(**kw):
    key = str(uuid4())
    return dict(
        submission_key=key,
        customer_name="Synthetic cancellation",
        email="synthetic@example.com",
        phone="+15555550131",
        service_address="131 Synthetic Road",
        requested_date=date.today().isoformat(),
        reason="price",
        comments="Synthetic only",
        policy_id=1,
        equipment_ack=True,
        policy_ack=True,
        privacy_ack=True,
        consent_sms=False,
        turnstile_token="synthetic-pass:" + key,
        **kw,
    )


def opening(members, complete=True):
    return SimpleNamespace(members=members, complete=complete)


def test_no_historical_denominator_is_not_zero():
    r = a.cohort_metrics(None, None)
    assert r["customer_churn_rate"] is None and r["service_churn_rate"] is None
    assert r["net_customer_growth"] is None


def test_account_churn_requires_loss_of_all_services():
    first = opening(
        [
            {"service_id": 1, "customer_id": 8},
            {"service_id": 2, "customer_id": 8},
            {"service_id": 3, "customer_id": 9},
        ]
    )
    last = opening(
        [{"service_id": 2, "customer_id": 8}, {"service_id": 4, "customer_id": 10}]
    )
    r = a.cohort_metrics(first, last)
    assert r["customer_cancellations"] == 1 and r["cohort_service_losses"] == 2
    assert r["customer_churn_rate"] == 0.5 and r["service_churn_rate"] == 2 / 3
    assert r["new_customers"] == 1 and r["net_customer_growth"] == 0
    assert a.cohort_metrics(first, opening([], False))["service_churn_rate"] is None


def test_revenue_keeps_currency_basis_and_missing_values_separate():
    first = opening(
        [
            {
                "service_id": 1,
                "customer_id": 8,
                "monthly": "50.00",
                "currency": "USD",
                "basis": "contracted",
            },
            {
                "service_id": 2,
                "customer_id": 8,
                "monthly": "20.00",
                "currency": "USD",
                "basis": "contracted",
            },
        ]
    )
    last = opening(
        [
            {
                "service_id": 2,
                "customer_id": 8,
                "monthly": "20.00",
                "currency": "USD",
                "basis": "contracted",
            }
        ]
    )
    r = a.cohort_revenue(first, last)[0]
    assert (
        r["lost"] == "50.00"
        and r["net_change"] == "-50.00"
        and r["basis"] == "contracted"
    )
    assert (
        a.cohort_revenue(first, opening([{"service_id": 3, "customer_id": 9}])) is None
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("equipment_ack", False),
        ("policy_ack", False),
        ("privacy_ack", False),
        ("phone", "not-a-phone"),
        ("requested_date", "2000-01-01"),
        ("reason", "<script>"),
        ("email", "bad"),
    ],
)
def test_public_validation(field, value):
    b = form()
    b[field] = value
    with pytest.raises(ValidationError):
        PublicInput(**b)


def test_sms_consent_optional_and_no_payment_or_account_lookup_contract():
    assert PublicInput(**form()).consent_sms is False
    with pytest.raises(ValidationError):
        PublicInput(**{**form(), "balance": "1"})
    assert "customer_id" not in PublicInput.model_fields


@pytest.mark.parametrize("money", ["-1", "NaN", "Infinity", "1.001"])
def test_competitor_pricing_validation(money):
    with pytest.raises(ValidationError):
        Replacement(monthly=money, currency="USD")


def test_customer_offer_not_independently_verified():
    with pytest.raises(ValidationError):
        ObservationInput(
            competitor_id=1,
            offer={},
            source_kind="customer_reported",
            source_reference="Synthetic report",
            observed_on=date.today(),
            verified=True,
        )


def test_installable_plugin_reproducible_and_allowlisted(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "cancel_build", ROOT / "scripts/cancellation/build.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    one = mod.build(ROOT, tmp_path / "one")
    two = mod.build(ROOT, tmp_path / "two")
    assert one == two and len(one["members"]) == 7
    import zipfile

    with zipfile.ZipFile(tmp_path / "one/nwo-customer-cancellation.zip") as z:
        assert len(z.namelist()) == 7 and all(
            x.startswith("nwo-customer-cancellation/") for x in z.namelist()
        )
    transport = (ROOT / "wordpress/nwo-customer-cancellation/transport.php").read_text()
    assert (
        "CURLOPT_SSL_VERIFYPEER" in transport
        and "/public/cancellation/health" in transport
    )


@pytest.fixture
def cancellation(signup, monkeypatch, tmp_path):
    x = signup
    from cryptography.fernet import Fernet

    callback_key = tmp_path / "sms-callback.key"
    callback_key.write_bytes(Fernet.generate_key())
    callback_key.chmod(0o600)
    monkeypatch.setattr(settings, "sms_callback_key_file", str(callback_key))
    monkeypatch.setattr(settings, "retention_public_enabled", True)
    p = s.configure(
        x.db,
        x.admin,
        PolicyInput(
            text="After monthly billing the full month may remain payable, subject to operator review. Acknowledgement alone does not establish a debt."
        ),
    )
    b = form()
    b["policy_id"] = p["id"]
    x.body = PublicInput(**b)
    c = Case(
        reference="CAN-" + str(uuid4()),
        submission_key=str(x.body.submission_key),
        digest="a" * 64,
        receipt_hash="b" * 64,
        requested_date=x.body.requested_date,
        reported_reason=x.body.reason,
        policy_id=p["id"],
        policy_ack_at=s.now(),
        equipment_ack=True,
    )
    crypto.initialize(c, x.body.model_dump(mode="json"))
    x.db.add(c)
    x.db.flush()
    x.case = c
    x.service.monthly_recurring = Decimal("65")
    x.service.currency = "USD"
    x.service.revenue_basis = "contracted"
    x.db.flush()
    return x


def verify(x):
    return s.verify(
        x.db,
        x.admin,
        x.case.id,
        Verification(
            expected_revision=x.case.revision,
            notes=NOTE,
            customer_id=x.account.id,
            service_ids=[x.service.id],
            identity_verified=True,
            address_verified=True,
            evidence_reference="SYN-IDENTITY",
        ),
    )


def test_verified_multi_service_request_preserves_other_services(cancellation):
    x = cancellation
    second = CustomerService(
        external_id="SYN131-SECOND",
        customer_id=x.account.id,
        status="active",
        service_type="fiber",
        source_updated_at=s.now(),
        last_synced_at=s.now(),
    )
    x.db.add(second)
    x.db.flush()
    verify(x)
    assert (
        len(s.case_view(x.db, x.case)["services"]) == 1
        and x.case.customer_id == x.account.id
    )
    assert second.status == "active" and x.service.status == "active"
    assert x.body.customer_name not in x.case.pii_ciphertext
    assert (
        s.case_view(x.db, x.case, True)["customer_submission"]["customer_name"]
        == x.body.customer_name
    )


@pytest.mark.parametrize(
    "bad", ["ownership", "identity", "stale_revision", "unauthorized"]
)
def test_identity_and_concurrency_rejections(cancellation, bad):
    x = cancellation
    b = Verification(
        expected_revision=x.case.revision + (bad == "stale_revision"),
        notes=NOTE,
        customer_id=x.account.id,
        service_ids=[999999] if bad == "ownership" else [x.service.id],
        identity_verified=bad != "identity",
        address_verified=True,
        evidence_reference="SYN-IDENTITY",
    )
    with pytest.raises((ValueError, PermissionError)):
        s.verify(x.db, x.tech if bad == "unauthorized" else x.admin, x.case.id, b)
    assert x.service.status == "active"


def test_transition_cannot_disconnect_powercode(cancellation):
    x = cancellation
    verify(x)
    for state in ["cancellation_confirmed", "awaiting_disconnection"]:
        s.change(
            x.db,
            x.admin,
            x.case.id,
            StatusChange(expected_revision=x.case.revision, notes=NOTE, status=state),
        )
    with pytest.raises(ValueError, match="Observed"):
        s.change(
            x.db,
            x.admin,
            x.case.id,
            StatusChange(
                expected_revision=x.case.revision, notes=NOTE, status="disconnected"
            ),
        )
    assert x.service.status == "active"
    source = PowercodeServiceHistory(
        service_id=x.service.id,
        previous_status="active",
        status="disconnected",
        snapshot_id="SYN131",
        source_updated_at=s.now(),
        recorded_at=s.now(),
    )
    x.db.add(source)
    x.service.status = "disconnected"
    x.db.flush()
    s.change(
        x.db,
        x.admin,
        x.case.id,
        StatusChange(
            expected_revision=x.case.revision, notes=NOTE, status="disconnected"
        ),
    )
    assert (
        x.db.scalar(
            select(CaseService.source_history_id).where(
                CaseService.case_id == x.case.id
            )
        )
        == source.id
    )
    result = a.summary(x.db, date.today().strftime("%Y-%m"))
    assert (
        result["observed_unique_service_cancellations"] == 1
        and result["customer_churn_rate"] is None
    )
    assert [
        r["monthly_recurring_snapshot"]
        for r in result["revenue_loss"]
        if r["dimension"] == "total"
    ] == ["65.00"]


def test_offer_approval_acceptance_and_retained_no_automatic_change(cancellation):
    x = cancellation
    verify(x)
    o = s.offer(
        x.db,
        x.admin,
        x.case.id,
        OfferInput(
            expected_revision=x.case.revision,
            notes=NOTE,
            kind="equipment_replacement_offer",
            terms="Synthetic operator-reviewed replacement offer",
            expires_at=s.now() + timedelta(days=2),
            eligible=True,
            evidence_reference="SYN-OFFER",
            monthly_cost="0",
            one_time_cost="20",
            currency="USD",
        ),
    )
    for action in ["approve", "accepted"]:
        s.offer_decide(
            x.db,
            x.admin,
            x.case.id,
            o["id"],
            OfferDecision(
                expected_revision=x.case.revision,
                notes=NOTE,
                fingerprint=o["fingerprint"],
                action=action,
                evidence_reference="SYN-CUSTOMER-ACCEPTANCE",
            ),
        )
    s.retention(
        x.db,
        x.admin,
        x.case.id,
        RetentionChange(
            expected_revision=x.case.revision,
            notes=NOTE,
            state="customer_retained",
            evidence_reference="SYN-RETAINED",
        ),
    )
    assert x.case.status == "withdrawn" and x.service.monthly_recurring == 65
    assert (
        s.case_view(x.db, x.case, True)["offers"][0]["decision_evidence"]["decision"]
        == "accepted"
    )


def test_changed_source_invalidates_offer(cancellation):
    x = cancellation
    verify(x)
    o = s.offer(
        x.db,
        x.admin,
        x.case.id,
        OfferInput(
            expected_revision=x.case.revision,
            notes=NOTE,
            kind="technical_investigation",
            terms=NOTE,
            expires_at=s.now() + timedelta(days=1),
            eligible=True,
            evidence_reference="SYN-OFFER",
        ),
    )
    x.service.plan = "Changed package"
    x.db.flush()
    with pytest.raises(ValueError, match="changed"):
        s.offer_decide(
            x.db,
            x.admin,
            x.case.id,
            o["id"],
            OfferDecision(
                expected_revision=x.case.revision,
                notes=NOTE,
                fingerprint=o["fingerprint"],
                action="approve",
                evidence_reference="SYN-APPROVAL",
            ),
        )


def test_historical_preview_atomic_unmatched_and_no_fake_ack(cancellation):
    x = cancellation
    data = "id,date,reason,service\nSYN-OLD,2020-01-01,Unknown reason,missing-service\n"
    cols = {
        "external_id": "id",
        "requested_date": "date",
        "reason": "reason",
        "service_external_id": "service",
    }
    preview = s.historical(x.db, x.admin, CSVInput(csv_text=data, columns=cols))
    assert preview["unmatched"] == 1
    result = s.historical(
        x.db,
        x.admin,
        CSVInput(
            csv_text=data,
            columns=cols,
            dry_run=False,
            expected_digest=preview["digest"],
        ),
    )
    assert result["rows"] == 1
    row = x.db.scalar(select(Case).where(Case.source == "historical_report"))
    assert (
        row.customer_id is None
        and row.policy_ack_at is None
        and row.reported_reason == "unknown"
    )
    assert crypto.unseal(row, row.pii_ciphertext)["reported_disconnected_date"] is None
    assert s.historical(
        x.db,
        x.admin,
        CSVInput(
            csv_text=data,
            columns=cols,
            dry_run=False,
            expected_digest=preview["digest"],
        ),
    )["already_imported"]
    with pytest.raises(ValueError):
        s.historical(
            x.db,
            x.admin,
            CSVInput(csv_text=data.replace("2020-01-01", "not-date"), columns=cols),
        )


def test_public_gateway_generic_receipt_csrf_turnstile_and_retry(cancellation):
    x = cancellation

    def database():
        yield x.db

    public.dependency_overrides[get_db] = database
    try:
        with TestClient(public) as client:
            cfg = client.get("/public/cancellation/config").json()
            b = form()
            b["policy_id"] = cfg["policy"]["id"]
            headers = {
                "Origin": settings.signup_public_origin,
                "X-Signup-CSRF": cfg["csrf_token"],
            }
            assert (
                client.post("/public/cancellation/requests", json=b).status_code == 403
            )
            bad = {**b, "turnstile_token": "bad"}
            assert (
                client.post(
                    "/public/cancellation/requests", json=bad, headers=headers
                ).status_code
                == 403
            )
            first = client.post(
                "/public/cancellation/requests", json=b, headers=headers
            )
            assert first.status_code == 200, first.text
            second = client.post(
                "/public/cancellation/requests", json=b, headers=headers
            )
            assert first.json() == second.json()
            assert set(first.json()) == {
                "received",
                "reference",
                "receipt_token",
                "message",
            }
            assert (
                client.get("/api/retention").status_code == 404
                and client.get("/api/customers").status_code == 404
            )
            assert (
                client.get("/public/cancellation/config").headers["cache-control"]
                == "no-store"
            )
    finally:
        public.dependency_overrides.clear()


def test_public_sql_role_cannot_read_or_mutate_private_tables(cancellation):
    x = cancellation
    with x.db.begin_nested():
        x.db.execute(text("CREATE ROLE noc131_restricted NOLOGIN"))
        x.db.execute(text("GRANT USAGE ON SCHEMA public TO noc131_restricted"))
        x.db.execute(
            text(
                "GRANT EXECUTE ON FUNCTION public.noc_cancel_config() TO noc131_restricted"
            )
        )
        x.db.execute(text("SET LOCAL ROLE noc131_restricted"))
        assert x.db.scalar(
            text("SELECT public.noc_cancel_config()->'policy'->>'id'")
        ) == str(x.case.policy_id)
        for table in [
            "customer_accounts",
            "customer_services",
            "retention_cases",
            "retention_policies",
            "retention_offers",
            "users",
            "notification_credentials",
        ]:
            assert not x.db.scalar(
                text("SELECT has_table_privilege(current_user,:table,'SELECT')"),
                {"table": table},
            )
        x.db.execute(text("RESET ROLE"))


def test_history_immutability(cancellation):
    x = cancellation
    verify(x)
    with pytest.raises(DBAPIError), x.db.begin_nested():
        x.db.execute(
            text("UPDATE retention_case_events SET status='closed' WHERE case_id=:id"),
            {"id": x.case.id},
        )
    with pytest.raises(DBAPIError), x.db.begin_nested():
        x.db.execute(
            text("UPDATE retention_cases SET reported_reason='unknown' WHERE id=:id"),
            {"id": x.case.id},
        )


def test_sms_canonical_consent_cross_provider_hold_and_changed_source(cancellation):
    x = cancellation
    verify(x)
    x.account.sms_consent = "granted"
    x.account.communication_preference = "sms"
    x.account.sms_opt_out = False
    x.db.flush()
    p = prepare(
        x.db,
        x.admin,
        x.case.id,
        Communication(
            expected_revision=x.case.revision, notes=NOTE, template="processing_update"
        ),
    )
    plan = x.db.get(NotificationPlan, p["id"])
    current, _ = current_snapshot(x.db, plan, s.now())
    assert current["customer_id"] == x.account.id and plan.incident_id is None
    recipient = x.db.scalar(
        select(NotificationRecipient).where(NotificationRecipient.plan_id == plan.id)
    )
    assert recipient.application_id is None and recipient.service_ids == [x.service.id]
    x.db.add(
        NotificationContactSuppression(
            contact_hash=recipient.contact_hash, reason="verified_stop"
        )
    )
    x.db.flush()
    with pytest.raises(ValueError):
        snapshot(x.db, plan)
    assert x.service.status == "active"


def test_slack_is_approved_source_without_customer_information(cancellation):
    x = cancellation
    verify(x)
    v = slack_snapshot(x.db, "cancellation", str(x.case.id))
    assert v["event"] == "cancellation_request"
    raw = json.dumps(
        payload(
            v,
            {"dashboard_origin": "https://noc.example.invalid", "mode": "webhook"},
            delivery_id=1,
        )
    )
    for secret in [
        x.body.customer_name,
        x.body.phone,
        x.body.email,
        x.body.service_address,
    ]:
        assert secret not in raw


def test_risk_evidence_is_advisory_not_predicted_probability(cancellation):
    x = cancellation
    verify(x)
    r = s.risk(x.db, x.case)
    assert (
        r["automatic_actions"] is False
        and r["confidence"] == "not_statistically_validated"
    )
    assert r["evidence"][0]["kind"] == "customer_reported_dissatisfaction"


def test_unauthorized_retention_routes(cancellation):
    from app.main import app

    x = cancellation

    def database():
        yield x.db

    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            assert client.get("/api/retention").status_code == 401
            assert client.get("/api/retention", headers=auth(x.tech)).status_code == 403
            assert (
                client.get("/api/retention", headers=auth(x.admin)).status_code == 200
            )
    finally:
        app.dependency_overrides.clear()


def test_migration_ddl_has_no_accidental_sqlalchemy_bind_parameters():
    import ast

    migration = ast.parse(
        (ROOT / "backend/alembic/versions/0034_customer_retention.py").read_text()
    )
    statements = []
    for node in ast.walk(migration):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            statements.append(node.args[0].value)
    assert len(statements) >= 20
    for statement in statements:
        assert not text(statement)._bindparams, (
            "Migration DDL must never demand an invented bind parameter from a JSON literal"
        )


def test_tower_filter_does_not_call_a_transfer_or_other_service_account_churn():
    first = opening(
        [{"service_id": 1, "customer_id": 8}, {"service_id": 2, "customer_id": 8}]
    )
    last = opening(
        [{"service_id": 1, "customer_id": 8}, {"service_id": 2, "customer_id": 8}]
    )
    tower_first = opening([{"service_id": 1, "customer_id": 8}])
    tower_last = opening([])
    result = a.cohort_metrics(tower_first, tower_last, first, last)
    assert (
        result["customer_cancellations"] == 0 and result["cohort_service_losses"] == 0
    )
    assert result["customer_churn_rate"] == 0 and result["service_churn_rate"] == 0


def test_retained_revenue_uses_immutable_event_date_and_basis(cancellation):
    x = cancellation
    test_offer_approval_acceptance_and_retained_no_automatic_change(x)
    x.case.updated_at = s.now() + timedelta(days=45)
    x.db.flush()
    report = a.summary(x.db, date.today().strftime("%Y-%m"))
    retained = report["retention"]
    assert retained["selected_retained_services"] == 1
    assert retained["retained_monthly_snapshot"] == [
        {"currency": "USD", "basis": "contracted_snapshot", "monthly": "65.00"}
    ]
