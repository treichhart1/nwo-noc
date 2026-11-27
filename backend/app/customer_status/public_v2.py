"""Bounded public projection for WordPress; no private entity reads or writes."""

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, Request, Query, Response
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy import text
from app.core.database import get_db
from app.customers.contracts import SafeModel
from .contracts import PublicAnnouncement, public_text

router = APIRouter()
MAX_BYTES = 524288


class AreaDetails(SafeModel):
    county: str | None = Field(None, max_length=100)
    technology: Literal["unknown", "wireless", "fiber", "hybrid", "regional"] = (
        "unknown"
    )
    group_label: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=200)

    @field_validator("county", "group_label", "description")
    @classmethod
    def safe(cls, v):
        return public_text(v) if v is not None else None


class PublicArea(AreaDetails):
    id: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9-]{27}$")
    label: str = Field(min_length=3, max_length=100)
    status: str = Field(default="unknown")
    _label = field_validator("label")(public_text)


class Announcement(PublicAnnouncement):
    impact: Literal["general", "partial", "major", "degraded"] = "general"
    area_details: list[PublicArea] = Field(default_factory=list, max_length=20)
    display_status: str = "investigating"

    @field_validator("id")
    @classmethod
    def identifier(cls, v):
        from uuid import UUID

        if str(UUID(v)) != v:
            raise ValueError("Public UUID required")
        return v


DISPLAY = (
    "major_service_interruption",
    "partial_service_interruption",
    "service_disruption",
    "degraded_performance",
    "investigating",
    "maintenance_underway",
    "monitoring_recovery",
    "all_systems_operational",
    "resolved",
    "scheduled_maintenance",
)


def display(item, now):
    if item.status == "service_disruption":
        return {
            "major": "major_service_interruption",
            "partial": "partial_service_interruption",
            "degraded": "degraded_performance",
        }.get(item.impact, "service_disruption")
    # A preapproved scheduled window does not become an operational/recovery
    # announcement merely because its start time passed. Updates need approval.
    if item.starts_at and item.status == "all_systems_operational":
        return "scheduled_maintenance"
    return item.status


def snapshot(db):
    """One PostgreSQL statement gives all three views the same MVCC snapshot.

    No isolation changes, source-table permissions, row locks or writes needed.
    Budget writes must already be committed by the caller before this read.
    """
    db.execute(text("SET LOCAL statement_timeout='2000ms'"))
    result = db.execute(
        text("""
SELECT 'configuration' AS category,to_jsonb(c) AS document FROM noc_public_status.configuration c
UNION ALL
SELECT 'announcement',to_jsonb(a) FROM (SELECT * FROM noc_public_status.announcements ORDER BY updated_at DESC,identifier LIMIT 201) a
UNION ALL
SELECT 'area',to_jsonb(a) FROM (SELECT * FROM noc_public_status.areas ORDER BY identifier LIMIT 201) a
""")
    ).mappings()
    policy = None
    rows, areas = [], []

    def date(value):
        if value is None:
            return None
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if not value.tzinfo or value.utcoffset() is None:
            raise ValueError("Aware public timestamp required")
        return value

    for row in result:
        value = dict(row["document"])
        if row["category"] == "configuration":
            if policy is not None:
                raise ValueError("Duplicate public policy")
            policy = value
            for key in (
                "baseline_approved_at",
                "last_public_update",
                "last_withdrawal_at",
            ):
                policy[key] = date(policy.get(key))
        elif row["category"] == "announcement":
            value["updated_at"] = date(value["updated_at"])
            rows.append(value)
        elif row["category"] == "area":
            areas.append(value)
        else:
            raise ValueError("Unexpected public projection category")
    if len(rows) > 200 or len(areas) > 200:
        raise ValueError("Public projection exceeds bounded capacity")
    rows.sort(key=lambda row: (-row["updated_at"].timestamp(), row["identifier"]))
    areas.sort(key=lambda row: row["identifier"])
    return policy, rows, areas


def projected(db, offset, limit, now=None):
    now = now or datetime.now(timezone.utc)
    p, rows, areas = snapshot(db)
    if not p or not p["enabled"] or not p["baseline_approved_at"]:
        raise ValueError("Public status disabled")
    if len(rows) > 200 or len(areas) > 200:
        raise ValueError("Public projection exceeds bounded capacity")
    groups = {"incidents": [], "maintenance": [], "resolved": []}
    for row in rows:
        value = Announcement.model_validate(
            dict(row["payload"], id=row["identifier"], updated_at=row["updated_at"])
        )
        value.display_status = display(value, now)
        key = (
            "resolved"
            if value.status == "resolved"
            else "maintenance"
            if row["kind"] == "maintenance"
            else "incidents"
        )
        groups[key].append(value.model_dump(mode="json"))
    updated = max(
        [
            p["baseline_approved_at"],
            p["last_public_update"] or p["baseline_approved_at"],
        ]
        + [r["updated_at"] for r in rows]
    )
    if updated > now + timedelta(seconds=60):
        raise ValueError("Future public approval timestamp")
    # Withdrawal clears the current publication pointer. It is not recovery.
    # Require a separate reviewed baseline before presenting healthy status.
    review_required = (
        p.get("last_withdrawal_at")
        and p["last_withdrawal_at"] > p["baseline_approved_at"]
    )
    stale = bool(
        review_required or now - p["baseline_approved_at"] > timedelta(hours=24)
    )
    active = groups["incidents"] + groups["maintenance"]
    states = [p["baseline_status"]] + [
        r["display_status"]
        for r in active
        if r["display_status"] != "scheduled_maintenance"
    ]
    overall = "unknown" if stale else min(states, key=DISPLAY.index)
    public_areas = []
    label_counts = Counter(row["label"].casefold() for row in areas)
    for row in areas:
        a = PublicArea.model_validate(
            dict(row["details"], id=row["identifier"], label=row["label"])
        )
        related = []
        ambiguous = False
        for value in active:
            if value["display_status"] == "scheduled_maintenance":
                continue
            identifiers = {detail["id"] for detail in value["area_details"]}
            if identifiers:
                if a.id in identifiers:
                    related.append(value["display_status"])
            elif a.label.casefold() in {label.casefold() for label in value["areas"]}:
                # Pre-v2 revisions have labels only. Duplicate labels cannot
                # identify a technology/county safely; never infer healthy.
                if label_counts[a.label.casefold()] > 1:
                    ambiguous = True
                else:
                    related.append(value["display_status"])
        a.status = (
            "unknown"
            if stale or ambiguous
            else min([p["baseline_status"]] + related, key=DISPLAY.index)
        )
        public_areas.append(a.model_dump(mode="json"))
    history = groups["resolved"]
    groups["resolved"] = history[offset : offset + limit]
    payload = {
        "api_version": "2",
        "overall_status": overall,
        "freshness": "stale" if stale else "fresh",
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=90)).isoformat(),
        "last_public_update": updated.isoformat(),
        "basis": "administrator-approved announcements; not live service verification",
        "service_areas": public_areas,
        **groups,
        "next_history_offset": offset + limit
        if len(history) > offset + limit
        else None,
    }
    # Stable token spans every page and includes withdrawals, not request time.
    token = {
        "update": updated.isoformat(),
        "areas": public_areas,
        "rows": [
            dict(
                identifier=r["identifier"],
                payload=r["payload"],
                updated_at=r["updated_at"].isoformat(),
            )
            for r in rows
        ],
    }
    payload["revision_token"] = hashlib.sha256(
        json.dumps(token, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("Public response too large")
    return raw


@router.api_route("/public/network-status/v2", methods=["GET", "HEAD"])
def feed(
    request: Request,
    history_offset: int = Query(0, ge=0, le=10000),
    history_limit: int = Query(20, ge=1, le=50),
    db=Depends(get_db),
):
    from .public_app import budget

    try:
        if set(request.query_params) - {"history_offset", "history_limit"}:
            raise ValueError("Unsupported public query")
        if not budget(db, request.client.host if request.client else "unknown"):
            return JSONResponse(
                {"detail": "Public request limit reached"},
                status_code=429,
                headers={"Cache-Control": "no-store", "Retry-After": "60"},
            )
        raw = projected(db, history_offset, history_limit)
        return Response(
            b"" if request.method == "HEAD" else raw,
            media_type="application/json",
            headers={
                "Cache-Control": "public, max-age=15, must-revalidate",
                "ETag": '"' + hashlib.sha256(raw).hexdigest() + '"',
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception:
        db.rollback()
        return JSONResponse(
            {"detail": "Public status temporarily unavailable"},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )


@router.get("/ready")
def ready(db=Depends(get_db)):
    try:
        projected(db, 0, 1)
        return {
            "status": "ok",
            "service": "public-status-only",
            "projection": "available",
        }
    except Exception:
        db.rollback()
        return JSONResponse(
            {"status": "unavailable", "service": "public-status-only"},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )
