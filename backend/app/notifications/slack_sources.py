"""Build Slack evidence solely from existing records; never export free-form PII."""

from datetime import timedelta
import math
from sqlalchemy import select
from app.models.entities import (
    Incident,
    Device,
    NetworkLink,
    MonitoringAlert,
    MonitoringWorker,
    AuditLog,
)
from app.field.models import WorkOrder
from app.maintenance_ops.models import Schedule
from app.signup.models import Application, AppWork
from app.tower_health.models import TowerFinding
from app.notifications.models import NotificationPlan
from app.notifications.service import utc, clock, digest
from app.notifications.slack_contracts import EVENTS

RULES = {
    "temperature_high": "temperature",
    "temperature_critical": "temperature",
    "commercial_power_lost": "power_failure",
    "low_dc_voltage": "low_battery",
    "low_battery_voltage": "low_battery",
    "rf_degraded": "rf_degradation",
    "low_rssi": "rf_degradation",
    "management_unreachable": "tower_outage",
    "interface_down": "backhaul_outage",
    "ups_warning": "low_battery",
    "low_signal": "rf_degradation",
    "low_snr": "rf_degradation",
    "low_capacity": "congestion",
    "high_utilization": "congestion",
    "overheating": "temperature",
    "wireless_outage": "sector_outage",
    "ethernet_failure": "backhaul_outage",
    "rising_noise": "suspected_interference",
    "collector_failure": "collector_failure",
    "collection_failure": "collector_failure",
    "stale": "collector_failure",
    "power_loss": "power_failure",
    "ac_loss": "power_failure",
    "low_voltage": "low_battery",
    "battery_low": "low_battery",
    "ospf_adjacency": "ospf_failure",
    "ospf_down": "ospf_failure",
}
METRICS = {
    "rssi_dbm": (-150, 0),
    "signal": (-150, 0),
    "snr_db": (-20, 100),
    "snr": (-20, 100),
    "temperature_c": (-80, 150),
    "temperature": (-80, 150),
    "voltage": (0, 1000),
    "battery_voltage": (0, 1000),
    "utilization": (0, 100),
    "cpu_percent": (0, 100),
    "capacity_mbps": (0, 1000000),
}


def metrics(value):
    if not isinstance(value, dict):
        return {}
    return {
        k: v
        for k, v in value.items()
        if k in METRICS
        and not isinstance(v, bool)
        and isinstance(v, (float, int))
        and math.isfinite(v)
        and METRICS[k][0] <= v <= METRICS[k][1]
    }


def snapshot(db, kind, identifier, now=None):
    now = now or clock()
    base = {
        "source_kind": kind,
        "source_id": str(identifier),
        "severity": "warning",
        "event": "incident_update",
        "incident_id": None,
        "site_id": None,
        "device_id": None,
        "link_id": None,
        "sector_id": None,
        "work_order_id": None,
        "technician_id": None,
        "customer_impact_count": None,
        "duration_seconds": None,
        "metrics": {},
        "confidence": "Recorded source; physical cause not confirmed",
        "safe_steps": "Review current monitoring evidence in the private NOC; request qualified technician investigation. Do not change equipment automatically.",
    }
    row = None
    if kind == "incident":
        row = db.get(Incident, int(identifier))
        if row:
            d = db.get(Device, row.device_id) if row.device_id else None
            l = db.get(NetworkLink, row.link_id) if row.link_id else None
            event = (
                "core_outage"
                if d and d.role in {"core", "core_router"}
                else "fiber_outage"
                if l and l.link_type == "fiber"
                else "backhaul_outage"
                if l
                else "sector_outage"
                if row.sector_id
                else "tower_outage"
            )
            if row.status == "closed":
                event = "incident_closed"
            elif row.status == "resolved" and row.resolved_at:
                event = "incident_update"  # restoration requires the canonical verified recovery plan
            base.update(
                event=event,
                severity=row.severity,
                incident_id=row.id,
                site_id=row.site_id,
                device_id=row.device_id,
                sector_id=row.sector_id,
                link_id=row.link_id,
                technician_id=row.assigned_to_user_id,
                customer_impact_count=row.customer_impact
                if isinstance(row.customer_impact, int) and row.customer_impact >= 0
                else None,
            )
            version = [
                row.revision,
                row.status,
                row.assigned_to_user_id,
                row.acknowledged_at.isoformat() if row.acknowledged_at else None,
            ]
            started = row.started_at
            observed = row.resolved_at or row.acknowledged_at
            if observed:
                base["duration_seconds"] = max(
                    0, int((utc(observed) - utc(started)).total_seconds())
                )
    elif kind in {"monitoring", "tower"}:
        row = db.get(
            MonitoringAlert if kind == "monitoring" else TowerFinding, int(identifier)
        )
        if row:
            event = RULES.get(row.rule)
            if not event:
                raise ValueError("No verified Slack mapping for this telemetry rule")
            cleared = row.state == "cleared" and row.cleared_at is not None
            if cleared:
                event = "recovery"
            d = db.get(Device, row.device_id)
            base.update(
                event=event,
                severity=(
                    row.severity
                    if kind == "monitoring"
                    else "critical"
                    if row.priority in {"critical", "urgent"}
                    else "warning"
                ),
                incident_id=row.incident_id,
                device_id=row.device_id,
                site_id=(d.wisp_site_id if d else None)
                if kind == "monitoring"
                else row.site_id,
                metrics=metrics(row.evidence),
            )
            version = [
                row.state,
                utc(
                    row.updated_at if kind == "monitoring" else row.last_seen
                ).isoformat(),
                row.rule,
            ]
            started = row.first_seen_at if kind == "monitoring" else row.first_seen
    elif kind == "work_order":
        row = db.get(WorkOrder, int(identifier))
        if row:
            install = db.scalar(
                select(AppWork.id).where(AppWork.work_order_id == row.id)
            )
            base.update(
                event="installation" if install else "technician_assignment",
                work_order_id=row.id,
                site_id=row.site_id,
                incident_id=row.incident_id,
                technician_id=row.technician_id,
                severity="critical"
                if row.priority in {"critical", "urgent"}
                else "info",
            )
            version = [
                row.revision,
                row.status,
                row.technician_id,
                row.scheduled_at.isoformat() if row.scheduled_at else None,
            ]
            started = row.created_at
    elif kind == "maintenance":
        row = db.get(Schedule, int(identifier))
        if row:
            base.update(
                event="overdue_maintenance"
                if row.active and utc(row.next_due_at) < now
                else "maintenance",
                site_id=row.site_id,
                technician_id=row.technician_id,
                severity="warning" if utc(row.next_due_at) < now else "info",
            )
            version = [
                row.revision,
                row.active,
                utc(row.next_due_at).isoformat(),
                base["event"],
            ]
            started = row.next_due_at
    elif kind == "signup":
        row = db.get(Application, int(identifier))
        if row:
            base.update(
                event="signup",
                site_id=row.proposed_site_id,
                technician_id=row.assigned_to,
                severity="info",
            )
            version = [row.revision, row.state, row.assigned_to]
            started = row.created_at
    elif kind == "worker":
        row = db.get(MonitoringWorker, str(identifier))
        if row:
            base.update(
                event="application_health",
                severity="critical"
                if utc(row.heartbeat_at) < now - timedelta(minutes=5)
                else "info",
            )
            version = [utc(row.heartbeat_at).isoformat(), row.phase, base["severity"]]
            started = row.heartbeat_at
    elif kind == "audit":
        row = db.get(AuditLog, int(identifier))
        if row:
            allowed = {
                "backup_failed": "backup_failure",
                "recovery_failed": "recovery_failure",
                "host.backup_failed": "backup_failure",
                "host.recovery_failed": "recovery_failure",
            }
            if row.action not in allowed:
                raise ValueError("No supported verified operational audit event")
            base.update(event=allowed[row.action], severity="critical")
            version = [row.id, row.action]
            started = row.created_at
    elif kind == "plan":
        row = db.get(NotificationPlan, int(identifier))
        if row:
            if not row.incident_id and not row.maintenance_id:
                raise ValueError("Unsupported legacy plan source")
            if row.incident_id:
                base = snapshot(db, "incident", str(row.incident_id), now)
            else:
                from app.notifications.models import NotificationMaintenance

                m = db.get(NotificationMaintenance, row.maintenance_id)
                if not m:
                    raise LookupError()
                base.update(
                    event="maintenance",
                    site_id=m.target.get("site_id"),
                    severity="info",
                )
            event = (row.snapshot.get("request", {}).get("evidence") or {}).get(
                "event_type"
            )
            mapping = {
                "tower_power": "power_failure",
                "ups_warning": "low_battery",
                "severe_degradation": "rf_degradation",
                "high_utilization": "congestion",
                "monitoring_failure": "collector_failure",
                "backhaul_outage": "backhaul_outage",
                "fiber_outage": "fiber_outage",
                "sector_outage": "sector_outage",
            }
            if row.kind == "restoration":
                evidence = row.snapshot.get("request", {}).get("evidence", {})
                if (
                    not evidence.get("recovery_verified")
                    or evidence.get("recovery_samples", 0) < 3
                ):
                    raise ValueError("Verified recovery evidence required")
                base["event"] = "recovery"
            elif event in mapping:
                base["event"] = mapping[event]
            base.update(
                source_kind="plan", source_id=str(row.id), severity=row.severity
            )
            version = [row.fingerprint, row.status, base.get("source_revision")]
            started = row.created_at
    else:
        raise ValueError("Unsupported event source")
    if row is None:
        raise LookupError()
    if base["event"] not in EVENTS or base["severity"] not in {
        "info",
        "warning",
        "critical",
    }:
        raise ValueError("Source event/severity unavailable")
    base["detected_at"] = utc(started).isoformat()
    base["source_revision"] = digest(version)
    # Never copy titles/names, descriptions, notes, customer contact, addresses,
    # raw root cause strings, arbitrary evidence or host file paths to Slack.
    return base


def payload(fields, config, *, delivery_id, thread_ts=None):
    names = {
        "incident_id": "Incident",
        "site_id": "Site",
        "device_id": "Device",
        "link_id": "Link",
        "sector_id": "Sector",
        "work_order_id": "Work order",
        "technician_id": "Assigned NOC user",
    }
    lines = ["AI NOC " + fields["event"].replace("_", " ") + " — " + fields["severity"]]
    lines += [
        label + " #" + str(fields[k])
        for k, label in names.items()
        if fields.get(k) is not None
    ]
    lines += ["Detected " + fields["detected_at"], fields["confidence"]]
    lines.append(
        "Duration at recorded observation: "
        + (
            str(fields["duration_seconds"]) + " seconds"
            if fields.get("duration_seconds") is not None
            else "unavailable"
        )
    )
    if fields.get("customer_impact_count") is not None:
        lines.append(
            "Potential customer impact (recorded/estimated): "
            + str(fields["customer_impact_count"])
        )
    lines.append(
        "Telemetry: "
        + (
            ", ".join(k + "=" + str(v) for k, v in fields["metrics"].items())
            or "unavailable"
        )
    )
    lines.append(fields["safe_steps"])
    # Stable content is bound to the source revision and delivery ID; do not put
    # mutable wall-clock duration in a retry/reconciliation fingerprint.
    lines.append("NOC notification #" + str(delivery_id))
    text = "\n".join(lines)
    blocks = [
        {
            "type": "section",
            "text": {"type": "plain_text", "text": text, "emoji": False},
        }
    ]
    if fields.get("incident_id"):
        url = config["dashboard_origin"] + "/?incident=" + str(fields["incident_id"])
        buttons = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open private NOC"},
                "url": url,
                "action_id": "noc_open",
                "value": str(delivery_id),
            }
        ]
        if config["mode"] == "bot" and config.get("interactions_enabled"):
            buttons += [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": label},
                    "action_id": action,
                    "value": str(delivery_id),
                }
                for action, label in [
                    ("noc_ack", "Acknowledge"),
                    ("noc_review", "Request technician review"),
                ]
            ]
        # URL buttons also produce interactions; webhook mode has no receiver.
        if config["mode"] == "bot" and config.get("interactions_enabled"):
            blocks.append({"type": "actions", "elements": buttons})
        else:
            text += "\nPrivate NOC: " + url
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "plain_text", "text": "Private NOC: " + url},
                }
            )
    result = {
        "text": text,
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    if thread_ts:
        result["thread_ts"] = thread_ts
    return result
