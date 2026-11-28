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
def cancellation(signup, monkeypatch, tmp_path, request):
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
    b.update(getattr(request, "param", {}))
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


def allow_sms(x):
    return s.purpose_consent(
        x.db,
        x.admin,
        x.case.id,
        PurposeConsent(
            expected_revision=x.case.revision,
            notes="Synthetic explicit permission for this cancellation request",
            granted=True,
            phone=x.account.phone,
            evidence_reference="SYN-PURPOSE-CONSENT",
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
    allow_sms(x)
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


def test_canonical_tower_and_retention_finance_incomplete_is_not_profit(cancellation):
    from app.finance.models import Period, Scope
    from app.retention.routes import tower, retention_profitability

    x = cancellation
    verify(x)
    start = date.today().replace(day=1)
    end = a.next_month(start)
    period = Period(
        code="SYN131-PERIOD",
        starts_on=start,
        ends_on=end,
        currency="USD",
        created_by=x.admin.id,
    )
    scope = Scope(
        code="SYN131-TOWER",
        name="Synthetic tower finance",
        kind="tower",
        site_id=x.a.id,
        context={},
        created_by=x.admin.id,
    )
    service_scope = Scope(
        code="SYN131-SERVICE",
        name="Synthetic service finance",
        kind="service",
        service_id=x.service.id,
        context={},
        created_by=x.admin.id,
    )
    x.db.add_all([period, scope, service_scope])
    x.db.flush()
    r = tower(x.a.id, start.strftime("%Y-%m"), period.id, scope.id, x.db, x.admin)
    assert (
        r["profitability"]["complete"] is False
        and r["profitability"]["contribution"] is None
    )
    profit = retention_profitability(
        x.case.id, period.id, service_scope.id, x.db, x.admin
    )
    assert (
        profit["estimated_retention_profitability"] is None
        and profit["automatic_financial_changes"] is False
    )


@pytest.mark.parametrize(
    "cancellation",
    [{"replacement": {"provider": "Starlink", "monthly": "50.00", "currency": "USD"}}],
    indirect=True,
)
def test_reported_competitor_price_is_not_independent_verification(cancellation):
    x = cancellation
    verify(x)
    row = x.db.scalar(select(Observation).where(Observation.case_id == x.case.id))
    assert row.source_kind == "customer_reported" and row.verified is False
    assert row.offer["monthly"] == "50.00" and x.case.competitor_id == row.competitor_id


def test_cancellation_sms_exact_phase_dedup_and_no_retained_completion(cancellation):
    x = cancellation
    verify(x)
    allow_sms(x)
    x.account.sms_consent = "granted"
    x.account.communication_preference = "sms"
    x.account.sms_opt_out = False
    x.db.flush()
    first = prepare(
        x.db,
        x.admin,
        x.case.id,
        Communication(
            expected_revision=x.case.revision, notes=NOTE, template="processing_update"
        ),
    )
    second = prepare(
        x.db,
        x.admin,
        x.case.id,
        Communication(
            expected_revision=x.case.revision, notes=NOTE, template="processing_update"
        ),
    )
    assert first["id"] == second["id"]
    assert (
        x.db.scalar(
            select(func.count())
            .select_from(NotificationRecipient)
            .where(NotificationRecipient.plan_id == first["id"])
        )
        == 1
    )
    x.case.status = "closed"
    x.db.flush()
    with pytest.raises(ValueError, match="Observed disconnection"):
        prepare(
            x.db,
            x.admin,
            x.case.id,
            Communication(
                expected_revision=x.case.revision, notes=NOTE, template="completion"
            ),
        )


def test_original_encrypted_request_cannot_be_rewritten(cancellation):
    x = cancellation
    with pytest.raises(DBAPIError), x.db.begin_nested():
        x.db.execute(
            text("UPDATE retention_cases SET pii_ciphertext='changed' WHERE id=:id"),
            {"id": x.case.id},
        )
    with pytest.raises(ValueError, match="expired"):
        s.redact(
            x.db,
            x.admin,
            x.case.id,
            Change(expected_revision=x.case.revision, notes=NOTE),
        )


def test_generic_status_cannot_bypass_identity_verification(cancellation):
    x = cancellation
    with pytest.raises(ValueError, match="identity"):
        s.change(
            x.db,
            x.admin,
            x.case.id,
            StatusChange(
                expected_revision=x.case.revision, notes=NOTE, status="verified"
            ),
        )
    assert x.case.status == "pending_verification" and x.case.customer_id is None


def test_unrelated_review_cannot_clear_legal_hold(cancellation):
    x = cancellation
    s.review(
        x.db,
        x.admin,
        x.case.id,
        Review(expected_revision=x.case.revision, notes=NOTE, legal_hold=True),
    )
    s.review(
        x.db,
        x.admin,
        x.case.id,
        Review(
            expected_revision=x.case.revision,
            notes="Additional documented review notes",
        ),
    )
    assert x.case.legal_hold is True


def test_cancellation_approval_enqueues_only_mock_safe_sms(cancellation):
    from app.notifications.service import approve
    from app.notifications.models import NotificationDelivery
    from app.notifications.worker import run_batch
    from app.notifications.providers import MockSender

    x = cancellation
    verify(x)
    allow_sms(x)
    p = prepare(
        x.db,
        x.admin,
        x.case.id,
        Communication(
            expected_revision=x.case.revision, notes=NOTE, template="processing_update"
        ),
    )
    approve(x.db, p["id"], x.admin.id, expected_fingerprint=p["fingerprint"])
    deliveries = list(
        x.db.scalars(
            select(NotificationDelivery).where(NotificationDelivery.plan_id == p["id"])
        )
    )
    assert len(deliveries) == 2 and {d.channel for d in deliveries} == {
        "sms",
        "internal",
    }
    sms_delivery = next(d for d in deliveries if d.channel == "sms")
    sms = MockSender()
    run_batch(x.db, sms=sms, slack=MockSender(), now=s.now())
    assert sms_delivery.state == "simulated"
    assert len(sms.calls) == 1
    run_batch(x.db, sms=sms, slack=MockSender(), now=s.now() + timedelta(seconds=1))
    assert len(sms.calls) == 1


@pytest.mark.parametrize("provider_name", ["twilio", "telnyx"])
def test_cancellation_content_native_transports_are_mocked(cancellation, provider_name):
    import httpx
    from test_sms_providers import config, secrets, response, PHONE
    from app.notifications.sms_transport import NativeSMS
    from app.notifications.sms_contracts import ProviderInput

    x = cancellation
    verify(x)
    x.account.phone = PHONE
    x.db.flush()
    allow_sms(x)
    p = prepare(
        x.db,
        x.admin,
        x.case.id,
        Communication(
            expected_revision=x.case.revision, notes=NOTE, template="processing_update"
        ),
    )
    plan = x.db.get(NotificationPlan, p["id"])
    cfg = config(provider_name)
    assert "customer_cancellation" not in ProviderInput(**cfg).categories
    cfg["categories"].append("customer_cancellation")
    assert "customer_cancellation" in ProviderInput(**cfg).categories
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(
            201 if provider_name == "twilio" else 200,
            json=response(provider_name, body=plan.message),
        )

    provider = NativeSMS(
        provider_name,
        cfg,
        secrets(provider_name),
        transport=httpx.MockTransport(transport),
    )
    receipt = provider.submit(
        destination=PHONE,
        body=plan.message,
        key="synthetic-cancellation-" + str(plan.id),
    )
    assert receipt.state == "accepted" and len(calls) == 1


def test_historical_report_dates_are_separate_from_verified_churn(cancellation):
    x = cancellation
    day = date.today().replace(day=1).isoformat()
    csv = f"id,date,end,reason\nSYN131-REPORTED,{day},{day},moving\n"
    cols = {
        "external_id": "id",
        "requested_date": "date",
        "disconnected_date": "end",
        "reason": "reason",
    }
    b = CSVInput(csv_text=csv, columns=cols)
    preview = s.historical(x.db, x.admin, b)
    s.historical(
        x.db,
        x.admin,
        CSVInput(
            csv_text=csv, columns=cols, dry_run=False, expected_digest=preview["digest"]
        ),
    )
    report = a.summary(x.db, date.today().strftime("%Y-%m"))
    assert report["historical_reports"]["reported_record_count"] == 1
    assert report["historical_reports"]["identity_reviewed_records"] == 0
    assert report["historical_reports"]["additive_to_verified_cancellations"] is False
    assert (
        report["observed_unique_service_cancellations"] == 0
        and report["customer_churn_rate"] is None
    )


def test_csv_missing_cells_are_rejected_atomically(cancellation):
    x = cancellation
    before = x.db.scalar(select(func.count()).select_from(Case))
    body = CSVInput(
        csv_text="id,date,reason\nSYN131-MISSING,2020-01-01\n",
        columns={"external_id": "id", "requested_date": "date", "reason": "reason"},
        dry_run=False,
        expected_digest="a" * 64,
    )
    with pytest.raises(ValueError, match="CSV validation"):
        s.historical(x.db, x.admin, body)
    assert x.db.scalar(select(func.count()).select_from(Case)) == before


def test_multi_tower_retention_offer_cost_is_not_counted_in_full_at_each_tower(
    cancellation,
):
    x = cancellation
    second = CustomerService(
        external_id="SYN131-SECOND-TOWER",
        customer_id=x.account.id,
        status="active",
        service_type="wireless",
        site_id=x.b.id,
        monthly_recurring=Decimal("30.00"),
        currency="USD",
        revenue_basis="contracted",
        source_updated_at=s.now(),
        last_synced_at=s.now(),
    )
    x.db.add(second)
    x.db.flush()
    s.verify(
        x.db,
        x.admin,
        x.case.id,
        Verification(
            expected_revision=x.case.revision,
            notes=NOTE,
            customer_id=x.account.id,
            service_ids=[x.service.id, second.id],
            identity_verified=True,
            address_verified=True,
            evidence_reference="SYN-MULTITOWER",
        ),
    )
    o = s.offer(
        x.db,
        x.admin,
        x.case.id,
        OfferInput(
            expected_revision=x.case.revision,
            notes=NOTE,
            kind="technical_investigation",
            terms=NOTE,
            expires_at=s.now() + timedelta(days=2),
            eligible=True,
            evidence_reference="SYN-OFFER",
            monthly_cost="5.00",
            one_time_cost="20.00",
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
                evidence_reference="SYN-ACCEPTED",
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
    lo = s.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hi = lo + timedelta(days=32)
    total = a.retained(x.db, lo, hi)
    assert total["estimated_offer_costs"] == {
        "USD": {"monthly": "5.00", "one_time": "20.00"}
    }
    for site in [x.a.id, x.b.id]:
        result = a.retained(x.db, lo, hi, site)
        assert result["estimated_offer_costs"] == {} and result[
            "unallocated_offer_case_ids"
        ] == [x.case.id]
        assert result["offer_cost_data_complete"] is False


def test_expired_approved_offer_can_be_replaced_without_rewriting_history(
    cancellation, monkeypatch
):
    x = cancellation
    verify(x)
    clock = s.now()

    def draft(expires):
        return s.offer(
            x.db,
            x.admin,
            x.case.id,
            OfferInput(
                expected_revision=x.case.revision,
                notes=NOTE,
                kind="technical_investigation",
                terms=NOTE,
                expires_at=expires,
                eligible=True,
                evidence_reference="SYN-EXPIRY",
            ),
        )

    def approve(o):
        return s.offer_decide(
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

    old = draft(clock + timedelta(days=1))
    approve(old)
    monkeypatch.setattr(s, "now", lambda: clock + timedelta(days=2))
    new = draft(clock + timedelta(days=3))
    approve(new)
    old_record = x.db.get(Offer, old["id"])
    assert (
        old_record.status == "approved"
        and s.offer_view(x.db, x.case, old_record)["expired"] is True
    )
    assert x.db.get(Offer, new["id"]).status == "approved"


def test_public_sms_unchecked_does_not_inherit_account_notification_consent(
    cancellation,
):
    x = cancellation
    verify(x)
    assert x.account.sms_consent == "granted" and x.body.consent_sms is False
    with pytest.raises(ValueError, match="request-scoped"):
        prepare(
            x.db,
            x.admin,
            x.case.id,
            Communication(
                expected_revision=x.case.revision,
                notes=NOTE,
                template="processing_update",
            ),
        )
    assert not x.db.scalar(
        select(NotificationPlan.id).where(
            NotificationPlan.kind == "customer_cancellation"
        )
    )
    s.change(
        x.db,
        x.admin,
        x.case.id,
        StatusChange(
            expected_revision=x.case.revision,
            notes=NOTE,
            status="cancellation_confirmed",
        ),
    )
    assert x.case.status == "cancellation_confirmed" and x.service.status == "active"


def test_case_permission_withdrawal_invalidates_both_provider_source_contracts(
    cancellation,
):
    x = cancellation
    verify(x)
    allow_sms(x)
    p = prepare(
        x.db,
        x.admin,
        x.case.id,
        Communication(
            expected_revision=x.case.revision, notes=NOTE, template="processing_update"
        ),
    )
    s.purpose_consent(
        x.db,
        x.admin,
        x.case.id,
        PurposeConsent(
            expected_revision=x.case.revision,
            notes="Synthetic verified permission withdrawal",
            granted=False,
            evidence_reference="SYN-WITHDRAWN",
        ),
    )
    with pytest.raises(ValueError):
        snapshot(x.db, x.db.get(NotificationPlan, p["id"]))
    assert x.account.sms_consent == "granted" and x.account.sms_opt_out is False
