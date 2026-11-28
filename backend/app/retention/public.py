"""Restricted public request intake: no account lookup or private NOC routes."""

import json
import secrets
from uuid import uuid4
from types import SimpleNamespace
from fastapi import Request, Response, Depends, HTTPException
from sqlalchemy import text
from app.core.database import get_db
from app.core.config import settings
from app.signup import public_service as shared, crypto, providers
from app.signup.public_app import app, execute
from .contracts import PublicInput


def enabled():
    shared.enabled()
    if not settings.retention_public_enabled:
        raise HTTPException(
            503, "Cancellation requests are not configured; contact the office"
        )


@app.get("/public/cancellation/config")
def config(request: Request, response: Response, db=Depends(get_db)):
    def read():
        enabled()
        shared.budget(db, request, "csrf")
        token = shared.csrf_issue()
        response.set_cookie(
            "signup_csrf",
            token,
            httponly=True,
            secure=not (settings.signup_public_test_mode and providers.isolated()),
            samesite="strict",
            max_age=900,
            path="/",
        )
        data = db.scalar(text("SELECT public.noc_cancel_config()"))
        if not data or not data.get("policy"):
            raise HTTPException(
                503, "Cancellation policy is unavailable; contact the office"
            )
        return {
            **data,
            "csrf_token": token,
            "site_key": settings.signup_turnstile_site_key
            if settings.signup_turnstile_mode == "turnstile"
            else None,
            "fixture_mode": settings.signup_public_test_mode and providers.isolated(),
        }

    return execute(db, read)


@app.post("/public/cancellation/requests")
def submit(body: PublicInput, request: Request, db=Depends(get_db)):
    def write():
        enabled()
        shared.csrf_check(
            request.headers.get("origin"),
            request.cookies.get("signup_csrf"),
            request.headers.get("x-signup-csrf"),
        )
        shared.budget(db, request, "submit")
        payload = body.model_dump(mode="json", exclude={"turnstile_token", "website"})
        signature = crypto.digest(
            "cancellation_submission",
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )
        token = crypto.digest("cancellation_receipt", str(body.submission_key))
        db.execute(text("SET LOCAL statement_timeout='5000ms'"))
        db.execute(text("SET LOCAL lock_timeout='3000ms'"))
        old = db.scalar(
            text("SELECT public.noc_cancel_replay(:key,:digest)"),
            {"key": str(body.submission_key), "digest": signature},
        )
        if old:
            return {**old, "receipt_token": token}
        if body.website:
            return {
                "received": True,
                "reference": "CAN-" + str(uuid4()),
                "receipt_token": secrets.token_hex(32),
                "message": "Request received for identity verification and review. No service or billing changes have been made.",
            }
        if not providers.turnstile(body.turnstile_token, body.submission_key):
            raise PermissionError("Security verification failed")
        transient = SimpleNamespace(wrapped_key=None, pii_ciphertext=None)
        crypto.initialize(transient, payload)
        result = db.scalar(
            text(
                "SELECT public.noc_cancel_submit(:key,:digest,:ref,:receipt,:wrapped,:pii,:requested,:reason,:policy)"
            ),
            {
                "key": str(body.submission_key),
                "digest": signature,
                "ref": "CAN-" + str(uuid4()),
                "receipt": crypto.digest("cancel_receipt_hash", token),
                "wrapped": transient.wrapped_key,
                "pii": transient.pii_ciphertext,
                "requested": body.requested_date,
                "reason": body.reason,
                "policy": body.policy_id,
            },
        )
        return {**result, "receipt_token": token}

    return execute(db, write)


@app.get("/public/cancellation/health")
def health(db=Depends(get_db)):
    def check():
        enabled()
        data = db.scalar(text("SELECT public.noc_cancel_config()"))
        if not data or not data.get("policy"):
            raise HTTPException(
                503, "Cancellation policy is unavailable; contact the office"
            )
        return {"status": "ok", "service": "public cancellation request intake"}

    return execute(db, check)
