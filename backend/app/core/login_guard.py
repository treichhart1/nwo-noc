"""Durable private-login budgets using existing indexed audit records, not process memory."""

import hashlib, hmac
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text
from fastapi import HTTPException
from app.core.config import settings
from app.models.entities import AuditLog

LIMITS = {"account": 30, "peer": 120, "global": 600}
ENTITY = "authentication_budget"


def key(kind, value):
    digest = hmac.new(
        settings.secret_key.encode(), (kind + ":" + value).encode(), hashlib.sha256
    ).hexdigest()
    return kind + ":" + digest


def reserve(db, username, peer, *, now=None):
    """Commit before authentication so failed password requests cannot roll back budgets.

    The entity cursor index bounds each read to at most its configured limit even
    with years of retained audit history. No username, password, raw IP or token is stored.
    """
    now = now or datetime.now(timezone.utc)
    if db.get_bind().dialect.name != "postgresql":
        raise ValueError("PostgreSQL authentication storage required")
    db.execute(text("SET LOCAL statement_timeout='3000ms'"))
    db.execute(text("SELECT pg_advisory_xact_lock(1110111)"))
    keys = {
        "account": key("account", username),
        "peer": key("peer", peer),
        "global": key("global", "private-login"),
    }
    for kind, ident in keys.items():
        recent = db.scalars(
            select(AuditLog.created_at)
            .where(AuditLog.entity_type == ENTITY, AuditLog.entity_id == ident)
            .order_by(AuditLog.id.desc())
            .limit(LIMITS[kind])
        ).all()
        if (
            sum(stamp >= now - timedelta(seconds=60) for stamp in recent)
            >= LIMITS[kind]
        ):
            db.rollback()
            raise HTTPException(
                429,
                "Login request budget exceeded; retry in one minute",
                headers={"Retry-After": "60", "Cache-Control": "no-store"},
            )
    for kind, ident in keys.items():
        db.add(
            AuditLog(
                action="security.login_attempt",
                entity_type=ENTITY,
                entity_id=ident,
                details={"scope": kind, "window_seconds": 60},
                created_at=now,
            )
        )
    db.commit()
