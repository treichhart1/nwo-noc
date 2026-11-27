"""Real disposable PostgreSQL acceptance; mocked Slack HTTP only."""

from datetime import timedelta
import hashlib
import hmac
import json
from urllib.parse import urlencode
import httpx
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from cryptography.fernet import Fernet
from test_rf_postgres_integration import postgres_db
from test_powercode_customers import imported, inventory
from test_notifications import network, NOW
from test_slack_native import config, TEAM, APP, USER, CHANNEL, TS, identity
from app.core.config import settings
from app.notifications import slack_service as service, slack_sources
from app.notifications.slack_models import (
    SlackIntegration,
    SlackInteraction,
    SlackUserMap,
    SlackSource,
)
from app.notifications.models import (
    NotificationDelivery as Delivery,
    NotificationAttempt as Attempt,
    NotificationCredential as Credential,
)
from app.notifications.slack_gateway import record
from app.notifications.slack_transport import SlackFailure
from app.notifications.controls import NotificationControl
from app.notifications.sms_service import recover_claims
from app.models.entities import IncidentNote


@pytest.fixture
def native(network, tmp_path, monkeypatch):
    x = network
    for name in ["notifications_vault_key_file", "slack_callback_key_file"]:
        key = tmp_path / name
        key.write_bytes(Fernet.generate_key())
        key.chmod(0o600)
        monkeypatch.setattr(settings, name, str(key))
    monkeypatch.setattr(settings, "offline_mode", False)
    monkeypatch.setattr(settings, "slack_native_transport_enabled", True)
    monkeypatch.setattr(settings, "slack_egress_approved", True)
    monkeypatch.setattr(settings, "rf_scheduler_spool_dir", str(tmp_path / "spool"))
    monkeypatch.setattr(service, "clock", lambda: NOW)
    monkeypatch.setattr(slack_sources, "clock", lambda: NOW)
    x.signing = "a" * 32
    x.config = config() | {"enabled": True, "interactions_enabled": True}
    x.row = SlackIntegration(id=1, config=x.config, revision=1, live_authorized=False)
    x.db.add(x.row)
    x.db.flush()
    service.store_credentials(
        x.db,
        json.dumps(
            {"bot_token": "xoxb-synthetic-not-live", "signing_secret": x.signing}
        ),
        x.admin.id,
    )
    x.row.live_authorized = True
    x.row.authorization_reference = "synthetic-authorized"
    x.plan = service.prepare(x.db, "incident", str(x.incident.id), x.admin.id)
    x.delivery = service.approve(
        x.db, x.plan.id, x.admin.id, "synthetic-event", x.plan.fingerprint
    )
    x.db.commit()
    x.factory = lambda: Session(
        bind=x.db.connection(), join_transaction_mode="create_savepoint"
    )
    return x


def accept(req):
    if req.url.path.endswith("auth.test"):
        return identity(req)
    return httpx.Response(
        200,
        json={
            "ok": True,
            "channel": CHANNEL,
            "ts": TS,
            "message": {"user": USER, "app_id": APP},
        },
    )


def fresh(x):
    x.db.expire_all()
    return x.db.get(Delivery, x.delivery.id)


def test_canonical_queue_acceptance_and_sms_isolation(native):
    x = native
    assert (
        service.run_batch(
            x.factory, transport=httpx.MockTransport(accept), now=NOW, limit=1
        )["processed"]
        == 1
    )
    d = fresh(x)
    assert d.state == "accepted" and d.provider_receipt == TS
    a = x.db.scalar(select(Attempt).where(Attempt.delivery_id == d.id))
    assert (
        a.provider_name is None
        and a.slack_workspace_id == TEAM
        and a.acceptance == "accepted"
    )
    assert (
        service.run_batch(
            x.factory,
            transport=httpx.MockTransport(lambda _: pytest.fail("duplicate send")),
            now=NOW + timedelta(minutes=10),
        )["processed"]
        == 0
    )


def test_claim_committed_before_io(native):
    x = native

    def handle(req):
        with x.factory() as observer:
            a = observer.scalar(
                select(Attempt).where(Attempt.delivery_id == x.delivery.id)
            )
            assert a.acceptance == "pending" and a.claim_token and a.lease_expires_at
        return accept(req)

    service.run_batch(
        x.factory, transport=httpx.MockTransport(handle), now=NOW, limit=1
    )
    assert fresh(x).state == "accepted"


@pytest.mark.parametrize("mode", ["timeout", "invalid", "fatal"])
def test_ambiguous_submission_quarantined_never_retried(native, mode):
    x = native

    def handle(req):
        if req.url.path.endswith("auth.test"):
            return identity(req)
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic")
        return httpx.Response(
            200,
            json={"ok": False, "error": "fatal_error"}
            if mode == "fatal"
            else {"ok": True},
        )

    service.run_batch(
        x.factory, transport=httpx.MockTransport(handle), now=NOW, limit=1
    )
    assert fresh(x).state == "reconciliation_required"
    assert (
        service.run_batch(
            x.factory,
            transport=httpx.MockTransport(lambda _: pytest.fail("unsafe retry")),
            now=NOW + timedelta(hours=1),
        )["processed"]
        == 0
    )


def test_rate_limit_retry_after(native):
    x = native

    def handle(req):
        return (
            identity(req)
            if req.url.path.endswith("auth.test")
            else httpx.Response(429, headers={"Retry-After": "90"})
        )

    service.run_batch(
        x.factory, transport=httpx.MockTransport(handle), now=NOW, limit=1
    )
    d = fresh(x)
    assert d.state == "retry" and service.utc(d.next_attempt_at) == NOW + timedelta(
        seconds=90
    )
    assert (
        service.run_batch(
            x.factory,
            transport=httpx.MockTransport(lambda _: pytest.fail("early retry")),
            now=NOW + timedelta(seconds=30),
        )["processed"]
        == 0
    )


def test_invalid_credentials_dead_letter(native):
    x = native
    service.run_batch(
        x.factory,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        ),
        now=NOW,
        limit=1,
    )
    assert fresh(x).state == "dead_letter"


def test_restart_claim_quarantine_not_recovered_by_sms(native):
    x = native
    a_id = service.reserve(x.db, NOW)
    x.db.commit()
    assert a_id
    recover_claims(x.db, NOW + timedelta(minutes=3))
    x.db.flush()
    assert x.db.get(Attempt, a_id).acceptance == "pending"
    service.recover(x.db, NOW + timedelta(minutes=3))
    x.db.commit()
    assert fresh(x).state == "reconciliation_required"


def test_deduplication_and_changed_source(native):
    x = native
    assert (
        service.prepare(x.db, "incident", str(x.incident.id), x.admin.id).id
        == x.plan.id
    )
    x.incident.revision += 1
    x.db.commit()
    service.run_batch(
        x.factory,
        transport=httpx.MockTransport(lambda _: pytest.fail("stale source")),
        now=NOW,
    )
    assert fresh(x).state == "suppressed"


@pytest.mark.parametrize("control", ["provider", "paused", "emergency"])
def test_emergency_and_authorization_fail_closed(native, control):
    x = native
    if control == "provider":
        x.row.live_authorized = False
    elif control == "paused":
        x.db.add(NotificationControl(id=1, paused=True))
    else:
        from app.notifications.controls import emergency_disable

        emergency_disable()
    x.db.commit()
    service.run_batch(
        x.factory,
        transport=httpx.MockTransport(lambda _: pytest.fail("disabled send")),
        now=NOW,
    )
    assert fresh(x).state in {"queued", "suppressed"}


def test_credentials_encrypted_and_rotation_revokes(native):
    x = native
    assert "xoxb-" not in x.db.get(Credential, "native_slack").ciphertext
    service.store_credentials(
        x.db,
        json.dumps({"bot_token": "xoxb-other-synthetic", "signing_secret": "b" * 32}),
        x.admin.id,
    )
    assert not x.row.live_authorized


def test_pending_rotation_denied(native):
    x = native
    service.reserve(x.db, NOW)
    x.db.commit()
    with pytest.raises(ValueError):
        service.store_credentials(x.db, "{}", x.admin.id)


def test_thread_updates_reuse_root(native):
    x = native
    service.run_batch(
        x.factory, transport=httpx.MockTransport(accept), now=NOW, limit=1
    )
    fresh(x)
    x.incident = x.db.get(type(x.incident), x.incident.id)
    x.incident.revision += 1
    x.incident.status = "closed"
    x.db.flush()
    p = service.prepare(x.db, "incident", str(x.incident.id), x.admin.id)
    d = service.approve(x.db, p.id, x.admin.id, "synthetic-close", p.fingerprint)
    x.db.commit()

    def reply(req):
        if req.url.path.endswith("auth.test"):
            return identity(req)
        assert json.loads(req.content)["thread_ts"] == TS
        return httpx.Response(
            200,
            json={
                "ok": True,
                "channel": CHANNEL,
                "ts": "1795737601.000002",
                "message": {"user": USER, "app_id": APP},
            },
        )

    service.run_batch(
        x.factory,
        transport=httpx.MockTransport(reply),
        now=NOW + timedelta(seconds=2),
        limit=1,
    )
    x.db.expire_all()
    assert x.db.get(Delivery, d.id).provider_receipt == "1795737601.000002"


def action_body(x, **changes):
    event = {
        "type": "block_actions",
        "team": {"id": TEAM},
        "api_app_id": APP,
        "user": {"id": USER},
        "channel": {"id": CHANNEL},
        "container": {"message_ts": TS},
        "actions": [
            {"action_id": "noc_ack", "value": str(x.delivery.id), "action_ts": TS}
        ],
    }
    event.update(changes)
    body = urlencode({"payload": json.dumps(event)}).encode()
    timestamp = str(int(NOW.timestamp()))
    return body, {
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": "v0="
        + hmac.new(
            x.signing.encode(),
            b"v0:" + timestamp.encode() + b":" + body,
            hashlib.sha256,
        ).hexdigest(),
    }


@pytest.fixture
def interactive(native, monkeypatch):
    x = native
    service.run_batch(
        x.factory, transport=httpx.MockTransport(accept), now=NOW, limit=1
    )
    fresh(x)
    x.db.add(
        SlackUserMap(
            slack_id=USER,
            workspace=TEAM,
            user_id=x.admin.id,
            channels=[CHANNEL],
            active=True,
        )
    )
    x.db.commit()
    import app.notifications.slack_gateway as gateway

    monkeypatch.setattr(gateway, "clock", lambda: NOW)
    return x


def test_signed_interaction_replay_and_private_worker(interactive):
    x = interactive
    body, headers = action_body(x)
    assert not record(x.db, body, headers)["duplicate"]
    x.db.commit()
    assert record(x.db, body, headers)["duplicate"]
    x.db.commit()
    assert x.db.scalar(select(func.count()).select_from(SlackInteraction)) == 1
    assert service.process_interactions(x.factory)["processed"] == 1
    assert service.process_interactions(x.factory)["processed"] == 0
    x.db.expire_all()
    assert x.db.get(type(x.incident), x.incident.id).status == "acknowledged"
    assert x.db.scalar(select(func.count()).select_from(IncidentNote)) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"team": {"id": "T999999999"}},
        {"user": {"id": "U999999999"}},
        {"channel": {"id": "C999999999"}},
        {"api_app_id": "A999999999"},
        {"container": {"message_ts": "1795737602.000003"}},
    ],
)
def test_interaction_identity_denied(interactive, change):
    body, headers = action_body(interactive, **change)
    with pytest.raises(ValueError):
        record(interactive.db, body, headers)


def test_modified_or_old_signature_denied(interactive):
    x = interactive
    body, headers = action_body(x)
    with pytest.raises(ValueError):
        record(x.db, body + b"altered", headers)
    headers["x-slack-request-timestamp"] = str(
        int((NOW - timedelta(minutes=6)).timestamp())
    )
    with pytest.raises(ValueError):
        record(x.db, body, headers)


def test_schema_has_no_duplicate_outbox(native):
    x = native
    assert (
        x.db.scalar(
            text("SELECT count(*) FROM pg_indexes WHERE indexname='uq_slack_receipt'")
        )
        == 1
    )
    assert not x.db.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM pg_proc p, LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE p.oid='public.noc_slack_interaction_allowed(text,text,text,integer)'::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE')"
        )
    )


def test_gateway_db_role_cannot_read_private_data_or_send(interactive):
    from pathlib import Path
    from sqlalchemy.exc import DBAPIError

    x = interactive
    sql = (
        (Path(__file__).resolve().parents[2] / "deploy/slack-gateway-role.sql")
        .read_text()
        .replace("BEGIN;\n", "", 1)
        .rsplit("COMMIT;", 1)[0]
    )
    x.db.execute(text(sql))
    x.db.flush()
    x.db.expire_all()
    body, headers = action_body(x)
    try:
        x.db.execute(text("SET LOCAL ROLE noc_slack_gateway_access"))
        assert not record(x.db, body, headers)["duplicate"]
        x.db.flush()
        for statement in [
            "SELECT * FROM public.users LIMIT 0",
            "SELECT * FROM public.notification_credentials LIMIT 0",
            "UPDATE public.notification_slack_integration SET live_authorized=true WHERE id=-1",
            "UPDATE public.incidents SET status='closed' WHERE id=-1",
            "INSERT INTO public.notification_deliveries (id) VALUES (-1)",
            "DELETE FROM public.notification_slack_interactions WHERE key='absent'",
        ]:
            with pytest.raises(DBAPIError):
                with x.db.begin_nested():
                    x.db.execute(text(statement))
    finally:
        x.db.execute(text("RESET ROLE"))


@pytest.mark.parametrize("mode", ["crash", "concurrent"])
def test_independent_committed_slack_claims(postgres_db, tmp_path, monkeypatch, mode):
    import os, threading
    from uuid import uuid4
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base
    from app.models.entities import User, Incident
    from app.notifications.models import NotificationPolicy
    from app.notifications.contracts import Policy

    url = os.environ["RF_INTEGRATION_TEST_DATABASE_URL"]
    parsed = make_url(url)
    assert (
        parsed.host in {"localhost", "127.0.0.1", "::1"}
        and parsed.database == "nwo_noc"
    )
    schema = "noc127_" + uuid4().hex
    owner = create_engine(url)
    with owner.begin() as connection:
        connection.exec_driver_sql('CREATE SCHEMA "' + schema + '"')
    engine = create_engine(
        url, execution_options={"schema_translate_map": {None: schema}}
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    for name in ["notifications_vault_key_file", "slack_callback_key_file"]:
        key = tmp_path / name
        key.write_bytes(Fernet.generate_key())
        key.chmod(0o600)
        monkeypatch.setattr(settings, name, str(key))
    monkeypatch.setattr(settings, "offline_mode", False)
    monkeypatch.setattr(settings, "slack_native_transport_enabled", True)
    monkeypatch.setattr(settings, "slack_egress_approved", True)
    monkeypatch.setattr(settings, "rf_scheduler_spool_dir", str(tmp_path / "spool"))
    try:
        Base.metadata.create_all(engine)
        with factory() as db:
            u = User(
                username="synthetic-slack",
                email="slack@example.invalid",
                role="admin",
                is_active=True,
                password_hash="not-used-for-authentication",
            )
            db.add(u)
            db.flush()
            incident = Incident(
                title="Synthetic isolated outage",
                severity="critical",
                started_at=service.clock(),
            )
            db.add(incident)
            db.add(NotificationPolicy(id=1, config=Policy().model_dump()))
            r = SlackIntegration(id=1, config=config() | {"enabled": True}, revision=1)
            db.add(r)
            db.flush()
            service.store_credentials(
                db,
                json.dumps(
                    {"bot_token": "xoxb-synthetic-not-live", "signing_secret": "a" * 32}
                ),
                u.id,
            )
            r.live_authorized = True
            r.authorization_reference = "synthetic"
            p = service.prepare(db, "incident", str(incident.id), u.id)
            d = service.approve(db, p.id, u.id, "synthetic-release", p.fingerprint)
            db.commit()
            delivery_id = d.id
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def wire(req):
            if req.url.path.endswith("auth.test"):
                return identity(req)
            calls.append(True)
            with factory() as observer:
                a = observer.scalar(
                    select(Attempt).where(Attempt.delivery_id == delivery_id)
                )
                assert a.acceptance == "pending" and a.claim_token
                assert observer.get(Delivery, delivery_id).state == "submitted"
            entered.set()
            if mode == "crash":
                raise SystemExit("synthetic termination after committed claim")
            assert release.wait(10)
            return accept(req)

        transport = httpx.MockTransport(wire)
        if mode == "crash":
            with pytest.raises(SystemExit):
                service.run_batch(factory, transport=transport, limit=1)
            with factory() as db:
                a = db.scalar(select(Attempt))
                a.lease_expires_at = service.clock() - timedelta(seconds=1)
                db.commit()
            assert (
                service.run_batch(
                    factory,
                    transport=httpx.MockTransport(
                        lambda _: pytest.fail("unsafe retry")
                    ),
                    limit=1,
                )["processed"]
                == 0
            )
            with factory() as db:
                assert db.get(Delivery, delivery_id).state == "reconciliation_required"
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(
                    service.run_batch, factory, transport=transport, limit=1
                )
                assert entered.wait(10)
                second = pool.submit(
                    service.run_batch, factory, transport=transport, limit=1
                )
                assert second.result(timeout=10)["processed"] == 0
                release.set()
                assert first.result(timeout=10)["processed"] == 1
        assert len(calls) == 1
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Attempt)) == 1
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.exec_driver_sql('DROP SCHEMA "' + schema + '" CASCADE')
        owner.dispose()


def test_optional_drafts_never_authorize_delivery(native):
    from app.models.entities import Incident

    x = native
    second = Incident(
        title="Synthetic application health event", severity="critical", started_at=NOW
    )
    x.db.add(second)
    x.db.flush()
    before = x.db.scalar(select(func.count()).select_from(Delivery))
    assert service.discover(x.db) >= 1
    assert x.db.scalar(select(func.count()).select_from(Delivery)) == before
    assert service.discover(x.db) == 0
