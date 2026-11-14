from datetime import timedelta, datetime
from typing import Literal
from fastapi import APIRouter, Depends, Query, Body
from pydantic import Field
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.config import settings
from app.api.deps import require_admin
from app.customers.routes import customer_operator
from app.customers.contracts import SafeModel
from app.models.entities import Site, Device, Incident
from app.services.incident_workflow import apply_update
from app.core.operator_text import clean_operator_text
from app.api.monitoring_reliability_routes import run
from .contracts import Observation, Policy
from .models import TowerCurrent, TowerSample, TowerFinding, TowerNote, TowerPolicyRow
from .service import (
    clock,
    policy,
    ingest,
    assess,
    audit,
    lock,
    trend_report,
    prune,
    finding_view,
)

router = APIRouter(
    prefix="/api/tower-health",
    tags=["Tower health and capacity"],
    dependencies=[Depends(customer_operator)],
)


@router.get("")
def fleet(
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(50, ge=1, le=100),
    db=Depends(get_db),
):
    def read():
        now = clock()
        p = policy(db)
        total = db.scalar(select(func.count(Site.id)))
        rows = list(
            db.execute(
                select(Site, TowerCurrent)
                .outerjoin(TowerCurrent, TowerCurrent.site_id == Site.id)
                .order_by(Site.id)
                .offset(offset)
                .limit(limit)
            )
        )
        items = []
        for site, current in rows:
            stale = (
                not current
                or (
                    current.report.get("data_expires_at") is not None
                    and now > datetime.fromisoformat(current.report["data_expires_at"])
                )
                or now - current.assessed_at > timedelta(seconds=p.max_age_seconds)
            )
            r = current.report if current else {}
            items.append(
                {
                    "site_id": site.id,
                    "name": site.name,
                    "state": "stale" if stale else r["state"],
                    "assessed_at": current.assessed_at if current else None,
                    "known_metric_score": (
                        None if stale else r.get("known_metric_score")
                    ),
                    "coverage": r.get("coverage"),
                    "customers": r.get("customers"),
                    "active_findings": len(r.get("findings", [])),
                    "peak_observed_utilization_percent": (
                        max(
                            (
                                v["capacity"]["observed_utilization_percent"]
                                for v in r.get("resources", [])
                                if v["capacity"]["observed_utilization_percent"]
                                is not None
                            ),
                            default=None,
                        )
                        if not stale
                        else None
                    ),
                    "customer_source_ready": r.get("customer_source_ready", False),
                }
            )
        return {
            "total": total,
            "items": items,
            "offset": offset,
            "generated_at": now,
            "worker_enabled": settings.tower_health_enabled,
            "read_only_network": True,
            "automatic_actions_enabled": False,
            "external_delivery_enabled": False,
            "policy": p.model_dump(),
        }

    return run(db, read)


@router.get("/sites/{site_id}")
def detail(site_id: int, db=Depends(get_db)):
    def read():
        rows = assess(db, site_ids=[site_id])
        if not rows:
            raise LookupError()
        return rows[0]

    return run(db, read)


class Refresh(SafeModel):
    site_ids: list[int] | None = Field(None, max_length=40)


@router.post("/refresh")
def refresh(body: Refresh, db=Depends(get_db), user=Depends(require_admin)):
    def work():
        if body.site_ids is not None and (
            not body.site_ids or any(i < 1 for i in body.site_ids)
        ):
            raise ValueError("Positive registered tower identifiers required")
        rows = assess(db, site_ids=body.site_ids, persist=True)
        audit(db, "refresh", user.id, sites=len(rows))
        return {"assessed_sites": len(rows), "external_calls": 0}

    return run(db, work, True)


@router.post("/synthetic-observations")
def synthetic(
    body: list[Observation] = Body(min_length=1, max_length=200),
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def work():
        if not (
            settings.offline_mode
            and settings.app_env == "staging"
            and settings.synthetic_monitoring_enabled
        ):
            raise ValueError("Explicit isolated synthetic staging required")
        if not 1 <= len(body) <= 200 or any(o.source != "synthetic" for o in body):
            raise ValueError("One to200 synthetic observations required")
        result = [ingest(db, o) for o in body]
        reports = assess(db, site_ids=sorted({o.site_id for o in body}), persist=True)
        from app.down_detector.engine import reconcile

        reconcile(db)
        audit(db, "synthetic_ingest", user.id, samples=len(body))
        return {"results": result, "sites_assessed": len(reports), "external_calls": 0}

    return run(db, work, True)


@router.put("/policy")
def set_policy(body: Policy, db=Depends(get_db), user=Depends(require_admin)):
    def work():
        lock(db)
        for ident in body.device_voltage_thresholds:
            if not db.get(Device, ident):
                raise ValueError("Voltage thresholds require a registered device")
        row = db.get(TowerPolicyRow, 1)
        if not row:
            row = TowerPolicyRow(
                id=1, config=body.model_dump(mode="json"), updated_by=user.id
            )
            db.add(row)
        else:
            row.config = body.model_dump(mode="json")
            row.updated_by = user.id
            row.updated_at = clock()
        audit(
            db,
            "policy_updated",
            user.id,
            voltage_profiles=len(body.device_voltage_thresholds),
        )
        return {"policy": body.model_dump(), "equipment_changes": 0}

    return run(db, work, True)


@router.get("/sites/{site_id}/history")
def history(
    site_id: int,
    scope_key: str = Query(
        pattern=r"^(device|sector|link):[1-9][0-9]*$", max_length=80
    ),
    days: int = Query(30, ge=30, le=90),
    db=Depends(get_db),
):
    def read():
        if not db.get(Site, site_id):
            raise LookupError()
        return trend_report(db, site_id, scope_key, days)

    return run(db, read)


@router.get("/sites/{site_id}/observations")
def observations(
    site_id: int, before: int | None = Query(None, ge=1), db=Depends(get_db)
):
    def read():
        if not db.get(Site, site_id):
            raise LookupError()
        q = select(TowerSample).where(TowerSample.site_id == site_id)
        if before:
            q = q.where(TowerSample.id < before)
        rows = list(db.scalars(q.order_by(TowerSample.id.desc()).limit(100)))
        return {
            "items": [
                {
                    "id": r.id,
                    "event_id": r.event_id,
                    "observed_at": r.observed_at,
                    "source": r.source,
                    "scope_key": r.scope_key,
                    "data": r.data,
                }
                for r in rows
            ],
            "next": rows[-1].id if len(rows) == 100 else None,
        }

    return run(db, read)


@router.get("/findings/{ident}")
def finding(ident: int, before: int | None = Query(None, ge=1), db=Depends(get_db)):
    def read():
        f = db.get(TowerFinding, ident)
        if not f:
            raise LookupError()
        q = select(TowerNote).where(TowerNote.finding_id == ident)
        if before:
            q = q.where(TowerNote.id < before)
        rows = list(db.scalars(q.order_by(TowerNote.id.desc()).limit(100)))
        return {
            **finding_view(f, clock(), policy(db)),
            "notes": [
                {
                    "id": r.id,
                    "actor_id": r.actor_id,
                    "at": r.at,
                    "action": r.action,
                    "text": r.text,
                }
                for r in rows
            ],
            "next": rows[-1].id if len(rows) == 100 else None,
        }

    return run(db, read)


class Action(SafeModel):
    action: Literal["acknowledge", "close_by_operator", "note"]
    explanation: str = Field(min_length=10, max_length=1000)


@router.post("/findings/{ident}/actions")
def action(
    ident: int, body: Action, db=Depends(get_db), user=Depends(customer_operator)
):
    def work():
        lock(db)
        f = db.get(TowerFinding, ident)
        if not f:
            raise LookupError()
        note = clean_operator_text(body.explanation)
        if not note or len(note) < 10:
            raise ValueError("Meaningful technician explanation required")
        if body.action == "acknowledge":
            if f.state not in {"active", "acknowledged"}:
                raise ValueError("Only active findings can be acknowledged")
            f.state = "acknowledged"
        if body.action == "close_by_operator":
            f.state = "closed_by_operator"
            f.cleared_at = clock()
            if f.incident_id:
                inc = db.get(Incident, f.incident_id)
                if inc and inc.status not in {"resolved", "closed"}:
                    apply_update(
                        db,
                        inc,
                        {"status": "resolved", "resolution": note},
                        user_id=user.id,
                    )

        db.add(
            TowerNote(finding_id=f.id, actor_id=user.id, action=body.action, text=note)
        )
        audit(db, body.action, user.id, finding=f)
        return {"id": f.id, "state": f.state, "physical_recovery_claimed": False}

    return run(db, work, True)


@router.post("/findings/{ident}/technician-preview")
def technician(ident: int, db=Depends(get_db), user=Depends(customer_operator)):
    def work():
        lock(db)
        f = db.get(TowerFinding, ident)
        if not f or f.state not in {"active", "acknowledged"}:
            raise ValueError("Active finding required")
        p = policy(db)
        if not f.evidence.get("observed_at") or clock() - datetime.fromisoformat(
            f.evidence["observed_at"]
        ) > timedelta(seconds=p.max_age_seconds):
            raise ValueError("Fresh verified finding evidence required")
        if not f.incident_id:
            inc = Incident(
                title=f"Tower health investigation ({f.scope_key}, {f.rule})",
                site_id=f.site_id,
                device_id=f.device_id,
                link_id=(
                    int(f.scope_key.split(":")[1])
                    if f.scope_key.startswith("link:")
                    else None
                ),
                sector_id=(
                    int(f.scope_key.split(":")[1])
                    if f.scope_key.startswith("sector:")
                    else None
                ),
                severity="critical" if f.priority == "critical" else "warning",
                root_cause=f.rule,
                summary="Internal equipment/capacity warning; customer outage is not established",
                started_at=f.first_seen,
            )
            db.add(inc)
            db.flush()
            f.incident_id = inc.id
        from app.notifications.service import create_plan
        from app.notifications.contracts import PlanRequest, Evidence

        plan = create_plan(
            db,
            PlanRequest(
                incident_id=f.incident_id,
                evidence=Evidence(
                    reference=f"TOWER-HEALTH:{f.id}",
                    event_type="monitoring_failure",
                    confirmed=False,
                ),
            ),
            user.id,
        )
        audit(db, "technician_preview", user.id, finding=f, plan_id=plan.id)
        return {
            "incident_id": f.incident_id,
            "plan_id": plan.id,
            "approval_required": True,
            "simulation_only": True,
            "customer_outage_confirmed": False,
        }

    return run(db, work, True)


class Retention(SafeModel):
    confirm: bool = Field(strict=True)


@router.post("/retention")
def retention(body: Retention, db=Depends(get_db), user=Depends(require_admin)):
    def work():
        if not body.confirm:
            raise ValueError("Explicit derived-history retention confirmation required")
        result = prune(db)
        audit(db, "retention_requested", user.id, counts=result)
        return result

    return run(db, work, True)
