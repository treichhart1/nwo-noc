"""HMAC staging-only credential. Cannot approve or write device configuration."""

import hashlib, hmac, time, stat, os
from pathlib import Path
from sqlalchemy import select, func
from app.core.config import settings
from app.notifications.secrets import seal, unseal
from app.field.models import now
from .models import Nonce
from .service import Conflict


def key_path():
    p = Path(settings.fiber_designer_key_file)
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise ValueError("Private credential vault key required")
    return str(p)


def set_secret(source, value):
    source.encrypted_secret = seal(key_path(), value)
    source.revision += 1


def sign(secret, source_id, timestamp, nonce, raw):
    message = f"v1\n{source_id}\n{timestamp}\n{nonce}\n{hashlib.sha256(raw).hexdigest()}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify(db, source, timestamp, nonce, signature, raw):
    from uuid import UUID

    if not (
        settings.fiber_designer_ingest_enabled
        and source.enabled
        and source.ingest_enabled
        and source.encrypted_secret
    ):
        raise PermissionError("Integration ingestion disabled")
    try:
        if (
            len(timestamp) > 12
            or abs(time.time() - int(timestamp)) > 300
            or str(UUID(nonce)) != nonce
        ):
            raise ValueError()
        if len(signature) != 64:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise PermissionError("Integration authentication unavailable") from None
    try:
        secret = unseal(key_path(), source.encrypted_secret)
    except (ValueError, OSError):
        raise PermissionError("Integration credential unavailable") from None
    if not hmac.compare_digest(
        sign(secret, source.id, timestamp, nonce, raw), signature
    ):
        raise PermissionError("Integration authentication unavailable")
    if db.get(Nonce, (source.id, nonce)):
        raise Conflict("transport_nonce_replayed")
    minute = now().replace(second=0, microsecond=0)
    if (
        db.scalar(
            select(func.count())
            .select_from(Nonce)
            .where(Nonce.source_id == source.id, Nonce.created_at >= minute)
        )
        >= 20
    ):
        raise Conflict("integration_ingest_budget_exceeded")
    db.add(Nonce(source_id=source.id, nonce=nonce))
    db.flush()
