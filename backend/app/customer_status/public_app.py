"""Standalone read-only status boundary. Never include private application routers."""

import hashlib, hmac, json
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, Depends, Query, Response
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from app.core.database import get_db
from app.core.config import settings
from app.analytics.service import private_secret
from .models import PublicBudget
from .contracts import PublicFeed, PublicAnnouncement

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security(request, call_next):
    if request.method not in {"GET", "HEAD"}:
        return JSONResponse(
            {"detail": "Read-only public status interface"},
            status_code=405,
            headers={"Allow": "GET, HEAD", "Cache-Control": "no-store"},
        )
    try:
        response = await call_next(request)
    except Exception:
        response = JSONResponse(
            {
                "detail": "Public status temporarily unavailable; contact support through the usual channels"
            },
            status_code=503,
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if response.status_code >= 400:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
def invalid(request, exc):
    return JSONResponse(
        {"detail": "Invalid public status query"},
        status_code=422,
        headers={"Cache-Control": "no-store"},
    )


def budget(db, client):
    now = datetime.now(timezone.utc)
    minute = now.replace(second=0, microsecond=0)
    key = hmac.new(
        private_secret(settings.customer_status_rate_key_file).encode(),
        client.encode(),
        hashlib.sha256,
    ).hexdigest()
    for key, cap in [("global", 1200), (key, 60)]:
        stmt = (
            insert(PublicBudget)
            .values(key=key, minute=minute, count=1)
            .on_conflict_do_update(
                index_elements=[PublicBudget.key, PublicBudget.minute],
                set_={
                    "count": __import__("sqlalchemy").func.least(
                        PublicBudget.count + 1, cap + 1
                    )
                },
            )
            .returning(PublicBudget.count)
        )
        if db.scalar(stmt) > cap:
            db.commit()
            return False
    db.execute(
        text(
            "DELETE FROM customer_status_public_budgets WHERE ctid IN (SELECT ctid FROM customer_status_public_budgets WHERE minute<:cutoff LIMIT 1000)"
        ),
        {"cutoff": minute - timedelta(minutes=2)},
    )
    db.commit()
    return True


@app.get("/health")
def health():
    return {"status": "ok", "service": "approved-status-only gateway"}


@app.api_route("/public/network-status/v1", methods=["GET", "HEAD"])
def current(
    request: Request,
    history_offset: int = Query(0, ge=0, le=10000),
    history_limit: int = Query(20, ge=1, le=50),
    db=Depends(get_db),
):
    try:
        if set(request.query_params) - {"history_offset", "history_limit"}:
            return JSONResponse({"detail": "Unknown public query"}, status_code=422)
        if not budget(db, request.client.host if request.client else "unknown"):
            return JSONResponse(
                {"detail": "Public status request limit reached"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        db.execute(text("SET LOCAL statement_timeout='2000ms'"))
        pol = (
            db.execute(text("SELECT * FROM noc_public_status.configuration"))
            .mappings()
            .first()
        )
        if not pol or not pol["enabled"] or not pol["baseline_approved_at"]:
            return JSONResponse(
                {"detail": "Public status is not enabled"}, status_code=503
            )
        rows = list(
            db.execute(
                text(
                    "SELECT * FROM noc_public_status.announcements ORDER BY updated_at DESC LIMIT 201"
                )
            ).mappings()
        )
        if len(rows) > 200:
            raise ValueError("Public projection exceeds reviewed limit")
        active = []
        maintenance = []
        history = []
        for r in rows:
            item = PublicAnnouncement.model_validate(
                dict(
                    {
                        k: v
                        for k, v in r["payload"].items()
                        if k not in {"impact", "area_details"}
                    },
                    id=r["identifier"],
                    updated_at=r["updated_at"],
                )
            )
            if item.status == "resolved":
                history.append(item)
            elif r["kind"] == "maintenance":
                maintenance.append(item)
            else:
                active.append(item)
        order = [
            "service_disruption",
            "investigating",
            "maintenance_underway",
            "monitoring_recovery",
            "all_systems_operational",
            "resolved",
        ]
        states = [pol["baseline_status"]] + [i.status for i in active + maintenance]
        overall = min(states, key=order.index)
        update = max(
            [
                pol["baseline_approved_at"],
                pol["last_public_update"] or pol["baseline_approved_at"],
            ]
            + [r["updated_at"] for r in rows]
        )
        now = datetime.now(timezone.utc)
        if update > now + timedelta(seconds=60) or now - update > timedelta(hours=24):
            raise ValueError("Approved public information is stale or future-dated")
        feed = PublicFeed(
            overall_status=overall,
            last_public_update=update,
            incidents=active,
            maintenance=maintenance,
            resolved=history[history_offset : history_offset + history_limit],
            next_history_offset=(
                history_offset + history_limit
                if len(history) > history_offset + history_limit
                else None
            ),
        )
        data = feed.model_dump(mode="json")
        raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
        if len(raw.encode()) > 524288:
            raise ValueError("Public response too large")
        etag = '"' + hashlib.sha256(raw.encode()).hexdigest() + '"'
        headers = {
            "Cache-Control": "public, max-age=30, must-revalidate",
            "ETag": etag,
            "Vary": "Accept-Encoding",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(
            "" if request.method == "HEAD" else raw,
            media_type="application/json",
            headers=headers,
        )
    except Exception:
        db.rollback()
        return JSONResponse(
            {"detail": "Public status temporarily unavailable"},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )


from .public_v2 import router as v2_router

app.include_router(v2_router)
