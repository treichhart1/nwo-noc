"""Batched monitoring service health and uncertainty-preserving topology context."""

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, case, or_
from app.models.entities import (
    MonitoringJob as Job,
    MonitoringAttempt as Attempt,
    MonitoringAlert as Alert,
    MonitoringWorker as Worker,
    Device,
    NetworkLink,
    AuditLog,
)
from app.services.rf_analytics_queries import utc
from app.core.config import settings
from app.services.monitoring_policy import ReliabilityPolicy


def correlate(alerts, devices, links, now):
    by_id = {d.id: d for d in devices}
    out = []
    stale_seconds = ReliabilityPolicy(**settings.rf_reliability_policy).stale_seconds

    def fresh(alert):
        try:
            observed = (
                utc(alert.updated_at)
                if alert.category == "monitoring"
                else datetime.fromisoformat(alert.evidence["observed_at"])
            )
            return (
                observed.tzinfo is not None
                and 0 <= (now - observed).total_seconds() <= stale_seconds
            )
        except (KeyError, TypeError, ValueError):
            return False

    active = {
        a.device_id: a
        for a in alerts
        if a.state == "active"
        and a.category == "rf"
        and fresh(a)
        and a.rule == "wireless_outage"
        and a.series in {"radio", "microwave"}
    }
    for a in alerts:
        parents = []
        seen = {a.device_id}
        p = by_id.get(a.device_id)
        while p and p.parent_device_id and p.parent_device_id not in seen:
            seen.add(p.parent_device_id)
            parents.append(p.parent_device_id)
            p = by_id.get(p.parent_device_id)
        current = fresh(a)
        upstream = (
            next((active[i] for i in parents if i in active), None)
            if current and a.state == "active"
            else None
        )
        incoming = [l for l in links if l.z_device_id == a.device_id]
        alternate = [
            l.id for l in incoming if upstream and l.a_device_id != upstream.device_id
        ]
        # Inventory links do not prove forwarding or working failover. Do not
        # suppress a local fault or assert a single root cause across redundancy.
        relation = (
            "multiple_paths_uncertain"
            if upstream and alternate
            else (
                "probable_upstream"
                if upstream and a.category == "rf"
                else "independent_evidence"
            )
        )
        if not current:
            relation = "insufficient_fresh_evidence"
        out.append(
            {
                "id": a.id,
                "device_id": a.device_id,
                "series": a.series,
                "rule": a.rule,
                "category": a.category,
                "severity": a.severity,
                "state": a.state if current else "unknown",
                "episode": a.episode,
                "first_seen_at": a.first_seen_at,
                "updated_at": a.updated_at,
                "evidence": a.evidence,
                "incident_id": a.incident_id,
                "correlation": relation,
                "probable_parent_alert_id": (
                    upstream.id
                    if upstream and not alternate and a.category == "rf"
                    else None
                ),
                "alternate_inventory_link_ids": alternate,
                "root_cause": "unconfirmed",
                "customer_impact": "not_estimated",
                "execution_enabled": False,
            }
        )
    return out


def health(db, *, now=None, offset=0, limit=50):
    from app.services.integration_interlock import disabled
    operator_disabled=disabled()
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    counts = db.execute(
        select(
            func.count(Job.key),
            func.count().filter(
                Job.enabled.is_(True), Job.next_due_at <= now, Job.state != "leased"
            ),
            func.count().filter(Job.enabled.is_(True), Job.state == "leased"),
            func.count().filter(
                Job.enabled.is_(True), Job.state == "leased", Job.lease_until < now
            ),
            func.count(func.distinct(Job.device_id)).filter(
                Job.enabled.is_(True),
                or_(
                    Job.last_valid_sample_at.is_(None),
                    Job.last_valid_sample_at
                    < now
                    - timedelta(
                        seconds=ReliabilityPolicy(
                            **settings.rf_reliability_policy
                        ).stale_seconds
                    ),
                ),
            ),
            func.min(Job.next_due_at).filter(
                Job.enabled.is_(True), Job.next_due_at <= now, Job.state != "leased"
            ),
        )
    ).one()
    attempts = db.execute(
        select(
            func.count(Attempt.token),
            func.count().filter(Attempt.result == "success"),
            func.count().filter(Attempt.result.not_in(("success", "running"))),
            func.avg(Attempt.duration_ms),
            func.max(Attempt.finished_at),
        ).where(Attempt.started_at >= since)
    ).one()
    delay = db.scalar(
        select(func.avg(Alert.evidence["processing_delay_ms"].as_float())).where(
            Alert.updated_at >= since
        )
    )
    jobs = list(
        db.scalars(
            select(Job)
            .order_by(Job.next_due_at, Job.key)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    alerts = list(
        db.scalars(
            select(Alert)
            .where(Alert.state != "cleared")
            .order_by(Alert.id)
            .limit(10001)
        )
    )
    devices = list(db.scalars(select(Device).order_by(Device.id).limit(10001)))
    links = list(db.scalars(select(NetworkLink).order_by(NetworkLink.id).limit(10001)))
    complete = all(len(v) <= 10000 for v in (alerts, devices, links))
    correlated = (
        correlate(alerts[:10000], devices[:10000], links[:10000], now)
        if complete
        else []
    )
    workers = list(
        db.scalars(select(Worker).order_by(Worker.heartbeat_at.desc()).limit(20))
    )
    recovery = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action.in_(
                    (
                        "monitoring.worker_started",
                        "monitoring.database_recovered",
                        "monitoring.redis_recovered",
                        "monitoring.lease_recovered",
                        "monitoring.spool_replayed",
                        "monitoring.collector_recovered",
                    )
                )
            )
            .order_by(AuditLog.id.desc())
            .limit(50)
        )
    )
    return {
        "generated_at": now,
        "operator_disabled":operator_disabled,
        "execution_enabled": False,
        "jobs_total": counts[0],
        "queue_backlog": counts[1],
        "leased_jobs": counts[2],
        "expired_leases": counts[3],
        "stale_or_missing_devices": counts[4],
        "oldest_due_delay_seconds": (
            max(0, (now - utc(counts[5])).total_seconds()) if counts[5] else 0
        ),
        "attempts_24h": attempts[0],
        "successes_24h": attempts[1],
        "failures_24h": attempts[2],
        "success_rate_24h": (
            round(attempts[1] / (attempts[1] + attempts[2]), 4)
            if attempts[1] + attempts[2]
            else None
        ),
        "average_collection_ms": (
            float(attempts[3]) if attempts[3] is not None else None
        ),
        "average_alert_processing_delay_ms": (
            float(delay) if delay is not None else None
        ),
        "last_attempt_finished_at": attempts[4],
        "topology_complete": complete,
        "alerts": correlated[:100],
        "alerts_truncated": len(correlated) > 100,
        "active_alerts": (
            sum(a["state"] == "active" for a in correlated) if complete else None
        ),
        "unknown_alerts": (
            sum(a["state"] == "unknown" for a in correlated) if complete else None
        ),
        "jobs": [
            {
                "key": j.key,
                "device_id": j.device_id,
                "kind": j.kind,
                "state": j.state if j.enabled else "disabled",
                "interval_seconds": j.interval_seconds,
                "next_due_at": j.next_due_at,
                "last_success_at": j.last_success_at,
                "last_valid_sample_at": j.last_valid_sample_at,
                "consecutive_failures": j.failures,
                "error": j.last_error,
            }
            for j in jobs[:limit]
        ],
        "has_more": len(jobs) > limit,
        "offset": offset,
        "workers": [
            {
                "id": w.id,
                "heartbeat_at": w.heartbeat_at,
                "phase": w.phase,
                "redis_state": w.redis_state,
                "spool_pending": w.spool_pending,
                "fresh": 0 <= (now - utc(w.heartbeat_at)).total_seconds() < 60,
            }
            for w in workers
        ],
        "recovery_events": [
            {
                "id": a.id,
                "action": a.action,
                "at": a.created_at,
                "job_key": a.entity_id,
                "details": a.details,
            }
            for a in recovery
        ],
        "semantics": "PostgreSQL owns jobs/receipts; Redis is a health signal. RF unknown is not an outage or recovery. Alternate inventory paths do not prove working failover.",
    }
