"""Bounded, issuer/audience-bound HS256 sessions; durable logout lives in PostgreSQL."""

from datetime import datetime, timedelta, timezone
import re, uuid
import jwt
from passlib.context import CryptContext
from app.core.config import settings

ALGORITHM = "HS256"
ISSUER = "nwo-noc"
AUDIENCE = "nwo-noc-api"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    if not 1 <= len(password.encode()) <= 72:
        raise ValueError("Password must contain 1–72 UTF-8 bytes")
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    if not 1 <= len(plain.encode()) <= 72:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, *, user_id: int = 0) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": subject,
            "uid": user_id,
            "iat": int(now.timestamp()),
            "exp": int(
                (
                    now + timedelta(minutes=settings.access_token_expire_minutes)
                ).timestamp()
            ),
            "jti": uuid.uuid4().hex,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "token_type": "access",
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_access_claims(token: str):
    if not isinstance(token, str) or len(token) > 4096:
        return None
    try:
        c = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={
                "require": ["sub", "exp", "iat", "jti", "iss", "aud", "token_type"]
            },
        )
        if type(c.get("uid")) is not int or c["uid"] < 0:
            return None
        if (
            not isinstance(c["sub"], str)
            or not 1 <= len(c["sub"]) <= 80
            or not isinstance(c["jti"], str)
            or not re.fullmatch("[a-f0-9]{32}", c["jti"])
            or c["token_type"] != "access"
        ):
            return None
        if (
            any(type(c[k]) is not int for k in ("exp", "iat"))
            or not 0 < c["exp"] - c["iat"] <= settings.access_token_expire_minutes * 60
        ):
            return None
        return c
    except (
        jwt.PyJWTError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        OverflowError,
    ):
        return None


def decode_access_token(token: str):
    claims = decode_access_claims(token)
    return claims["sub"] if claims else None
