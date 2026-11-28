"""Isolated SendGrid signed-event receiver; no private routes or send capability."""

import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from app.core.config import settings
from app.core.database import get_db
from app.notifications import email_service as service
from app.notifications.sms_gateway import callback_budget

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
            # Shared persistent callback quota prevents unbounded DB/crypto work.
            if not callback_budget(db, "email-sendgrid"):
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
