"""Real disposable PostgreSQL email source/permission/outbox acceptance."""

from types import SimpleNamespace
from datetime import timedelta
import json
import pytest
from sqlalchemy import select, func
from cryptography.fernet import Fernet
from app.core.config import settings
from app.notifications import email_service as s
from app.notifications.email_models import *
from app.notifications.email_contracts import *
from app.notifications.models import (
    NotificationDelivery as Delivery,
    NotificationAttempt as Attempt,
    NotificationPlan as Plan,
    NotificationCredential as Credential,
)
from app.notifications.secrets import seal
from app.notifications.email_transport import EmailReceipt, EmailFailure
from test_rf_postgres_integration import postgres_db
from test_powercode_customers import inventory, imported
from test_notifications import network
from test_signup import signup
from test_email_native import cfg, event_fixture, DEST, email_identity_key


@pytest.fixture
def email_case(network, monkeypatch, tmp_path):
    x = network
    key = tmp_path / "email-vault.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "notifications_vault_key_file", str(key))
    monkeypatch.setattr(settings, "offline_mode", True)
    monkeypatch.setattr(settings, "app_env", "staging")
    x.account.email = DEST
    x.db.add(
        EmailProvider(
            name="mock",
            config=cfg(
                enabled=True,
                purposes=[
                    "signup",
                    "operational",
                    "cancellation",
                    "retention",
                    "installation",
                    "maintenance",
                ],
                recipient_hashes=[s.identity(DEST)],
            ).model_dump(),
            revision=1,
        )
    )
    x.db.flush()
    x.provider = x.db.get(EmailProvider, "mock")
    return x


def prepare_network(x):
    from test_notifications import prepared

    p = prepared(x)
    from app.notifications import service as ns

    # Original notification approval remains independent of email.
    ns.approve(x.db, p.id, x.admin.id, expected_fingerprint=p.fingerprint)
    t = EmailTemplate(
        key="network_outage",
        revision=1,
        config=EmailTemplateRequest(
            key="network_outage",
            purpose="operational",
            subject="Network notice {reference}",
            text="We are investigating a service interruption. Status {status}.",
        ).model_dump(),
        created_by=x.admin.id,
    )
    x.db.add(t)
    x.db.flush()
    pref = s.permission(
        x.db,
        EmailPermission(
            customer_id=x.account.id,
            purpose="operational",
            granted=True,
            verified_address=DEST,
            reference="synthetic-identity-purpose-confirmation",
        ),
        x.admin.id,
    )
    body = EmailDraftRequest(
        source_kind="network",
        source_id=p.id,
        customer_id=x.account.id,
        template_id=t.id,
    )
    eplan = s.prepare(x.db, body, x.admin.id)
    d = x.db.scalar(select(Delivery).where(Delivery.plan_id == eplan.id))
    e = x.db.get(EmailEnvelope, d.id)
    return SimpleNamespace(
        source=p, template=t, pref=pref, body=body, plan=eplan, delivery=d, envelope=e
    )


def test_permission_is_independent_email_purpose(email_case):
    x = email_case
    before = (x.account.sms_consent, x.account.sms_opt_out)
    p = s.permission(
        x.db,
        EmailPermission(
            customer_id=x.account.id,
            purpose="cancellation",
            granted=True,
            verified_address=DEST,
            reference="synthetic-verified-purpose",
        ),
        x.admin.id,
    )
    assert (
        p.granted
        and p.customer_id == x.account.id
        and before == (x.account.sms_consent, x.account.sms_opt_out)
    )
    assert x.db.scalar(select(func.count(EmailPreferenceEvent.id))) == 1


def test_unverified_or_changed_address_rejected(email_case):
    x = email_case
    with pytest.raises(ValueError):
        s.permission(
            x.db,
            EmailPermission(
                customer_id=x.account.id,
                purpose="signup",
                granted=True,
                verified_address="other@example.com",
                reference="synthetic-proof",
            ),
            x.admin.id,
        )


def test_shared_suppression_cannot_be_cleared_by_permission(email_case):
    x = email_case
    x.db.add(
        EmailSuppression(
            contact_hash=s.identity(DEST),
            reason="unsubscribe",
            evidence="synthetic-confirmed-event",
        )
    )
    x.db.flush()
    p = s.permission(
        x.db,
        EmailPermission(
            customer_id=x.account.id,
            purpose="operational",
            granted=True,
            verified_address=DEST,
            reference="synthetic-new-permission",
        ),
        x.admin.id,
    )
    assert p.granted and x.db.get(EmailSuppression, s.identity(DEST))


def test_network_draft_deduplication_and_encryption(email_case):
    x = email_case
    q = prepare_network(x)
    again = s.prepare(x.db, q.body, x.admin.id)
    assert again.id == q.plan.id and q.delivery.channel == "email"
    assert (
        DEST not in q.envelope.ciphertext
        and "interruption" not in q.envelope.ciphertext
    )
    assert s.decrypt(q.envelope.ciphertext)["destination"] == DEST
    assert not q.envelope.authorization


def test_email_approval_does_not_authorize_sms_or_slack(email_case):
    x = email_case
    q = prepare_network(x)
    rows = list(x.db.scalars(select(Delivery).where(Delivery.plan_id == q.plan.id)))
    assert len(rows) == 1 and rows[0].channel == "email"
    d = s.approve(
        x.db,
        q.delivery.id,
        EmailApproval(
            confirm=True,
            reference="synthetic-email-approval",
            fingerprint=q.plan.fingerprint,
        ),
        x.admin.id,
        mock=True,
    )
    assert d.state == "queued" and not d.sms_authorization and not d.slack_authorization


def test_email_consent_withdrawal_invalidates_approval(email_case):
    x = email_case
    q = prepare_network(x)
    s.permission(
        x.db,
        EmailPermission(
            customer_id=x.account.id,
            purpose="operational",
            granted=False,
            verified_address=DEST,
            reference="synthetic-withdrawal",
        ),
        x.admin.id,
    )
    with pytest.raises(ValueError):
        s.approve(
            x.db,
            q.delivery.id,
            EmailApproval(
                confirm=True,
                reference="synthetic-email-approval",
                fingerprint=q.plan.fingerprint,
            ),
            x.admin.id,
            mock=True,
        )


def test_email_source_change_invalidates_approval(email_case):
    x = email_case
    q = prepare_network(x)
    x.account.email = "changed@example.com"
    x.db.flush()
    with pytest.raises(ValueError):
        s.approve(
            x.db,
            q.delivery.id,
            EmailApproval(
                confirm=True,
                reference="synthetic-email-approval",
                fingerprint=q.plan.fingerprint,
            ),
            x.admin.id,
            mock=True,
        )


def test_ambiguous_submission_never_reauthorized(email_case):
    x = email_case
    q = prepare_network(x)
    x.db.add(
        Attempt(
            delivery_id=q.delivery.id,
            attempt=1,
            outcome="timeout",
            email_provider="mock",
            acceptance="unknown",
        )
    )
    q.delivery.state = "reconciliation_required"
    x.db.flush()
    with pytest.raises(ValueError):
        s.approve(
            x.db,
            q.delivery.id,
            EmailApproval(
                confirm=True,
                reference="synthetic-email-approval",
                fingerprint=q.plan.fingerprint,
            ),
            x.admin.id,
            mock=True,
        )


def test_smtp_or_sendgrid_provider_switch_does_not_bypass_suppression(email_case):
    x = email_case
    q = prepare_network(x)
    x.db.add(
        EmailSuppression(
            contact_hash=s.identity(DEST),
            reason="bounce",
            evidence="synthetic-provider-evidence",
        )
    )
    x.db.flush()
    with pytest.raises(ValueError):
        s.snapshot(x.db, q.envelope)


def signed(x, q, kind="delivered"):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import base64

    key = ec.generate_private_key(ec.SECP256R1())
    public = base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    provider = EmailProvider(
        name="sendgrid",
        config=cfg(callbacks_enabled=True).model_dump(),
        revision=1,
        webhook_public_key=public,
    )
    x.db.add(provider)
    x.db.flush()
    q.envelope.provider = "sendgrid"
    q.envelope.correlation = "a" * 64
    a = Attempt(
        delivery_id=q.delivery.id,
        attempt=1,
        outcome="intent_committed",
        email_provider="sendgrid",
        acceptance="pending",
    )
    x.db.add(a)
    x.db.flush()

    def make(kind=kind, event="synthetic-event"):
        ts = str(int(s.ns.clock().timestamp()))
        body = json.dumps(
            [
                {
                    "event": kind,
                    "email": DEST,
                    "timestamp": int(ts),
                    "sg_event_id": event,
                    "noc_attempt": "a" * 64,
                    "sg_message_id": "synthetic-message",
                }
            ]
        ).encode()
        headers = {
            "x-twilio-email-event-webhook-timestamp": ts,
            "x-twilio-email-event-webhook-signature": base64.b64encode(
                key.sign(ts.encode() + body, ec.ECDSA(hashes.SHA256()))
            ).decode(),
        }
        return body, headers

    return make


@pytest.mark.parametrize(
    "kind", ["bounce", "spamreport", "unsubscribe", "group_unsubscribe"]
)
def test_signed_suppression_and_duplicate_event(email_case, kind):
    x = email_case
    q = prepare_network(x)
    make = signed(x, q, kind)
    b, h = make()
    assert s.apply_events(x.db, b, h)["processed"] == 1
    assert s.apply_events(x.db, b, h)["processed"] == 0
    assert x.db.get(EmailSuppression, s.identity(DEST))
    assert x.account.sms_opt_out is False
    with pytest.raises(ValueError):
        s.snapshot(x.db, q.envelope)


def test_verified_delivery_does_not_regress_to_processed(email_case):
    x = email_case
    q = prepare_network(x)
    make = signed(x, q)
    b, h = make("delivered", "event1")
    s.apply_events(x.db, b, h)
    b, h = make("processed", "event2")
    s.apply_events(x.db, b, h)
    assert q.delivery.state == "delivered"


def test_unassociated_callback_rejected(email_case):
    x = email_case
    q = prepare_network(x)
    make = signed(x, q)
    b, h = make()
    q.envelope.correlation = "b" * 64
    x.db.flush()
    with pytest.raises(ValueError):
        s.apply_events(x.db, b, h)


def test_private_email_routes_require_administrator(email_case):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.database import get_db
    from app.api.deps import current_user

    x = email_case
    app.dependency_overrides[get_db] = lambda: x.db
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(
        role="operator", is_active=True, id=x.admin.id
    )
    try:
        assert TestClient(app).get("/api/communications").status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("mode", ["crash", "concurrent", "callback"])
def test_independent_committed_email_claims_before_io(
    postgres_db, tmp_path, monkeypatch, mode
):
    """Dedicated random schema; independent observers see committed outbox intents."""
    import os, threading
    from uuid import uuid4
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base
    from app.models.entities import User
    from app.customers.models import CustomerAccount
    from app.notifications.models import NotificationPolicy
    from app.notifications.contracts import Policy
    from app.notifications.email_transport import EmailReceipt

    url = os.environ["RF_INTEGRATION_TEST_DATABASE_URL"]
    parsed = make_url(url)
    assert (
        parsed.host in {"localhost", "127.0.0.1", "::1"}
        and parsed.database == "nwo_noc"
    )
    schema = "noc132_" + uuid4().hex
    owner = create_engine(url)
    with owner.begin() as conn:
        conn.exec_driver_sql('CREATE SCHEMA "' + schema + '"')
    engine = create_engine(
        url, execution_options={"schema_translate_map": {None: schema}}
    )
    Factory = sessionmaker(engine, expire_on_commit=False)
    key = tmp_path / "durable-email.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "notifications_vault_key_file", str(key))
    monkeypatch.setattr(settings, "offline_mode", True)
    monkeypatch.setattr(settings, "app_env", "staging")
    now = s.ns.clock()
    body = {
        "subject": "Synthetic durable fixture",
        "text": "Synthetic only",
        "html": "<p>Synthetic only</p>",
    }
    try:
        Base.metadata.create_all(engine)
        with Factory() as db:
            u = User(
                username="synthetic-email-admin",
                email="admin@example.invalid",
                role="admin",
                is_active=True,
                password_hash="not-used-for-login",
            )
            db.add(u)
            db.flush()
            account = CustomerAccount(
                external_id="synthetic-email",
                email=DEST,
                source_updated_at=now,
                last_synced_at=now,
            )
            db.add(account)
            db.flush()
            pref = EmailPreference(
                subject_key="customer:" + str(account.id),
                customer_id=account.id,
                purpose="operational",
                contact_hash=s.identity(DEST),
                granted=True,
                reference="synthetic",
                verified_by=u.id,
                revision=1,
            )
            db.add(pref)
            t = EmailTemplate(
                key="network_outage",
                revision=1,
                config=EmailTemplateRequest(
                    key="network_outage",
                    purpose="operational",
                    subject="Synthetic network notice",
                    text="Synthetic durable acceptance only.",
                ).model_dump(),
                created_by=u.id,
            )
            db.add(t)
            provider = EmailProvider(
                name="mock",
                config=cfg(
                    enabled=True,
                    purposes=["operational"],
                    recipient_hashes=[s.identity(DEST)],
                ).model_dump(),
                revision=1,
            )
            db.add(provider)
            db.add(
                NotificationPolicy(
                    id=1,
                    config=Policy(
                        timezone="UTC",
                        quiet_start=(now.hour + 12) % 24,
                        quiet_end=(now.hour + 13) % 24,
                    ).model_dump(),
                )
            )
            db.flush()
            p = Plan(
                dedupe_key=s.ns.digest(schema),
                kind="email_operational",
                severity="info",
                confidence="synthetic",
                status="approved",
                target={},
                evidence={},
                snapshot={"email": True},
                fingerprint="a" * 64,
                created_by=u.id,
                approved_by=u.id,
                due_at=now,
                message="encrypted",
                slack_payload={},
            )
            db.add(p)
            db.flush()
            d = Delivery(
                plan_id=p.id,
                channel="email",
                recipient_key=pref.subject_key,
                state="queued",
                next_attempt_at=now,
            )
            db.add(d)
            db.flush()
            db.add(
                EmailEnvelope(
                    delivery_id=d.id,
                    provider="mock",
                    source={},
                    template_id=t.id,
                    preference_id=pref.id,
                    contact_hash=s.identity(DEST),
                    ciphertext=s.encrypt({"destination": DEST, "payload": body}),
                    authorization={"synthetic": True},
                )
            )
            db.commit()
            did = d.id
        # Source/permission validation is exercised above; isolate durable claim behavior here.
        monkeypatch.setattr(
            s,
            "validate",
            lambda db, d, e, **kw: (
                db.get(EmailProvider, "mock"),
                {"message": body},
                DEST,
            ),
        )
        monkeypatch.setattr(s, "validate_pending", lambda *a, **kw: None)
        entered = threading.Event()
        release = threading.Event()
        calls = []

        class Observe:
            def send(self, **kw):
                with Factory() as observer:
                    a = observer.scalar(
                        select(Attempt).where(Attempt.delivery_id == did)
                    )
                    d = observer.get(Delivery, did)
                    assert a and a.acceptance == "pending" and d.state == "submitting"
                calls.append(kw["key"])
                entered.set()
                if mode == "callback":
                    with Factory() as callback:
                        receipt = callback.scalar(
                            select(Attempt).where(Attempt.delivery_id == did)
                        )
                        receipt.acceptance = "accepted"
                        receipt.raw_status = "delivered"
                        callback.get(Delivery, did).state = "delivered"
                        callback.commit()
                    raise EmailFailure("timeout")
                if mode == "crash":
                    raise SystemExit("synthetic worker crash after durable intent")
                assert release.wait(10)
                return EmailReceipt("mock-durable-receipt", simulated=True)

        monkeypatch.setattr(s, "MockEmail", Observe)
        if mode == "crash":
            with pytest.raises(SystemExit):
                s.run_batch(Factory, mock=True, limit=1)
            with Factory() as db:
                a = db.scalar(select(Attempt).where(Attempt.delivery_id == did))
                a.lease_expires_at = now - timedelta(seconds=1)
                db.commit()
            assert s.run_batch(Factory, mock=True, limit=1)["processed"] == 0
            with Factory() as db:
                assert db.get(Delivery, did).state == "reconciliation_required"
        elif mode == "callback":
            assert s.run_batch(Factory, mock=True, limit=1)["processed"] == 1
            with Factory() as db:
                assert db.get(Delivery, did).state == "delivered"
                result = db.scalar(select(Attempt).where(Attempt.delivery_id == did))
                assert (
                    result.acceptance == "accepted"
                    and result.outcome == "signed_event_before_transport_result"
                )
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(s.run_batch, Factory, mock=True, limit=1)
                assert entered.wait(10)
                assert (
                    pool.submit(s.run_batch, Factory, mock=True, limit=1).result(
                        timeout=10
                    )["processed"]
                    == 0
                )
                release.set()
                assert first.result(timeout=10)["processed"] == 1
            with Factory() as db:
                assert db.get(Delivery, did).state == "simulated"
        assert len(calls) == 1
    finally:
        engine.dispose()
        with owner.begin() as conn:
            conn.exec_driver_sql('DROP SCHEMA "' + schema + '" CASCADE')
        owner.dispose()


# Canonical signup/retention fixture graph, no duplicate applicant/customer registry.
from test_field import field, auth
from test_equipment import stock
from test_retention import cancellation, verify as verify_cancellation


def test_signup_email_draft_and_withdrawal(signup, email_case, monkeypatch):
    from test_signup import application
    from app.signup import crypto

    # Both canonical fixtures share the rollback-only database graph.
    x = signup
    x.account.email = DEST
    a = application(x, email=DEST)
    t = EmailTemplate(
        key="signup_received",
        revision=1,
        config=EmailTemplateRequest(
            key="signup_received",
            purpose="signup",
            subject="Application received",
            text="We received application {reference}; status {status}.",
        ).model_dump(),
        created_by=x.admin.id,
    )
    x.db.add(t)
    x.db.flush()
    s.permission(
        x.db,
        EmailPermission(
            application_id=a.id,
            purpose="signup",
            granted=True,
            verified_address=DEST,
            reference="synthetic-applicant-identity",
        ),
        x.admin.id,
    )
    p = s.prepare(
        x.db,
        EmailDraftRequest(
            source_kind="signup", source_id=a.id, application_id=a.id, template_id=t.id
        ),
        x.admin.id,
    )
    d = x.db.scalar(select(Delivery).where(Delivery.plan_id == p.id))
    e = x.db.get(EmailEnvelope, d.id)
    assert s.snapshot(x.db, e)[0]["source"]["kind"] == "signup"
    pii = crypto.unseal(a, a.pii_ciphertext)
    pii["consent_email"] = False
    a.pii_ciphertext = crypto.seal(a, pii)
    x.db.flush()
    with pytest.raises(ValueError):
        s.snapshot(x.db, e)


def test_cancellation_email_requires_verification_and_separate_purpose(
    cancellation, email_case
):
    x = cancellation
    x.account.email = DEST
    t = EmailTemplate(
        key="cancellation_received",
        revision=1,
        config=EmailTemplateRequest(
            key="cancellation_received",
            purpose="cancellation",
            subject="Cancellation request received",
            text="Request {reference} requires operator review.",
        ).model_dump(),
        created_by=x.admin.id,
    )
    x.db.add(t)
    x.db.flush()
    s.permission(
        x.db,
        EmailPermission(
            customer_id=x.account.id,
            purpose="cancellation",
            granted=True,
            verified_address=DEST,
            reference="synthetic-cancellation-email-proof",
        ),
        x.admin.id,
    )
    body = EmailDraftRequest(
        source_kind="cancellation",
        source_id=x.case.id,
        customer_id=x.account.id,
        template_id=t.id,
    )
    with pytest.raises(ValueError):
        s.prepare(x.db, body, x.admin.id)
    verify_cancellation(x)
    p = s.prepare(x.db, body, x.admin.id)
    assert p.kind == "email_cancellation" and x.case.status == "verified"
    assert x.service.status == "active"


def test_retention_email_never_issues_an_unapproved_offer(cancellation, email_case):
    x = cancellation
    x.account.email = DEST
    verify_cancellation(x)
    t = EmailTemplate(
        key="retention_offer",
        revision=1,
        config=EmailTemplateRequest(
            key="retention_offer",
            purpose="retention",
            subject="Retention offer review",
            text="Contact support to review approved terms for {reference}.",
        ).model_dump(),
        created_by=x.admin.id,
    )
    x.db.add(t)
    x.db.flush()
    s.permission(
        x.db,
        EmailPermission(
            customer_id=x.account.id,
            purpose="retention",
            granted=True,
            verified_address=DEST,
            reference="synthetic-retention-email-proof",
        ),
        x.admin.id,
    )
    with pytest.raises(ValueError):
        s.prepare(
            x.db,
            EmailDraftRequest(
                source_kind="cancellation",
                source_id=x.case.id,
                customer_id=x.account.id,
                template_id=t.id,
            ),
            x.admin.id,
        )


def test_restoration_template_cannot_use_outage_source(email_case):
    x = email_case
    q = prepare_network(x)
    t = EmailTemplate(
        key="verified_restoration",
        revision=1,
        config=EmailTemplateRequest(
            key="verified_restoration",
            purpose="operational",
            subject="Recovery verification",
            text="Service recovery evidence reviewed for {reference}.",
        ).model_dump(),
        created_by=x.admin.id,
    )
    x.db.add(t)
    x.db.flush()
    with pytest.raises(ValueError):
        s.prepare(
            x.db,
            EmailDraftRequest(
                source_kind="network",
                source_id=q.source.id,
                customer_id=x.account.id,
                template_id=t.id,
            ),
            x.admin.id,
        )


def test_usage_limit_counts_committed_intent(email_case):
    x = email_case
    q = prepare_network(x)
    x.provider.config = x.provider.config | {"daily_messages": 1}
    x.db.flush()
    s.approve(
        x.db,
        q.delivery.id,
        EmailApproval(
            confirm=True,
            reference="synthetic-budget-review",
            fingerprint=q.plan.fingerprint,
        ),
        x.admin.id,
        mock=True,
    )
    x.db.add(
        Attempt(
            delivery_id=q.delivery.id,
            attempt=1,
            outcome="intent_committed",
            email_provider="mock",
            acceptance="pending",
            estimated_cost_microunits=1,
        )
    )
    x.db.flush()
    with pytest.raises(ValueError, match="usage/budget"):
        s.validate(x.db, q.delivery, q.envelope, mock=True)


def test_recipient_permission_rechecked_after_queue_approval(email_case):
    x = email_case
    q = prepare_network(x)
    s.approve(
        x.db,
        q.delivery.id,
        EmailApproval(
            confirm=True,
            reference="synthetic-queue-consent",
            fingerprint=q.plan.fingerprint,
        ),
        x.admin.id,
        mock=True,
    )
    q.delivery.state = "submitting"
    q.pref.granted = False
    x.db.flush()
    with pytest.raises(ValueError):
        s.validate_pending(x.db, q.delivery.id, True)
    assert (
        x.db.scalar(
            select(func.count(Attempt.id)).where(Attempt.delivery_id == q.delivery.id)
        )
        == 0
    )


def test_quote_email_reuses_only_current_prepared_access(signup, email_case):
    from test_signup import application
    from app.signup.models import Quote
    from app.signup import crypto
    import secrets

    x = signup
    a = application(x, email=DEST)
    a.state = "quote_sent"
    token = secrets.token_urlsafe(48)
    q = Quote(
        application_id=a.id,
        number=1,
        plan_id=x.plan.id,
        snapshot={"monthly": "65.00"},
        expires_at=s.ns.clock() + timedelta(days=1),
        status="issued",
        approved_by=x.admin.id,
        issued_at=s.ns.clock(),
        access_hash=crypto.digest("quote_access", token),
        access_ciphertext=crypto.seal(a, {"token": token}),
    )
    x.db.add(q)
    t = EmailTemplate(
        key="quote_available",
        revision=1,
        config=EmailTemplateRequest(
            key="quote_available",
            purpose="signup",
            subject="Quote available",
            text="Enter this private token in the existing secure quote form: {quote_token}",
        ).model_dump(),
        created_by=x.admin.id,
    )
    x.db.add(t)
    x.db.flush()
    s.permission(
        x.db,
        EmailPermission(
            application_id=a.id,
            purpose="signup",
            granted=True,
            verified_address=DEST,
            reference="synthetic-quote-recipient",
        ),
        x.admin.id,
    )
    p = s.prepare(
        x.db,
        EmailDraftRequest(
            source_kind="signup", source_id=a.id, application_id=a.id, template_id=t.id
        ),
        x.admin.id,
    )
    d = x.db.scalar(select(Delivery).where(Delivery.plan_id == p.id))
    e = x.db.get(EmailEnvelope, d.id)
    assert (
        token not in json.dumps(p.snapshot)
        and token not in p.message
        and token not in json.dumps(p.target)
    )
    payload = s.decrypt(e.ciphertext)["payload"]
    assert token in payload["text"] and token not in payload["subject"]
    q.access_hash = crypto.digest("quote_access", "rotated")
    x.db.flush()
    with pytest.raises(ValueError):
        s.snapshot(x.db, e)


def test_plan_cancellation_rechecked_immediately_before_io(email_case):
    x = email_case
    q = prepare_network(x)
    s.approve(
        x.db,
        q.delivery.id,
        EmailApproval(
            confirm=True,
            reference="synthetic-last-moment-cancel",
            fingerprint=q.plan.fingerprint,
        ),
        x.admin.id,
        mock=True,
    )
    q.delivery.state = "submitting"
    x.db.flush()
    s.validate_pending(x.db, q.delivery.id, True)
    # Existing canonical cancellation can happen after the committed claim.
    q.plan.status = "cancelled"
    x.db.flush()
    with pytest.raises(ValueError, match="revoked before I/O"):
        s.validate_pending(x.db, q.delivery.id, True)
    assert (
        x.db.scalar(
            select(func.count(Attempt.id)).where(Attempt.delivery_id == q.delivery.id)
        )
        == 0
    )


def test_private_rates_and_immutable_event_history(email_case):
    from app.notifications.email_routes import analytics, attempts

    x = email_case
    q = prepare_network(x)
    make = signed(x, q)
    b, h = make()
    s.apply_events(x.db, b, h)
    x.db.flush()
    result = analytics(db=x.db)
    metric = next(m for m in result["metrics"] if m["provider"] == "sendgrid")
    assert (
        metric["confirmed_delivery_rate"] == 1
        and metric["resolved_submission_success_rate"] == 1
    )
    assert attempts(q.delivery.id, db=x.db)["events"][0]["kind"] == "delivered"


def test_long_smtp_receipt_preserved_without_truncation(email_case):
    from app.notifications.email_transport import message_id

    x = email_case
    q = prepare_network(x)
    identifier = message_id("synthetic-long-domain", "a" * 40 + ".example.com")
    q.delivery.provider_receipt = identifier
    attempt = Attempt(
        delivery_id=q.delivery.id,
        attempt=1,
        email_provider="mock",
        outcome="synthetic-long-receipt",
        acceptance="accepted",
        provider_message_id=identifier,
    )
    x.db.add(attempt)
    x.db.flush()
    x.db.expire_all()
    assert x.db.get(Delivery, q.delivery.id).provider_receipt == identifier
    assert x.db.get(Attempt, attempt.id).provider_message_id == identifier


def test_expired_email_cleanup_honors_canonical_legal_hold(signup, email_case):
    from test_signup import application
    from app.notifications.email_routes import redact

    x = signup
    a = application(x, email=DEST)
    a.legal_hold = True
    t = EmailTemplate(
        key="signup_received",
        revision=1,
        config=EmailTemplateRequest(
            key="signup_received",
            purpose="signup",
            subject="Application received",
            text="Application {reference} was received for review.",
        ).model_dump(),
        created_by=x.admin.id,
    )
    x.db.add(t)
    x.db.flush()
    s.permission(
        x.db,
        EmailPermission(
            application_id=a.id,
            purpose="signup",
            granted=True,
            verified_address=DEST,
            reference="synthetic-held-applicant",
        ),
        x.admin.id,
    )
    p = s.prepare(
        x.db,
        EmailDraftRequest(
            source_kind="signup", source_id=a.id, application_id=a.id, template_id=t.id
        ),
        x.admin.id,
    )
    d = x.db.scalar(select(Delivery).where(Delivery.plan_id == p.id))
    e = x.db.get(EmailEnvelope, d.id)
    original = e.ciphertext
    p.created_at = s.ns.clock() - timedelta(days=366)
    d.state = "simulated"
    x.db.flush()
    assert redact(db=x.db, u=x.admin)["redacted"] == 0
    assert e.ciphertext == original
    a.legal_hold = False
    x.db.flush()
    assert redact(db=x.db, u=x.admin)["redacted"] == 1
    assert s.decrypt(e.ciphertext) == {"redacted": True}
