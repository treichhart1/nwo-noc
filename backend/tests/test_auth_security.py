"""Authentication token regression tests: no database or network required."""
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.core import security


def test_token_round_trip():
    token = security.create_access_token("noc-test-user")
    assert security.decode_access_token(token) == "noc-test-user"


def test_expired_token_is_rejected():
    token = jwt.encode(
        {"sub": "noc-test-user", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        security.settings.secret_key,
        algorithm=security.ALGORITHM,
    )
    assert security.decode_access_token(token) is None


def test_invalid_signature_is_rejected():
    token = jwt.encode(
        {"sub": "noc-test-user", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "different-signing-key-for-test",
        algorithm=security.ALGORITHM,
    )
    assert security.decode_access_token(token) is None


def test_malformed_token_is_rejected():
    assert security.decode_access_token("not-a-valid-jwt") is None
