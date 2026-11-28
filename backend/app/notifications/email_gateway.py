"""Isolated SendGrid signed-event receiver; no private routes or send capability."""

import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from app.core.config import settings
from app.core.database import get_db
from app.notifications import email_service as service
from app.notifications.sms_gateway import callback_budget
from app.notifications.email_models import EmailProvider
from app.notifications.email_transport import verified_events
from app.notifications.service import clock

BUDGET_KEYS = ("email-sendgrid", "email-sg-hold")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)


@app.middleware("http")
async def security(request, call_next):
    try:
        r = await call_next(request)
    except Exception:
        r = JSONResponse(
            {"detail": "Email event receiver unavailable"}, status_code=503
        )
    r.headers["Cache-Control"] = "no-store"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


@app.post("/callbacks/email/sendgrid")
async def callback(request: Request):
    if not settings.email_callback_gateway_enabled:
        return JSONResponse({"detail": "Email callbacks disabled"}, status_code=503)
    if (
        request.query_params
        or request.headers.get("content-type", "").split(";")[0] != "application/json"
    ):
        return JSONResponse({"detail": "Invalid callback"}, status_code=400)

    async def bounded():
        value = bytearray()
        async for part in request.stream():
            value.extend(part)
            if len(value) > 65536:
                raise ValueError()
        return bytes(value)

    try:
        body = await asyncio.wait_for(bounded(), 3)
    except (ValueError, TimeoutError):
        return JSONResponse({"detail": "Bounded body required"}, status_code=413)

    def process():
        generator = get_db()
        db = next(generator)
        try:
            # Unauthenticated traffic cannot consume signed suppression quota.
            provider = db.get(EmailProvider, "sendgrid")
            if (
                not provider
                or not provider.webhook_public_key
                or not provider.config.get("callbacks_enabled")
            ):
                raise ValueError("Callback not configured")
            verified = verified_events(
                body,
                dict(request.headers),
                provider.webhook_public_key,
                int(clock().timestamp()),
            )
            priority = any(
                e["event"]
                in {"bounce", "spamreport", "unsubscribe", "group_unsubscribe"}
                for e in verified
            )
            if not callback_budget(db, BUDGET_KEYS[1] if priority else BUDGET_KEYS[0]):
                return JSONResponse(
                    {"detail": "Rate limit"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
            result = service.apply_events(db, body, dict(request.headers))
            db.commit()
            return result
        except (ValueError, LookupError):
            db.rollback()
            return JSONResponse(
                {"detail": "Email event verification failed"}, status_code=403
            )
        finally:
            generator.close()

    return await run_in_threadpool(process)


@app.get("/health")
def health():
    return {"status": "ok", "scope": "receiver process only", "delivery_enabled": False}
