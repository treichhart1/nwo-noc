"""NOC-94 security regressions; no live network."""

from datetime import datetime, timedelta, timezone
import os
import pytest, jwt, httpx
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from test_rf_postgres_integration import postgres_db
from app.core.config import settings, Settings
from app.core.security import create_access_token, decode_access_claims, hash_password
from app.core.database import get_db
from app.main import app
from app.models.entities import User, RevokedToken, AuditLog
from app.services import integration_interlock as latch
from app.services.monitoring_scheduler import claim


@pytest.fixture
def auth(postgres_db):
    user = User(
        username="noc94-user",
        email="noc94@synthetic.invalid",
        password_hash=hash_password("synthetic-test-only-password"),
        role="admin",
        is_active=True,
    )
    postgres_db.add(user)
    postgres_db.commit()
    app.dependency_overrides[get_db] = lambda: postgres_db
    try:
        with TestClient(app) as c:
            yield c, user, postgres_db
    finally:
        app.dependency_overrides.clear()


def login(c):
    r = c.post(
        "/api/auth/login",
        data={"username": "noc94-user", "password": "synthetic-test-only-password"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(t):
    return {"Authorization": "Bearer " + t}


def test_logout_revokes_presented_session_only_and_is_durable(auth):
    c, user, db = auth
    t = login(c)
    other = login(c)
    assert c.get("/api/auth/me", headers=bearer(t)).status_code == 200
    assert c.post("/api/auth/logout", headers=bearer(t)).status_code == 204
    db.expire_all()
    assert c.get("/api/auth/me", headers=bearer(t)).status_code == 401
    assert c.get("/api/auth/me", headers=bearer(other)).status_code == 200
    assert db.scalar(select(func.count(RevokedToken.token_id))) == 1
    audits = list(db.scalars(select(AuditLog).where(AuditLog.action.like("auth.%"))))
    assert len(audits) == 3 and all(
        t not in str(a.details) and other not in str(a.details) for a in audits
    )


def test_identity_binding_and_live_role_changes(auth):
    c, user, db = auth
    t = login(c)
    assert (
        c.get(
            "/api/auth/me",
            headers=bearer(create_access_token(user.username, user_id=user.id + 1)),
        ).status_code
        == 401
    )
    user.role = "viewer"
    db.commit()
    assert (
        c.post(
            "/api/monitoring/reliability/emergency-disable", headers=bearer(t)
        ).status_code
        == 403
    )
    user.is_active = False
    db.commit()
    assert c.get("/api/auth/me", headers=bearer(t)).status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "other"},
        {"aud": "other"},
        {"token_type": "refresh"},
        {"jti": "invalid"},
        {"uid": True},
        {"exp": True},
        {"sub": []},
        {"iat": -100},
        {"exp": int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())},
    ],
)
def test_claims_fail_closed(change):
    t = create_access_token("synthetic", user_id=1)
    c = (
        jwt.decode(t, settings.secret_key, algorithms=["HS256"], audience="nwo-noc-api")
        | change
    )
    assert (
        decode_access_claims(jwt.encode(c, settings.secret_key, algorithm="HS256"))
        is None
    )


def test_legacy_and_oversized_tokens_rejected():
    t = jwt.encode(
        {"sub": "synthetic", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.secret_key,
        algorithm="HS256",
    )
    assert decode_access_claims(t) is None and decode_access_claims("a" * 5000) is None


@pytest.mark.parametrize("ttl", [0, 31, 720])
def test_long_session_lifetime_rejected(ttl):
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            secret_key="a" * 40,
            admin_password="b" * 40,
            database_url="postgresql+psycopg://synthetic/db",
            access_token_expire_minutes=ttl,
        )


def test_admin_stop_persists_without_network_or_false_outage(
    auth, tmp_path, monkeypatch
):
    c, user, db = auth
    monkeypatch.setattr(settings, "rf_scheduler_spool_dir", str(tmp_path))
    t = login(c)
    assert (
        c.post(
            "/api/monitoring/reliability/emergency-disable", headers=bearer(t)
        ).status_code
        == 200
    )
    assert latch.disabled() and claim(db, "worker") == []
    from app.services.monitoring import tcp_check, run_due_checks
    import socket

    monkeypatch.setattr(
        socket, "create_connection", lambda *a, **kw: pytest.fail("network contact")
    )
    assert (
        tcp_check("192.0.2.94", 443, 1)[0] is False
        and run_due_checks(db)["operator_disabled"] is True
    )
    from app.integrations.read_only import (
        ReadClient,
        ReadPolicy,
        MIKROTIK_PATHS,
        IntegrationFailure,
    )

    with ReadClient(
        ReadPolicy(
            "https://192.0.2.94", ("https://192.0.2.94",), MIKROTIK_PATHS, True, True
        ),
        transport=httpx.MockTransport(lambda req: pytest.fail("HTTP contact")),
    ) as client:
        with pytest.raises(IntegrationFailure) as e:
            client.request("/rest/system/resource")
        assert e.value.code == "disabled"
    assert (
        c.get("/api/monitoring/reliability", headers=bearer(t)).json()[
            "operator_disabled"
        ]
        is True
    )
    assert (tmp_path / latch.FLAG).stat().st_mode & 0o777 == 0o600


def test_symlink_latch_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "rf_scheduler_spool_dir", str(tmp_path))
    (tmp_path / latch.FLAG).symlink_to(tmp_path / "missing")
    assert latch.disabled()


def test_spool_scan_preserves_payload_and_rejects_symlink(tmp_path):
    from app.runtime_permissions import scan

    p = tmp_path / ("a" * 64 + ".json")
    p.write_text("synthetic-payload")
    p.chmod(0o600)
    assert (
        scan(tmp_path, owners=(os.geteuid(),))[1] == [p]
        and p.read_text() == "synthetic-payload"
    )
    tmp_path.chmod(0o777)
    with pytest.raises(ValueError):
        scan(tmp_path, owners=(os.geteuid(),))
    tmp_path.chmod(0o700)
    p.unlink()
    p.symlink_to(tmp_path / "other")
    with pytest.raises(ValueError):
        scan(tmp_path, owners=(os.geteuid(),))


def test_snmp_authpriv_crypto_retained():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    from cryptography.hazmat.decrepit.ciphers.modes import CFB

    key = b"a" * 16
    iv = b"b" * 16
    raw = b"synthetic-radio-data"
    e = Cipher(algorithms.AES(key), CFB(iv)).encryptor()
    v = e.update(raw) + e.finalize()
    d = Cipher(algorithms.AES(key), CFB(iv)).decryptor()
    assert d.update(v) + d.finalize() == raw


def test_settings_do_not_expose_secrets():
    s = Settings(
        _env_file=None,
        secret_key="never-print-secret-key-" + "a" * 30,
        admin_password="never-print-password",
        database_url="postgresql+psycopg://user:never-print-dbpwd@localhost/nwo_noc",
    )
    text = repr(s) + str(s.model_dump())
    assert all(
        v not in text
        for v in ("never-print-secret-key", "never-print-password", "never-print-dbpwd")
    )


def test_deep_malformed_jwt_is_unauthenticated_not_server_error():
    import base64

    header = (
        base64.urlsafe_b64encode(b"[" * 1100 + b"0" + b"]" * 1100).rstrip(b"=").decode()
    )
    assert decode_access_claims(header + ".e30.sig") is None
