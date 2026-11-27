"""Disposable PostgreSQL tests; canonical queue, consent and notification fixtures."""

from datetime import datetime, timedelta, timezone
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from test_rf_postgres_integration import postgres_db
from test_powercode_customers import imported, inventory
from test_notifications import network, prepared, approve, NOW, sms_rows
from test_sms_providers import (
    config,
    secrets,
    response,
    twilio_event,
    telnyx_event,
    PHONE,
    SENDER,
    TOKEN,
    BODY,
    TW_ID,
    TEL_ID,
)
from app.core.config import settings
from app.core.security import create_access_token
from app.models.entities import User
from app.notifications.models import (
    NotificationAttempt as Attempt,
    NotificationDelivery as Delivery,
    NotificationPlan as Plan,
    NotificationSuppression as Suppression,
)
from app.notifications.sms_models import (
    SMSProviderConfig as Provider,
    SMSRouting as Routing,
    SMSWebhookEvent as Event,
)
from app.notifications import sms_service as service
from app.notifications.sms_callbacks import apply_callback
from app.notifications.sms_contracts import ProviderInput
from app.notifications.sms_routes import configure, activate, set_credentials
from app.notifications.sms_transport import Submission, SMSFailure
from app.notifications.service import clock, utc
from app.notifications.sms_identity import contact_hash
from app.notifications.secrets import seal
from app.main import app
from app.core.database import get_db


@pytest.fixture
def native(network, tmp_path, monkeypatch):
    x = network
    monkeypatch.setattr(service, "clock", lambda: NOW)
    x.account.phone = PHONE
    x.db.flush()
    p = prepared(x)
    approve(x.db, p.id, x.admin.id, NOW)
    x.plan = p
    x.delivery = sms_rows(x)[0]
    key = tmp_path / "sms-vault.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "notifications_vault_key_file", str(key))
    callback_key = tmp_path / "callback-vault.key"
    callback_key.write_bytes(Fernet.generate_key())
    callback_key.chmod(0o600)
    monkeypatch.setattr(settings, "sms_callback_key_file", str(callback_key))
    monkeypatch.setattr(settings, "sms_native_transport_enabled", True)
    monkeypatch.setattr(settings, "sms_egress_approved", True)
    monkeypatch.setattr(settings, "offline_mode", False)
    monkeypatch.setattr(settings, "sms_native_worker_enabled", True)
    x.signing = Ed25519PrivateKey.generate()
    for name in ("twilio", "telnyx"):
        c = config(name) | {
            "categories": ["outage", "maintenance", "restoration"],
            "recipient_hashes": [contact_hash(PHONE)],
        }
        row = Provider(
            name=name,
            config=c,
            enabled=True,
            live_authorized=True,
            authorization_reference="synthetic-provider-authorization",
            revision=1,
        )
        x.db.add(row)
        x.db.flush()
        private = secrets(name) | (
            {"webhook_auth_token": TOKEN}
            if name == "twilio"
            else {
                "webhook_public_key": base64.b64encode(
                    x.signing.public_key().public_bytes(
                        serialization.Encoding.Raw, serialization.PublicFormat.Raw
                    )
                ).decode()
            }
        )
        service.store_credentials(x.db, name, json.dumps(private), x.admin.id)
        row.live_authorized = True
    x.db.add(
        Routing(
            id=1,
            config={"primary": "twilio", "secondary": "telnyx", "failover": False},
            revision=1,
        )
    )
    x.db.flush()
    service.authorize_delivery(
        x.db, x.delivery.id, x.admin.id, "synthetic-release", p.fingerprint
    )
    x.db.flush()
    x.factory = lambda: Session(
        bind=x.db.connection(), join_transaction_mode="create_savepoint"
    )
    return x


def receipt(name, body, raw="queued"):
    return httpx.MockTransport(
        lambda r: httpx.Response(
            201 if name == "twilio" else 200, json=response(name, raw=raw, body=body)
        )
    )


@pytest.mark.parametrize("name", ["twilio", "telnyx"])
def test_canonical_delivery_submission_preserves_provider_receipt(native, name):
    x = native
    r = x.db.get(Routing, 1)
    r.config = {"primary": name, "secondary": None, "failover": False}
    r.revision += 1
    service.authorize_delivery(
        x.db, x.delivery.id, x.admin.id, "synthetic-review", x.plan.fingerprint
    )
    x.db.flush()
    result = service.run_native_batch(
        x.factory, transports={name: receipt(name, x.plan.message)}, now=NOW, limit=1
    )
    x.db.expire_all()
    d = x.db.get(Delivery, x.delivery.id)
    assert result["processed"] == 1 and d.state == "accepted"
    assert d.provider_receipt == (TW_ID if name == "twilio" else TEL_ID)
    a = x.db.scalar(select(Attempt).where(Attempt.delivery_id == d.id))
    assert a.acceptance == "accepted" and a.destination_hash == contact_hash(PHONE)
    assert (
        service.run_native_batch(x.factory, transports={}, now=NOW, limit=1)[
            "processed"
        ]
        == 0
    )


def test_unknown_timeout_quarantines_without_retry_or_failover(native):
    x = native
    route = x.db.get(Routing, 1)
    route.config = route.config | {"failover": True}
    route.revision += 1
    service.authorize_delivery(
        x.db, x.delivery.id, x.admin.id, "synthetic-review", x.plan.fingerprint
    )
    a_id = service.reserve(x.db, NOW)
    a = x.db.get(Attempt, a_id)
    service.finish(x.db, a_id, failure=SMSFailure("timeout"), now=NOW)
    assert a.acceptance == "unknown" and x.delivery.state == "reconciliation_required"
    with pytest.raises(ValueError):
        service.manual_failover(x.db, x.delivery.id, x.admin.id, "not-permitted")
    assert service.reserve(x.db, NOW + timedelta(minutes=5)) is None


def test_proven_rejection_safe_policy_failover_and_shared_identity(native):
    x = native
    r = x.db.get(Routing, 1)
    r.config = r.config | {"failover": True}
    r.revision += 1
    service.authorize_delivery(
        x.db, x.delivery.id, x.admin.id, "synthetic-review", x.plan.fingerprint
    )
    ident = service.reserve(x.db, NOW)
    service.finish(
        x.db,
        ident,
        failure=SMSFailure("rate_limited", not_accepted=True, retry_after=5),
        now=NOW,
    )
    assert x.delivery.state == "retry" and x.delivery.sms_provider == "telnyx"
    second = service.reserve(x.db, NOW + timedelta(seconds=10))
    assert x.db.get(Attempt, second).delivery_id == x.delivery.id
    assert x.db.get(Attempt, second).provider_name == "telnyx"
    assert x.db.get(Attempt, ident).acceptance == "not_accepted"


def test_unapproved_secondary_is_suppressed_at_claim(native):
    x = native
    ident = service.reserve(x.db, NOW)
    service.finish(
        x.db,
        ident,
        failure=SMSFailure("authentication_failed", not_accepted=True),
        now=NOW,
    )
    x.db.get(Provider, "telnyx").live_authorized = False
    with pytest.raises(ValueError):
        service.manual_failover(x.db, x.delivery.id, x.admin.id, "synthetic-manual")


@pytest.mark.parametrize("provider", ["twilio", "telnyx"])
def test_verified_stop_blocks_both_providers_and_never_start_reconsents(
    native, provider
):
    x = native
    if provider == "twilio":
        body, headers = twilio_event()
    else:
        body, headers = telnyx_event(x.signing, now=NOW)
    assert not apply_callback(x.db, provider, body, headers, now=NOW)["duplicate"]
    x.db.flush()
    x.db.refresh(x.account)
    x.db.refresh(x.delivery)
    assert x.account.sms_opt_out and x.delivery.state == "suppressed"
    assert x.db.get(Suppression, x.account.id).reason == "opt_out"
    assert apply_callback(x.db, provider, body, headers, now=NOW)["duplicate"]
    for name in ("twilio", "telnyx"):
        with pytest.raises(ValueError):
            service.validate_delivery(x.db, x.delivery, name, NOW)
    if provider == "twilio":
        body, headers = twilio_event("START", sid="SM" + "9" * 32)
    else:
        body, headers = telnyx_event(x.signing, text="START", now=NOW)
    apply_callback(x.db, provider, body, headers, now=NOW)
    x.db.flush()
    x.db.refresh(x.account)
    assert x.account.sms_opt_out
    assert (
        x.db.scalar(
            select(func.count()).select_from(Event).where(Event.kind == "start")
        )
        == 1
    )


def test_stop_on_disabled_original_provider_is_still_enforced(native):
    x = native
    x.db.get(Provider, "twilio").enabled = False
    x.db.get(Provider, "twilio").live_authorized = False
    body, headers = twilio_event()
    apply_callback(x.db, "twilio", body, headers, now=NOW)
    x.db.flush()
    x.db.refresh(x.account)
    assert x.account.sms_opt_out


def test_stop_for_unknown_sender_is_retained_without_creating_customer(native):
    x = native
    before = x.db.scalar(
        select(func.count()).select_from(
            __import__(
                "app.customers.models", fromlist=["CustomerAccount"]
            ).CustomerAccount
        )
    )
    x.account.phone = "+15555550129"
    x.db.flush()
    body, h = twilio_event()
    apply_callback(x.db, "twilio", body, h, now=NOW)
    assert x.db.scalar(
        select(Event.key).where(
            Event.contact_hash == contact_hash(PHONE), Event.kind == "stop"
        )
    )
    assert (
        x.db.scalar(
            select(func.count()).select_from(
                __import__(
                    "app.customers.models", fromlist=["CustomerAccount"]
                ).CustomerAccount
            )
        )
        == before
    )


@pytest.mark.parametrize("period", ["daily", "monthly"])
def test_provider_budget_enforced_before_io(native, period):
    x = native
    row = x.db.get(Provider, "twilio")
    row.config = row.config | {period + "_limit_microunits": 1}
    row.revision += 1
    service.authorize_delivery(
        x.db, x.delivery.id, x.admin.id, "synthetic-budget-review", x.plan.fingerprint
    )
    assert service.reserve(x.db, NOW) is None
    assert x.delivery.state == "suppressed" and x.delivery.attempts == 0


def test_ambiguous_pending_cost_is_reserved_and_not_double_counted(native):
    x = native
    ident = service.reserve(x.db, NOW)
    amount = x.db.get(Attempt, ident).estimated_cost_microunits
    assert service.spending(x.db, "twilio", NOW - timedelta(days=1)) == amount
    service.finish(x.db, ident, failure=SMSFailure("timeout"), now=NOW)
    assert service.spending(x.db, "twilio", NOW - timedelta(days=1)) == amount


def test_rejected_attempt_releases_reservation_but_history_survives(native):
    x = native
    ident = service.reserve(x.db, NOW)
    service.finish(
        x.db, ident, failure=SMSFailure("rejected", not_accepted=True), now=NOW
    )
    assert service.spending(x.db, "twilio", NOW - timedelta(days=1)) == 0
    assert x.db.get(Attempt, ident).acceptance == "not_accepted"


def test_worker_restart_quarantines_pending_claim(native):
    x = native
    ident = service.reserve(x.db, NOW)
    assert service.recover_claims(x.db, NOW + timedelta(minutes=3)) == 1
    assert x.db.get(Attempt, ident).acceptance == "unknown"
    assert x.delivery.state == "reconciliation_required"
    assert service.reserve(x.db, NOW + timedelta(minutes=4)) is None


def test_out_of_order_duplicate_callback_does_not_regress_delivery(native):
    x = native
    ident = service.reserve(x.db, NOW)
    service.finish(x.db, ident, result=Submission(TW_ID, "accepted", "queued"), now=NOW)
    for status in ["delivered", "delivered", "queued"]:
        body, h = twilio_event(status=status)
        apply_callback(x.db, "twilio", body, h, now=NOW)
    assert x.delivery.state == "delivered"
    assert (
        x.db.scalar(
            select(func.count()).select_from(Event).where(Event.kind == "status")
        )
        == 2
    )


def test_callback_before_http_receipt_is_retained_and_attached(native):
    x = native
    ident = service.reserve(x.db, NOW)
    body, h = twilio_event(status="delivered")
    apply_callback(x.db, "twilio", body, h, now=NOW)
    assert (
        x.db.scalar(select(Event).where(Event.message_id == TW_ID)).delivery_id is None
    )
    service.finish(x.db, ident, result=Submission(TW_ID, "accepted", "queued"), now=NOW)
    assert x.delivery.state == "delivered"
    assert (
        x.db.scalar(select(Event).where(Event.message_id == TW_ID)).delivery_id
        == x.delivery.id
    )


def test_provider_receipt_cannot_authorize_failover_after_delivery_failure(native):
    x = native
    ident = service.reserve(x.db, NOW)
    service.finish(x.db, ident, result=Submission(TW_ID, "failed", "failed"), now=NOW)
    with pytest.raises(ValueError):
        service.manual_failover(x.db, x.delivery.id, x.admin.id, "not-permitted")


def test_new_routing_revision_invalidates_live_release(native):
    x = native
    x.db.get(Routing, 1).revision += 1
    assert service.reserve(x.db, NOW) is None and x.delivery.state == "suppressed"


def test_admin_api_never_returns_stored_credentials(native):
    x = native

    def db_override():
        yield x.db

    app.dependency_overrides[get_db] = db_override
    token = create_access_token(x.admin.username, user_id=x.admin.id)
    try:
        with TestClient(app) as c:
            r = c.get(
                "/api/notifications/sms", headers={"Authorization": "Bearer " + token}
            )
            assert r.status_code == 200
            for value in [
                TOKEN,
                secrets("twilio")["api_key_secret"],
                secrets("telnyx")["api_key"],
                PHONE,
                SENDER,
            ]:
                assert value not in r.text
    finally:
        app.dependency_overrides.clear()


def test_callback_gateway_signature_validation_with_real_postgres(native, monkeypatch):
    from app.notifications import sms_gateway

    x = native
    monkeypatch.setattr(settings, "sms_callback_gateway_enabled", True)

    def gateway_db():
        yield x.db

    monkeypatch.setattr(sms_gateway, "get_db", gateway_db)
    body, h = twilio_event()
    with TestClient(sms_gateway.app) as c:
        assert (
            c.post("/callbacks/sms/twilio", content=body, headers=h).status_code == 200
        )
        assert (
            c.post("/callbacks/sms/twilio", content=body, headers=h).status_code == 200
        )
        assert (
            c.post(
                "/callbacks/sms/twilio",
                content=body,
                headers=h | {"x-twilio-signature": "invalid"},
            ).status_code
            == 403
        )
        assert c.get("/api/notifications/sms").status_code == 404


def test_unknown_receipt_not_found_does_not_prove_nonacceptance(native):
    x = native
    ident = service.reserve(x.db, NOW)
    service.finish(x.db, ident, failure=SMSFailure("timeout"), now=NOW)
    with pytest.raises(SMSFailure):
        service.reconcile_attempt(
            x.db,
            ident,
            x.admin.id,
            "synthetic-provider-investigation",
            TW_ID,
            transport=httpx.MockTransport(lambda r: httpx.Response(404)),
        )
    assert x.delivery.state == "reconciliation_required"


@pytest.mark.parametrize("name,code", [("twilio", "21610"), ("telnyx", "40300")])
def test_provider_opt_out_error_enforces_shared_suppression(native, name, code):
    x = native
    r = x.db.get(Routing, 1)
    r.config = {
        "primary": name,
        "secondary": "telnyx" if name == "twilio" else "twilio",
        "failover": True,
    }
    r.revision += 1
    service.authorize_delivery(
        x.db, x.delivery.id, x.admin.id, "synthetic-approved-route", x.plan.fingerprint
    )
    ident = service.reserve(x.db, NOW)
    service.finish(
        x.db,
        ident,
        failure=SMSFailure("opt_out", not_accepted=True, provider_code=code),
        now=NOW,
    )
    x.db.flush()
    x.db.refresh(x.account)
    assert x.account.sms_opt_out and x.delivery.state == "suppressed"
    assert x.db.get(Attempt, ident).provider_error_code == code
    with pytest.raises(ValueError):
        service.manual_failover(x.db, x.delivery.id, x.admin.id, "forbidden")


def test_reset_delivery_state_cannot_resend_accepted_attempt(native):
    x = native
    ident = service.reserve(x.db, NOW)
    service.finish(x.db, ident, result=Submission(TW_ID, "accepted", "queued"), now=NOW)
    x.delivery.state = "queued"
    x.db.flush()
    assert service.reserve(x.db, NOW + timedelta(seconds=1)) is None
    assert x.delivery.state == "reconciliation_required"


def test_provider_reported_callback_cost_is_separate_from_estimate(native):
    x = native
    r = x.db.get(Routing, 1)
    r.config = {"primary": "telnyx", "secondary": None, "failover": False}
    r.revision += 1
    service.authorize_delivery(
        x.db, x.delivery.id, x.admin.id, "synthetic-review", x.plan.fingerprint
    )
    ident = service.reserve(x.db, NOW)
    service.finish(
        x.db, ident, result=Submission(TEL_ID, "accepted", "queued"), now=NOW
    )
    body, h = telnyx_event(x.signing, raw="delivered", now=NOW)
    value = json.loads(body)
    value["data"]["payload"]["cost"] = {"amount": "0.003", "currency": "USD"}
    body = json.dumps(value).encode()
    h["telnyx-signature-ed25519"] = base64.b64encode(
        x.signing.sign(h["telnyx-timestamp"].encode() + b"|" + body)
    ).decode()
    apply_callback(x.db, "telnyx", body, h, now=NOW)
    a = x.db.get(Attempt, ident)
    assert a.reported_cost_microunits == 3000 and a.estimated_cost_microunits != 3000


def test_pause_keeps_unclaimed_queue_intact(native):
    from app.notifications.controls import NotificationControl

    x = native
    x.db.add(NotificationControl(id=1, paused=True))
    x.db.flush()
    assert service.reserve(x.db, NOW) is None
    assert x.delivery.state == "queued" and x.delivery.attempts == 0


@pytest.mark.parametrize("mode", ["crash", "concurrent"])
def test_independent_postgres_sessions_claim_durable_before_io(
    postgres_db, tmp_path, monkeypatch, mode
):
    """A separate observer sees committed claims; no outer-savepoint shortcut."""
    import os
    import threading
    from uuid import uuid4
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base
    from app.notifications.service import digest

    url = os.environ["RF_INTEGRATION_TEST_DATABASE_URL"]
    parsed = make_url(url)
    assert (
        parsed.host in {"localhost", "127.0.0.1", "::1"}
        and parsed.database == "nwo_noc"
    )
    schema = "noc126_" + uuid4().hex
    owner = create_engine(url)
    with owner.begin() as c:
        c.exec_driver_sql('CREATE SCHEMA "' + schema + '"')
    engine = create_engine(
        url, execution_options={"schema_translate_map": {None: schema}}
    )
    Factory = sessionmaker(engine, expire_on_commit=False)
    try:
        Base.metadata.create_all(engine)
        callback = tmp_path / "durable-callback.key"
        callback.write_bytes(Fernet.generate_key())
        callback.chmod(0o600)
        monkeypatch.setattr(settings, "sms_callback_key_file", str(callback))
        monkeypatch.setattr(settings, "sms_native_transport_enabled", True)
        monkeypatch.setattr(settings, "sms_egress_approved", True)
        monkeypatch.setattr(settings, "offline_mode", False)
        now = clock()
        with Factory() as db:
            u = User(
                username="synthetic-durable-admin",
                email="durable@example.invalid",
                role="admin",
                is_active=True,
                password_hash="not-used-for-authentication",
            )
            db.add(u)
            db.flush()
            p = Plan(
                dedupe_key=digest(schema),
                kind="maintenance",
                severity="warning",
                confidence="confirmed",
                status="approved",
                target={},
                evidence={},
                snapshot={"request": {}},
                fingerprint="a" * 64,
                created_by=u.id,
                approved_by=u.id,
                due_at=now,
                message=BODY,
                slack_payload={},
            )
            db.add(p)
            db.flush()
            db.add(
                Provider(
                    name="twilio",
                    config=config("twilio"),
                    revision=1,
                    enabled=True,
                    live_authorized=True,
                    authorization_reference="synthetic",
                )
            )
            from app.notifications.models import NotificationPolicy
            from app.notifications.contracts import Policy

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
            db.add(
                Routing(
                    id=1,
                    config={"primary": "twilio", "secondary": None, "failover": False},
                    revision=1,
                )
            )
            d = Delivery(
                plan_id=p.id,
                channel="sms",
                recipient_key="synthetic",
                sms_authorization={"synthetic_test": True},
                next_attempt_at=now,
            )
            db.add(d)
            db.commit()
            delivery_id = d.id

        def validated(db, d, name, at):
            return (
                db.get(Provider, name),
                db.get(Plan, d.plan_id),
                SimpleNamespace(phone=PHONE),
            )

        monkeypatch.setattr(service, "validate_delivery", validated)
        monkeypatch.setattr(service, "credentials", lambda db, name: secrets(name))
        calls = []
        entered = threading.Event()
        release = threading.Event()

        def wire(request):
            calls.append(True)
            with Factory() as observer:
                claim = observer.scalar(
                    select(Attempt).where(Attempt.delivery_id == delivery_id)
                )
                assert claim is not None and claim.acceptance == "pending"
                assert observer.get(Delivery, delivery_id).state == "submitted"
            entered.set()
            if mode == "crash":
                raise SystemExit("synthetic worker terminated after durable claim")
            assert release.wait(10)
            return httpx.Response(201, json=response("twilio"))

        transport = httpx.MockTransport(wire)
        if mode == "crash":
            with pytest.raises(SystemExit):
                service.run_native_batch(
                    Factory, transports={"twilio": transport}, limit=1
                )
            with Factory() as db:
                a = db.scalar(select(Attempt))
                a.lease_expires_at = clock() - timedelta(seconds=1)
                db.commit()
            assert (
                service.run_native_batch(Factory, transports={}, limit=1)["processed"]
                == 0
            )
            with Factory() as db:
                assert db.get(Delivery, delivery_id).state == "reconciliation_required"
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(
                    service.run_native_batch,
                    Factory,
                    transports={"twilio": transport},
                    limit=1,
                )
                assert entered.wait(10)
                second = pool.submit(
                    service.run_native_batch,
                    Factory,
                    transports={"twilio": transport},
                    limit=1,
                )
                assert second.result(timeout=10)["processed"] == 0
                release.set()
                assert first.result(timeout=10)["processed"] == 1
        assert len(calls) == 1
        with Factory() as db:
            assert db.scalar(select(func.count()).select_from(Attempt)) == 1
    finally:
        engine.dispose()
        with owner.begin() as c:
            c.exec_driver_sql('DROP SCHEMA "' + schema + '" CASCADE')
        owner.dispose()


def test_operator_cannot_activate_or_release_native_sms(native):
    x = native
    u = User(
        username="synthetic-sms-operator",
        email="sms-operator@example.invalid",
        password_hash="unused",
        role="operator",
        is_active=True,
    )
    x.db.add(u)
    x.db.flush()

    def overridden_db():
        yield x.db

    app.dependency_overrides[get_db] = overridden_db
    try:
        with TestClient(app) as c:
            token = create_access_token(u.username, user_id=u.id)
            h = {"Authorization": "Bearer " + token}
            assert (
                c.post(
                    "/api/notifications/sms/providers/twilio/authorize",
                    headers=h,
                    json={"confirm": True, "reference": "forbidden"},
                ).status_code
                == 403
            )
            assert (
                c.post(
                    f"/api/notifications/sms/deliveries/{x.delivery.id}/authorize",
                    headers=h,
                    json={
                        "confirm": True,
                        "reference": "forbidden",
                        "fingerprint": x.plan.fingerprint,
                    },
                ).status_code
                == 403
            )
    finally:
        app.dependency_overrides.clear()


def test_restricted_callback_database_role_cannot_restore_consent_or_resend(native):
    from pathlib import Path
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    x = native
    sql = (
        Path(__file__).resolve().parents[2] / "deploy/sms-gateway-role.sql"
    ).read_text()
    # Keep the fixture's outer transaction; the optional DBA script is not an
    # installer action. PostgreSQL role/grant changes roll back with this test.
    sql = sql.replace("BEGIN;\n", "", 1).rsplit("COMMIT;", 1)[0]
    x.db.execute(text(sql))
    x.db.flush()
    x.db.expire_all()
    body, h = twilio_event()
    try:
        x.db.execute(text("SET LOCAL ROLE noc_sms_gateway_access"))
        assert not apply_callback(x.db, "twilio", body, h, now=NOW)["duplicate"]
        x.db.flush()
        for statement in [
            "SELECT * FROM public.users LIMIT 0",
            "SELECT * FROM public.notification_credentials LIMIT 0",
            "SELECT details FROM public.audit_logs LIMIT 0",
            "UPDATE public.customer_accounts SET sms_opt_out=false WHERE id=-1",
            "UPDATE public.notification_attempts SET acceptance='not_accepted' WHERE id=-1",
            "DELETE FROM public.notification_sms_events WHERE key='synthetic-no-entry'",
        ]:
            with pytest.raises(DBAPIError):
                with x.db.begin_nested():
                    x.db.execute(text(statement))
    finally:
        x.db.execute(text("RESET ROLE"))
    x.db.refresh(x.account)
    assert x.account.sms_opt_out


def test_no_acl_restore_bootstrap_restricts_sms_function_again(postgres_db):
    """Model pg_restore --no-privileges resetting the function to PUBLIC execute."""
    import importlib.util
    import sys
    from pathlib import Path
    from sqlalchemy import text

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts/release"))
    spec = importlib.util.spec_from_file_location(
        "sms_runtime_bootstrap", root / "scripts/release/bootstrap_runtime_db.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = postgres_db
    public_execute = text("""
        SELECT EXISTS (SELECT 1 FROM pg_proc p,
          LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
          WHERE p.oid=to_regprocedure('public.noc_sms_enforce_opt_out(text,timestamptz)')
          AND a.grantee=0 AND a.privilege_type='EXECUTE')
    """)
    db.execute(
        text(
            "GRANT EXECUTE ON FUNCTION public.noc_sms_enforce_opt_out(text,timestamptz) TO PUBLIC"
        )
    )
    assert db.scalar(public_execute)
    for _ in range(2):
        db.execute(text(module.runtime_role_sql("a" * 48, owner="nwo_noc")))
        assert not db.scalar(public_execute)
        assert db.scalar(
            text(
                "SELECT has_function_privilege('noc_app','public.noc_sms_enforce_opt_out(text,timestamptz)','EXECUTE')"
            )
        )
