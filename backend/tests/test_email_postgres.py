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
from test_email_native import cfg, event_fixture, DEST


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
