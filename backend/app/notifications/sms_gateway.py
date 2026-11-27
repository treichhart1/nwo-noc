"""Dedicated callback ASGI boundary; never mounts private NOC routers or docs."""

import asyncio
from datetime import timedelta
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import settings
from app.core.database import get_db
from app.notifications.service import clock
from app.notifications.sms_models import (
    SMSCallbackBudget,
    SMSProviderConfig,
    SMSWebhookEvent,
)
from app.notifications.sms_callbacks import apply_callback, CallbackBusy, normalize
from app.notifications.secrets import unseal
from app.notifications.sms_transport import SMSFailure

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)


def callback_budget(db, provider):
    minute = clock().replace(second=0, microsecond=0)
    value = db.scalar(
        insert(SMSCallbackBudget)
        .values(provider=provider, minute=minute, count=1)
        .on_conflict_do_update(
            index_elements=[SMSCallbackBudget.provider, SMSCallbackBudget.minute],
            set_={"count": func.least(SMSCallbackBudget.count + 1, 241)},
        )
        .returning(SMSCallbackBudget.count)
    )
    db.execute(
        delete(SMSCallbackBudget).where(
            SMSCallbackBudget.minute < minute - timedelta(minutes=10)
        )
    )
    db.commit()
    return value <= 240


@app.middleware("http")
async def security(request, call_next):
    try:
        response = await call_next(request)
    except Exception:
        response = JSONResponse({"detail": "SMS callback unavailable"}, status_code=503)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def receive(request, provider):
    if not settings.sms_callback_gateway_enabled:
        return JSONResponse({"detail": "SMS callbacks disabled"}, status_code=503)
    if request.query_params:
        return JSONResponse({"detail": "Invalid callback"}, status_code=400)
    expected = (
        "application/x-www-form-urlencoded"
        if provider == "twilio"
        else "application/json"
    )
    if (
        request.headers.get("content-type", "").split(";")[0].strip().lower()
        != expected
    ):
        return JSONResponse({"detail": "Invalid callback"}, status_code=415)

    async def bounded_body():
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 16384:
                raise ValueError()
        return bytes(body)

    try:
        body = await asyncio.wait_for(bounded_body(), timeout=3)
    except (ValueError, TimeoutError):
        return JSONResponse({"detail": "Invalid callback"}, status_code=413)
    # Blocking DB/crypto is offloaded; the standalone gateway never calls send().
    from starlette.concurrency import run_in_threadpool

    def process():
        generator = get_db()
        db = next(generator)
        try:
            config = db.get(SMSProviderConfig, provider)
            if (
                not config
                or not config.config.get("callbacks_enabled")
                or not config.webhook_ciphertext
            ):
                raise ValueError("Callback not configured")
            secret = unseal(settings.sms_callback_key_file, config.webhook_ciphertext)
            verified = normalize(
                provider, config.config, body, dict(request.headers), secret, clock()
            )
            prior = db.get(SMSWebhookEvent, verified["key"])
            if prior and prior.body_hash != verified["body_hash"]:
                raise ValueError("Conflicting event identity")
            if prior:
                return (
                    Response("<Response/>", media_type="application/xml")
                    if provider == "twilio"
                    else JSONResponse({"received": True, "duplicate": True})
                )
            # Invalid/replayed signatures cannot consume authenticated STOP quota.
            bucket = (
                provider + "-stop"
                if verified["kind"] == "stop" or verified["_opt_out"]
                else provider
            )
            if not callback_budget(db, bucket):
                return JSONResponse(
                    {"detail": "Callback rate limit"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
            result = apply_callback(db, provider, body, dict(request.headers))
            db.commit()
            return (
                Response("<Response/>", media_type="application/xml")
                if provider == "twilio"
                else JSONResponse({"received": True, "duplicate": result["duplicate"]})
            )
        except CallbackBusy:
            db.rollback()
            return JSONResponse({"detail": "Callback storage busy"}, status_code=503)
        except (ValueError, LookupError, OSError, TypeError, SMSFailure):
            db.rollback()
            return JSONResponse(
                {"detail": "Callback verification failed"}, status_code=403
            )
        except SQLAlchemyError:
            db.rollback()
            return JSONResponse(
                {"detail": "Callback storage unavailable"}, status_code=503
            )
        finally:
            generator.close()

    return await run_in_threadpool(process)


@app.post("/callbacks/sms/twilio")
async def twilio(request: Request):
    return await receive(request, "twilio")


@app.post("/callbacks/sms/telnyx")
async def telnyx(request: Request):
    return await receive(request, "telnyx")


@app.get("/health")
def health():
    # Process readiness only, never a claim about provider or database health.
    return {
        "status": "ok",
        "service": "sms-callbacks-only",
        "callbacks_enabled": settings.sms_callback_gateway_enabled,
    }
