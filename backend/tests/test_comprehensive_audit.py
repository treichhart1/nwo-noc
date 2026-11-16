"""Reproduced audit defects; synthetic HTTP streams and disposable PostgreSQL only."""

import json
from uuid import uuid4
from types import SimpleNamespace
from datetime import timedelta
import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from app.core.config import settings
from app.core.security import create_access_token
from app.models.entities import RevokedToken, AuditLog, User, Site, Device
from app.api import session_routes, routes
from app.signup import providers
from test_rf_postgres_integration import postgres_db
from app.core import login_guard
from test_final_release_security import auth


@pytest.mark.parametrize("adapter", ["read_only", "powercode", "ollama"])
def test_shared_http_clients_reject_compressed_bombs_without_retry(adapter):
    import gzip
    from app.core.http_json import ResponseBudgetExceeded

    calls = []
    compressed = gzip.compress(json.dumps({"padding": "x" * 5000000}).encode())

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield compressed

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=Stream()
        )

    transport = httpx.MockTransport(handler)
    if adapter == "read_only":
        from app.integrations.read_only import (
            ReadClient,
            ReadPolicy,
            UISP_PATHS,
            IntegrationFailure,
        )

        policy = ReadPolicy(
            "https://synthetic.example.invalid",
            ("https://synthetic.example.invalid",),
            UISP_PATHS,
            True,
            True,
            max_bytes=32000,
        )
        with ReadClient(policy, transport=transport) as client, pytest.raises(
            IntegrationFailure, match="response_too_large"
        ):
            client.request("/nms/api/v2.1/devices")
    elif adapter == "powercode":
        from app.customers.providers import FixtureAPI, ProviderFailure

        with pytest.raises(ProviderFailure, match="schema_mismatch"):
            FixtureAPI(transport, limits={"max_bytes": 32000}).fetch()
    else:
        from app.services.rf_explanation import LocalOllamaProvider

        with pytest.raises(ResponseBudgetExceeded):
            LocalOllamaProvider("synthetic", transport=transport).plan({})
    assert len(calls) == 1


def test_shared_http_json_preserves_raw_deflate_and_enforces_deadline():
    import zlib
    from app.core.http_json import bounded_json

    compressor = zlib.compressobj(wbits=-15)
    payload = compressor.compress(b'{"synthetic":true}') + compressor.flush()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield payload

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"Content-Encoding": "deflate"}, stream=Stream()
            )
        )
    ) as client:
        with client.stream("GET", "https://synthetic.example.invalid") as response:
            assert bounded_json(response, 100) == {"synthetic": True}
        with client.stream(
            "GET", "https://synthetic.example.invalid"
        ) as response, pytest.raises(TimeoutError):
            bounded_json(response, 100, deadline=1, clock=lambda: 2)


@pytest.mark.parametrize(
    "module,flag",
    [
        ("app.analytics.worker", "analytics_rollup_enabled"),
        ("app.fiber_sync.worker", "fiber_designer_worker_enabled"),
    ],
)
def test_worker_interruptions_close_sessions_and_emit_sanitized_warnings(
    module, flag, monkeypatch, tmp_path, caplog
):
    import runpy, sys, time
    from app.core import database

    class StopLoop(BaseException):
        pass

    closed = []

    class Session:
        def __enter__(self):
            return self

        def execute(self, *a, **kw):
            raise RuntimeError("synthetic-provider-password-must-not-appear")

        def __exit__(self, *exc):
            closed.append(True)

    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(
        settings, "fiber_designer_heartbeat_file", str(tmp_path / "worker-heartbeat")
    )
    monkeypatch.setattr(sys, "argv", ["synthetic-worker"])
    monkeypatch.delitem(sys.modules, module, raising=False)

    def stop(*a):
        raise StopLoop()

    monkeypatch.setattr(time, "sleep", stop)
    with pytest.raises(StopLoop):
        runpy.run_module(module, run_name="__main__")
    assert closed == [True] and "interrupted" in caplog.text
    assert "synthetic-provider-password" not in caplog.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/devices",
        "/api/customers",
        "/api/finance/v1/periods",
        "/api/maintenance-ops/documents",
    ],
)
def test_private_api_denials_are_never_cacheable(path):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        response = c.get(path)
    assert response.status_code in {401, 403, 404}
    assert response.headers["Cache-Control"] == "no-store"


def test_failed_login_consumes_budget_and_never_issues_a_token(auth, monkeypatch):
    c, user, db = auth
    monkeypatch.setitem(login_guard.LIMITS, "account", 2)
    for _ in range(2):
        r = c.post(
            "/api/auth/login",
            data={"username": user.username, "password": "wrong-synthetic-password"},
        )
        assert r.status_code == 401 and "access_token" not in r.json()
    r = c.post(
        "/api/auth/login",
        data={"username": user.username, "password": "synthetic-test-only-password"},
    )
    assert r.status_code == 429 and r.headers["Cache-Control"] == "no-store"
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_type == login_guard.ENTITY)
        )
        == 6
    )


def test_unknown_user_uses_dummy_password_verification(auth, monkeypatch):
    from app.core.security import pwd_context

    c, _, _ = auth
    calls = []
    monkeypatch.setattr(pwd_context, "dummy_verify", lambda: calls.append(True))
    r = c.post(
        "/api/auth/login",
        data={"username": "unknown-synthetic-user", "password": "synthetic-only"},
    )
    assert r.status_code == 401 and calls == [True]


def test_global_login_budget_limits_rotating_accounts_and_peers(
    postgres_db, monkeypatch
):
    monkeypatch.setitem(login_guard.LIMITS, "global", 1)
    login_guard.reserve(postgres_db, "one", "192.0.2.1")
    with pytest.raises(HTTPException) as e:
        login_guard.reserve(postgres_db, "two", "192.0.2.2")
    assert e.value.status_code == 429


@pytest.fixture
def provider_config(monkeypatch, tmp_path):
    secret = tmp_path / "provider.key"
    secret.write_text("synthetic-only-provider-key")
    secret.chmod(0o600)
    for k, v in dict(
        offline_mode=False,
        signup_turnstile_mode="turnstile",
        signup_turnstile_secret_file=str(secret),
        signup_public_origin="https://signup.example.invalid",
        signup_geocoder="nominatim",
        signup_geocoder_origin="https://geocode.example.invalid",
        signup_geocoder_approved_origins="https://geocode.example.invalid",
        signup_geocoder_admin_approved=True,
    ).items():
        monkeypatch.setattr(settings, k, v)


class EndlessResponse(httpx.SyncByteStream):
    def __init__(self):
        self.bytes = 0
        self.closed = False

    def __iter__(self):
        for _ in range(256):
            self.bytes += 8192
            yield b" " * 8192

    def close(self):
        self.closed = True


@pytest.mark.parametrize("kind,limit", [("turnstile", 32000), ("geocoder", 64000)])
def test_provider_response_budget_stops_reading_and_closes_stream(
    provider_config, kind, limit
):
    streams = []

    def handler(req):
        stream = EndlessResponse()
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        if kind == "turnstile":
            assert providers.turnstile("synthetic-token", uuid4(), client) is False
        else:
            assert providers.geocode({"street": "Synthetic road"}, client) is None
    assert streams and all(s.bytes <= limit + 8192 and s.closed for s in streams)


@pytest.mark.parametrize("payload", [None, [], True, 1, "not an object"])
def test_malformed_turnstile_envelope_fails_closed(provider_config, payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as client:
        assert providers.turnstile("synthetic-token", uuid4(), client) is False


def test_logout_replay_after_parallel_auth_checks_is_idempotent(postgres_db):
    db = postgres_db
    u = User(
        username="audit-logout",
        email="logout@example.invalid",
        role="admin",
        password_hash="unusable",
        is_active=True,
    )
    db.add(u)
    db.flush()
    token = create_access_token(u.username, user_id=u.id)
    # Both requests can pass current_user before either commits its revocation.
    assert session_routes.logout(user=u, token=token, db=db).status_code == 204
    assert session_routes.logout(user=u, token=token, db=db).status_code == 204
    assert db.scalar(select(func.count()).select_from(RevokedToken)) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "auth.logout")
        )
        == 1
    )


def test_invalid_device_site_is_rejected_before_mutation(postgres_db):
    from app.schemas.common import DevicePatch

    db = postgres_db
    d = Device(
        source="synthetic",
        external_id="audit-invalid-site",
        name="Synthetic audit device",
    )
    db.add(d)
    db.flush()
    user = SimpleNamespace(id=None, role="operator")
    request = SimpleNamespace(client=None)
    with pytest.raises(HTTPException) as caught:
        routes.patch_device(
            d.id, DevicePatch(wisp_site_id=2147483647), request, db, user
        )
    assert caught.value.status_code == 422
    assert d.wisp_site_id is None and not db.scalar(
        select(AuditLog.id).where(AuditLog.action == "device.update")
    )
    assert (
        db.scalar(select(func.count()).select_from(Device)) >= 1
    )  # Session remains usable.


def test_site_summary_preserves_canonical_and_legacy_membership(postgres_db):
    db = postgres_db
    a, b = Site(name="Audit site A"), Site(name="Audit site B")
    db.add_all([a, b])
    db.flush()
    for id, name, status in [
        (a.id, b.name, "active"),
        (None, a.name, "offline"),
        (b.id, a.name, "unknown"),
    ]:
        db.add(
            Device(
                source="synthetic",
                external_id=uuid4().hex,
                name="Synthetic summary device",
                wisp_site_id=id,
                site_name=name,
                status=status,
                authorized=True,
            )
        )
    db.flush()
    output = {s.id: s for s in routes.sites(db=db, _=None)}
    assert (
        output[a.id].device_count == 2
        and output[a.id].offline_count == 1
        and output[a.id].health_score == 50
    )

    assert (
        output[b.id].device_count == 1
        and output[b.id].active_count == 0
        and output[b.id].health_score == 0
    )


@pytest.mark.parametrize(
    "field",
    [
        "monitor_enabled",
        "monitor_port",
        "monitor_interval_seconds",
        "monitor_timeout_seconds",
    ],
)
def test_nullable_patch_does_not_bypass_nonnull_database_constraints(field):
    from pydantic import ValidationError
    from app.schemas.common import DevicePatch

    with pytest.raises(ValidationError):
        DevicePatch(**{field: None})
    assert DevicePatch().model_dump(exclude_unset=True) == {}


def test_link_updates_reject_invalid_inventory_before_mutation(postgres_db):
    from app.models.entities import NetworkLink
    from app.schemas.common import NetworkLinkCreate, DevicePatch

    db = postgres_db
    a, b = Site(name="Audit link A"), Site(name="Audit link B")
    db.add_all([a, b])
    db.flush()
    link = NetworkLink(name="Audit original link", a_site_id=a.id, z_site_id=b.id)
    db.add(link)
    db.flush()
    for data in [dict(a_site_id=2147483647), dict(a_device_id=2147483647)]:
        values = dict(name="Audit edited link", a_site_id=a.id, z_site_id=b.id)
        values.update(data)
        with pytest.raises(HTTPException) as e:
            routes.update_link(
                link.id,
                NetworkLinkCreate(**values),
                SimpleNamespace(client=None),
                db,
                SimpleNamespace(id=None),
            )
        assert e.value.status_code in {404, 422} and link.name == "Audit original link"
    d = Device(
        source="synthetic", external_id="audit-parent", name="Synthetic audit parent"
    )
    db.add(d)
    db.flush()
    with pytest.raises(HTTPException) as e:
        routes.patch_device(
            d.id,
            DevicePatch(parent_device_id=2147483647),
            SimpleNamespace(client=None),
            db,
            SimpleNamespace(id=None),
        )
    assert e.value.status_code == 422 and d.parent_device_id is None


def test_login_budget_is_durable_bounded_private_and_expires(postgres_db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for _ in range(login_guard.LIMITS["account"]):
        login_guard.reserve(postgres_db, "synthetic-account", "192.0.2.111", now=now)
    with pytest.raises(HTTPException) as e:
        login_guard.reserve(postgres_db, "synthetic-account", "192.0.2.112", now=now)
    assert e.value.status_code == 429 and e.value.headers["Retry-After"] == "60"
    # Failed requests cannot erase prior budget rows. Different peer does not evade account limit.
    rows = list(
        postgres_db.scalars(
            select(AuditLog).where(AuditLog.entity_type == login_guard.ENTITY)
        )
    )
    assert len(rows) == 3 * login_guard.LIMITS["account"]
    assert all(
        "synthetic-account" not in str(r.details) + r.entity_id
        and "192.0.2." not in str(r.details) + r.entity_id
        for r in rows
    )
    login_guard.reserve(
        postgres_db, "synthetic-account", "192.0.2.111", now=now + timedelta(seconds=61)
    )


def test_login_budget_limits_username_rotation_at_shared_peer(postgres_db, monkeypatch):
    monkeypatch.setitem(login_guard.LIMITS, "peer", 2)
    login_guard.reserve(postgres_db, "one", "192.0.2.111")
    login_guard.reserve(postgres_db, "two", "192.0.2.111")
    with pytest.raises(HTTPException) as e:
        login_guard.reserve(postgres_db, "three", "192.0.2.111")
    assert e.value.status_code == 429


def test_login_request_body_is_bounded_without_content_length():
    from fastapi.testclient import TestClient
    from app.main import app

    def chunks():
        yield b"username=" + b"x" * 9000

    with TestClient(app) as c:
        r = c.post(
            "/api/auth/login",
            content=chunks(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 413 and r.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_provider_compressed_response_is_bounded_after_inflation(
    provider_config, encoding
):
    import zlib, gzip

    payload = json.dumps({"padding": "x" * 1000000}).encode()
    compressed = (
        gzip.compress(payload) if encoding == "gzip" else zlib.compress(payload)
    )

    class Compressed(httpx.SyncByteStream):
        def __iter__(self):
            yield compressed

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"Content-Encoding": encoding}, stream=Compressed()
            )
        )
    ) as client:
        assert providers.turnstile("synthetic-token", uuid4(), client) is False


def test_valid_compressed_provider_json_is_preserved(provider_config):
    import gzip
    from datetime import datetime, timezone

    data = {
        "success": True,
        "action": "service_signup",
        "hostname": "signup.example.invalid",
        "challenge_ts": datetime.now(timezone.utc).isoformat(),
    }

    class Compressed(httpx.SyncByteStream):
        def __iter__(self):
            yield gzip.compress(json.dumps(data).encode())

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=Compressed()
            )
        )
    ) as client:
        assert providers.turnstile("synthetic-token", uuid4(), client) is True


@pytest.fixture
def concurrent_auth_schema(postgres_db):
    from sqlalchemy import create_engine, text
    from app.core.database import Base

    schema = "noc111_auth_" + uuid4().hex
    engine = create_engine(postgres_db.get_bind().engine.url, pool_size=3)
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA " + schema))
    isolated = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(
        isolated, tables=[User.__table__, AuditLog.__table__, RevokedToken.__table__]
    )
    try:
        yield isolated
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        engine.dispose()


def test_concurrent_login_budget_cannot_oversubscribe(
    concurrent_auth_schema, monkeypatch
):
    from sqlalchemy.orm import Session
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    monkeypatch.setitem(login_guard.LIMITS, "account", 1)
    gate = Barrier(2)

    def attempt():
        with Session(concurrent_auth_schema) as db:
            gate.wait(timeout=10)
            try:
                login_guard.reserve(db, "synthetic-race", "192.0.2.111")
                return 200
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert sorted(f.result(timeout=15) for f in futures) == [200, 429]
    with Session(concurrent_auth_schema) as db:
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 3


def test_actual_parallel_logout_has_one_durable_revocation_and_audit(
    concurrent_auth_schema,
):
    from sqlalchemy.orm import Session
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    with Session(concurrent_auth_schema) as db:
        user = User(
            username="synthetic-race",
            email="race@example.invalid",
            role="admin",
            password_hash="unusable",
            is_active=True,
        )
        db.add(user)
        db.flush()
        token = create_access_token(user.username, user_id=user.id)
        id = user.id
        db.commit()
    gate = Barrier(2)

    def revoke():
        with Session(concurrent_auth_schema) as db:
            user = db.get(User, id)
            gate.wait(timeout=10)
            return session_routes.logout(user=user, token=token, db=db).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(revoke) for _ in range(2)]
        assert [f.result(timeout=15) for f in futures] == [204, 204]
    with Session(concurrent_auth_schema) as db:
        assert db.scalar(select(func.count()).select_from(RevokedToken)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "auth.logout")
            )
            == 1
        )
