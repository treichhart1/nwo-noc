from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import Field
from sqlalchemy import select, func, or_
from app.core.database import get_db
from app.core.config import settings
from app.api.deps import require_admin
from app.customers.routes import customer_operator
from app.customers.contracts import SafeModel
from app.models.entities import Incident, NetworkLink, Site, AuditLog
from app.notifications.models import NotificationPlan
from app.notifications.contracts import PlanRequest, Evidence
from app.notifications.service import create_plan
from app.services.incident_workflow import apply_update
from app.api.monitoring_reliability_routes import run
from .models import OutageState, OutageEpisode, OutageTimeline, OutageObservation
from .contracts import Observation
from .engine import ingest, reconcile, clock, policy, impact, audit, lock

router = APIRouter(
    prefix="/api/down-detector",
    tags=["Down Detector"],
    dependencies=[Depends(customer_operator)],
)


@router.post("/synthetic-observations")
def observe(body: list[Observation], db=Depends(get_db), user=Depends(require_admin)):
    def work():
        if not (
            settings.offline_mode
            and settings.app_env == "staging"
            and settings.synthetic_monitoring_enabled
        ):
            raise ValueError("Explicit isolated synthetic staging required")
        if not 1 <= len(body) <= 200 or any(o.source != "synthetic" for o in body):
            raise ValueError("One to 200 synthetic observations required")
        results = [ingest(db, o) for o in body]
        result = reconcile(db)
        audit(db, "synthetic_batch", user.id, count=len(body))
        return {"results": results, **result}

    return run(db, work, True)


def episode_view(db, ep, now):
    st = db.get(OutageState, ep.key)
    return {
        "id": ep.id,
        "key": ep.key,
        "kind": st.kind,
        "target_id": st.target_id,
        "site_id": st.site_id,
        "incident_id": ep.incident_id,
        "parent_episode_id": ep.parent_id,
        "status": ep.status,
        "classification": ep.classification,
        "confidence": ep.confidence,
        "started_at": ep.started_at,
        "confirmed_at": ep.confirmed_at,
        "resolved_at": ep.resolved_at,
        "duration_seconds": max(
            0, ((ep.resolved_at or now) - ep.started_at).total_seconds()
        ),
        "recovery_state": st.state,
        "stale": now - st.last_seen > timedelta(seconds=policy().stale_seconds),
        "last_observed_at": st.last_seen,
        "evidence": ep.evidence,
        "override": bool(ep.override_reason),
    }


@router.get("")
def dashboard(
    site_id: int | None = Query(None, ge=1),
    device_id: int | None = Query(None, ge=1),
    link_id: int | None = Query(None, ge=1),
    severity: Literal["critical", "warning"] | None = None,
    outage_type: str | None = Query(None, max_length=40),
    technology: Literal["wireless", "fiber", "hybrid"] | None = None,
    status: Literal["active", "resolved"] | None = None,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(50, ge=1, le=100),
    db=Depends(get_db),
):
    def read():
        now = clock()
        q = (
            select(OutageEpisode)
            .join(OutageState, OutageState.key == OutageEpisode.key)
            .outerjoin(Incident, Incident.id == OutageEpisode.incident_id)
        )
        if site_id:
            q = q.where(OutageState.site_id == site_id)
        if device_id:
            q = q.where(
                OutageState.kind == "device", OutageState.target_id == device_id
            )
        if link_id:
            q = q.where(OutageState.kind == "link", OutageState.target_id == link_id)
        if outage_type:
            q = q.where(OutageEpisode.classification == outage_type)
        if status:
            q = q.where(OutageEpisode.status == status)
        if severity:
            q = q.where(Incident.severity == severity)
        if technology:
            from app.customers.models import CustomerService

            # Explicit service mapping; no inferred technology from equipment vendor.
            scope = select(CustomerService.site_id).where(
                CustomerService.service_type == technology,
                CustomerService.mapping_state == "verified",
            )
            q = q.where(OutageState.site_id.in_(scope))
        total = db.scalar(select(func.count()).select_from(q.subquery()))
        rows = list(
            db.scalars(
                q.order_by(OutageEpisode.started_at.desc(), OutageEpisode.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        states = list(db.scalars(select(OutageState).limit(5000)))
        fresh = [
            s
            for s in states
            if now - s.last_seen <= timedelta(seconds=policy().stale_seconds)
        ]
        sites = list(db.scalars(select(Site).limit(1000)))
        links = list(db.scalars(select(NetworkLink).limit(5000)))
        site_states = {s.target_id: s.state for s in fresh if s.kind == "site"}
        return {
            "generated_at": now,
            "enabled": settings.down_detector_enabled,
            "read_only": True,
            "external_calls": 0,
            "total": total,
            "offset": offset,
            "items": [episode_view(db, e, now) for e in rows],
            "counts": {
                "sites": len(sites),
                "online_sites": sum(v == "up" for v in site_states.values()),
                "offline_sites": sum(v == "down" for v in site_states.values()),
                "unknown_sites": len(sites)
                - sum(v in {"up", "down"} for v in site_states.values()),
                "stale_components": len(states) - len(fresh),
                "degraded_components": sum(s.state == "degraded" for s in fresh),
                "active_critical_incidents": db.scalar(
                    select(func.count(Incident.id)).where(
                        Incident.severity == "critical",
                        Incident.status.not_in(["resolved", "closed"]),
                    )
                ),
            },
            "dependencies": [
                {
                    "link_id": l.id,
                    "a_site_id": l.a_site_id,
                    "z_site_id": l.z_site_id,
                    "technology": l.link_type,
                    "inventory_state": l.status,
                }
                for l in links
            ],
            "policy": policy().model_dump(),
        }

    return run(db, read)


@router.get("/episodes/{ident}")
def detail(ident: int, before: int | None = Query(None, ge=1), db=Depends(get_db)):
    def read():
        ep = db.get(OutageEpisode, ident)
        if not ep:
            raise LookupError()
        q = select(OutageTimeline).where(OutageTimeline.episode_id == ident)
        if before:
            q = q.where(OutageTimeline.id < before)
        timeline = list(db.scalars(q.order_by(OutageTimeline.id.desc()).limit(100)))
        st = db.get(OutageState, ep.key)
        recommendations = []
        for signal, action in [
            ("microwave", "Check recorded microwave carrier and Ethernet history"),
            ("ospf", "Inspect read-only OSPF neighbor history"),
            ("commercial_power", "Review commercial power and UPS evidence"),
            ("collector", "Inspect collector heartbeat before diagnosing equipment"),
            ("wireless", "Review access-sector radio history"),
        ]:
            if st.evidence.get("signals", {}).get(signal) is False:
                recommendations.append(
                    {
                        "action": action,
                        "evidence_event_id": st.evidence["event_id"],
                        "signal": signal,
                    }
                )
        if st.evidence.get("reboot_detected"):
            recommendations.append(
                {
                    "action": "Review observed uptime decrease; reset or reboot cause unconfirmed",
                    "evidence_event_id": st.evidence["event_id"],
                }
            )
        incident_events = []
        notification_events = []
        if ep.incident_id:
            incident_events = [
                {"at": a.created_at, "action": a.action, "actor_id": a.user_id}
                for a in db.scalars(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "incident",
                        AuditLog.entity_id == str(ep.incident_id),
                    )
                    .order_by(AuditLog.id.desc())
                    .limit(50)
                )
            ]
            plans = list(
                db.scalars(
                    select(NotificationPlan.id)
                    .where(NotificationPlan.incident_id == ep.incident_id)
                    .limit(100)
                )
            )
            if plans:
                notification_events = [
                    {
                        "at": a.created_at,
                        "action": a.action,
                        "actor_id": a.user_id,
                        "plan_id": a.entity_id,
                    }
                    for a in db.scalars(
                        select(AuditLog)
                        .where(
                            AuditLog.entity_type == "notification",
                            AuditLog.entity_id.in_([str(i) for i in plans]),
                        )
                        .order_by(AuditLog.id.desc())
                        .limit(50)
                    )
                ]
        pending = db.scalar(
            select(func.count(OutageEpisode.id)).where(
                OutageEpisode.parent_id == ep.id, OutageEpisode.status == "active"
            )
        )
        return {
            **episode_view(db, ep, clock()),
            "pending_downstream_recoveries": pending,
            "linked_incident_events": incident_events,
            "notification_events": notification_events,
            "impact": impact(db, ep),
            "recommendations": recommendations,
            "timeline": [
                {
                    "id": t.id,
                    "at": t.at,
                    "action": t.action,
                    "actor_id": t.actor_id,
                    "evidence": t.evidence,
                }
                for t in timeline
            ],
            "timeline_next": timeline[-1].id if len(timeline) == 100 else None,
            "raw_event_reference": st.evidence.get("event_id"),
            "execution_enabled": False,
        }

    return run(db, read)


class OperatorAction(SafeModel):
    action: Literal["acknowledge", "override"]
    explanation: str = Field(min_length=10, max_length=500)
    classification: (
        Literal[
            "unknown_tower_connectivity_loss",
            "suspected_power_failure",
            "backhaul_outage",
            "device_offline",
            "management_connectivity_failure",
        ]
        | None
    ) = None


@router.post("/episodes/{ident}/operator-action")
def operator_action(
    ident: int,
    body: OperatorAction,
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    def work():
        lock(db)
        ep = db.get(OutageEpisode, ident)
        if not ep:
            raise LookupError()
        from app.core.operator_text import clean_operator_text

        explanation = clean_operator_text(body.explanation)
        if not explanation or len(explanation) < 10:
            raise ValueError("Meaningful operator explanation required")
        if body.action == "override":
            if not body.classification:
                raise ValueError("Explicit classification required")
            ep.classification = body.classification
            ep.confidence = "operator_assessment"
            ep.override_reason = explanation
            if ep.incident_id and not ep.parent_id:
                inc = db.get(Incident, ep.incident_id)
                inc.root_cause = ep.classification
                inc.revision += 1
        elif ep.incident_id:
            inc = db.get(Incident, ep.incident_id)
            if inc.status in {"resolved", "closed"}:
                raise ValueError("Resolved incident cannot be acknowledged")
            apply_update(db, inc, {"status": "acknowledged"}, user_id=user.id)
        audit(
            db,
            body.action,
            user.id,
            ep,
            explanation=explanation,
            classification=ep.classification,
        )
        return {"id": ep.id, "classification": ep.classification}

    return run(db, work, True)


class Preview(SafeModel):
    kind: Literal["outage", "restoration"] = "outage"


@router.post("/episodes/{ident}/notification-preview")
def notification(
    ident: int, body: Preview, db=Depends(get_db), user=Depends(customer_operator)
):
    def work():
        lock(db)
        ep = db.get(OutageEpisode, ident)
        if not ep or not ep.incident_id:
            raise LookupError()
        if ep.parent_id:
            raise ValueError(
                "Prepare one primary-incident preview instead of downstream duplicates"
            )
        st = db.get(OutageState, ep.key)
        signals = st.evidence.get("signals", {})
        verified = st.state == "down" and ep.confidence == "probable" and now_fresh(st)
        event = (
            "backhaul_outage"
            if st.kind == "link"
            else (
                "sector_outage"
                if st.kind == "sector"
                else "equipment_failure" if st.kind == "device" else "tower_outage"
            )
        )
        if ep.classification == "fiber_outage":
            event = "fiber_outage"
        elif ep.classification == "collector_failure":
            event = "monitoring_failure"
        if ep.classification == "confirmed_commercial_power_outage":
            event = (
                "tower_power"
                if sum(
                    signals.get(k) is False
                    for k in [
                        "icmp",
                        "snmp",
                        "telemetry",
                        "interface",
                        "wireless",
                        "microwave",
                        "fiber",
                    ]
                )
                >= policy().independent_signals
                else "ups_warning"
            )
            verified = event == "tower_power" and now_fresh(st)
        if body.kind == "restoration":
            inc = db.get(Incident, ep.incident_id)
            if (
                ep.status != "resolved"
                or inc.status not in {"resolved", "closed"}
                or st.state != "up"
                or not now_fresh(st)
            ):
                raise ValueError("Stable primary and downstream recovery required")
            event = "restoration"
            verified = True
        p = create_plan(
            db,
            PlanRequest(
                incident_id=ep.incident_id,
                kind=body.kind,
                evidence=Evidence(
                    reference=f"DETECTOR:{ep.id}",
                    event_type=event,
                    confirmed=verified,
                    recovery_verified=body.kind == "restoration",
                    recovery_samples=(
                        min(st.good_count, 100) if body.kind == "restoration" else 0
                    ),
                    verified_at=st.last_seen if body.kind == "restoration" else None,
                ),
            ),
            user.id,
        )
        audit(db, "notification_preview", user.id, ep, plan_id=p.id)
        return {"plan_id": p.id, "approval_required": True, "simulation_only": True}

    return run(db, work, True)


def now_fresh(st):
    return clock() - st.last_seen <= timedelta(seconds=policy().stale_seconds)


@router.get("/history")
def history(days: int = Query(30, ge=1, le=90), db=Depends(get_db)):
    def read():
        now = clock()
        cutoff = now - timedelta(days=days)
        rows = list(
            db.scalars(
                select(OutageEpisode)
                .where(OutageEpisode.started_at >= cutoff)
                .limit(10001)
            )
        )
        if len(rows) > 10000:
            raise ValueError("Narrow history interval; bounded report exceeded")
        resolved = [e for e in rows if e.resolved_at and not e.parent_id]
        return {
            "window_days": days,
            "confirmed_episodes": len(rows),
            "primary_outages": sum(not e.parent_id for e in rows),
            "mttr_seconds": (
                sum((e.resolved_at - e.started_at).total_seconds() for e in resolved)
                / len(resolved)
                if resolved
                else None
            ),
            "availability_percent": None,
            "availability_reason": "No continuous independently verified service coverage; unknown intervals are not uptime",
            "by_component": [
                {
                    "key": k,
                    "episodes": sum(e.key == k for e in rows),
                    "measured_completed_duration_seconds": sum(
                        (e.resolved_at - e.started_at).total_seconds()
                        for e in rows
                        if e.key == k and e.resolved_at
                    ),
                }
                for k in sorted({e.key for e in rows})
            ],
        }

    return run(db, read)


@router.get("/episodes/{ident}/observations")
def observations(
    ident: int, before: int | None = Query(None, ge=1), db=Depends(get_db)
):
    def read():
        ep = db.get(OutageEpisode, ident)
        if not ep:
            raise LookupError()
        q = select(OutageObservation).where(
            OutageObservation.key == ep.key,
            OutageObservation.observed_at >= ep.started_at,
        )
        if ep.resolved_at:
            q = q.where(OutageObservation.observed_at <= ep.resolved_at)
        if before:
            q = q.where(OutageObservation.id < before)
        rows = list(db.scalars(q.order_by(OutageObservation.id.desc()).limit(100)))
        return {
            "items": [
                {"id": r.id, "received_at": r.received_at, "observation": r.data}
                for r in rows
            ],
            "next": rows[-1].id if len(rows) == 100 else None,
        }

    return run(db, read)
