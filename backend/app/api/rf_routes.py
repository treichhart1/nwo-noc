"""Authenticated, read-only RF diagnostics endpoints."""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.api.rf_permissions import require_rf_reader
from app.core.database import get_db
from app.models.entities import User, RFCollectionRun, Device, TelemetrySample
from app.services.rf_diagnostics import get_rf_diagnostics
from app.services.rf_collection_health import get_rf_collection_health
from app.services.rf_collection_reliability import collection_reliability
from app.services.rf_site_health import get_rf_site_health
from app.services.rf_self_healing import RFObservation, propose
from app.services.rf_degradation import evaluate_rf_degradation
from app.services.rf_metrics import normalize_rf_metrics

router = APIRouter(prefix="/api", tags=["RF diagnostics"])


@router.get("/devices/{device_id}/rf-diagnostics")
def device_rf_diagnostics(
    device_id: int = Path(..., ge=1),
    lookback_hours: int = Query(24, ge=1, le=168),
    max_samples: int = Query(288, ge=6, le=1000),
    db: Session = Depends(get_db),
    _user: User = Depends(require_rf_reader),
):
    try:
        return get_rf_diagnostics(
            db, device_id, lookback_hours=lookback_hours, max_samples=max_samples,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc


@router.get("/rf/collection-health")
def rf_collection_health(
    db: Session = Depends(get_db),
    _user: User = Depends(require_rf_reader),
):
    return get_rf_collection_health(db)


@router.get("/rf/collection-history")
def rf_collection_history(
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _user: User = Depends(require_rf_reader),
):
    runs = db.scalars(
        select(RFCollectionRun)
        .order_by(RFCollectionRun.started_at.desc(), RFCollectionRun.id.desc())
        .limit(limit)
    ).all()
    return [{
        "id": run.id,
        "started_at": run.started_at.isoformat(),
        "status": run.status,
        "received": run.received,
        "staged": run.staged,
        "skipped": run.skipped,
    } for run in runs]


@router.get("/rf/collection-reliability")
def rf_collection_reliability(
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
    _user: User = Depends(require_rf_reader),
):
    return collection_reliability(db, days=days)


@router.get("/rf/site-health")
def rf_site_health(
    db: Session = Depends(get_db),
    _user: User = Depends(require_rf_reader),
):
    return get_rf_site_health(db)


@router.get("/devices/{device_id}/rf-self-healing/preview")
def rf_self_healing_preview(
    device_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    _user: User = Depends(require_rf_reader),
):
    """Bounded, read-only preview from explicitly tagged, fresh RF samples.

    Only a sustained three-sample degradation and independently fresh, confirmed
    reachability can produce a pending *simulation*. No action is executable.
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(TelemetrySample)
        .where(
            TelemetrySample.device_id == device_id,
            TelemetrySample.sampled_at >= now - timedelta(minutes=15),
            TelemetrySample.sampled_at <= now,
        )
        .order_by(TelemetrySample.sampled_at.desc(), TelemetrySample.id.desc())
        .limit(20)
    ).all()
    valid = []
    for row in rows:
        raw = row.raw_data if isinstance(row.raw_data, dict) else {}
        metrics = raw.get("rf_metrics_v1")
        if not isinstance(metrics, dict):
            continue
        normalized = normalize_rf_metrics(metrics)
        if not all(normalized.get(k) is not None for k in ("rssi_dbm", "snr_db", "ccq_percent")):
            continue
        sampled_at = row.sampled_at
        if sampled_at.tzinfo is None:
            sampled_at = sampled_at.replace(tzinfo=timezone.utc)
        valid.append((sampled_at.astimezone(timezone.utc), normalized))
    valid.sort(key=lambda item: item[0])
    recent = valid[-3:]
    degraded = len(recent) == 3 and evaluate_rf_degradation([item[1] for item in recent])["degraded"]
    reachability_fresh = (
        device.monitor_enabled is True
        and device.monitor_status == "up"
        and device.monitor_last_checked_at is not None
    )
    if reachability_fresh:
        checked = device.monitor_last_checked_at
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        reachability_fresh = now - timedelta(minutes=15) <= checked <= now
    if degraded and reachability_fresh:
        sampled_at, metrics = recent[-1]
        observation = RFObservation(device_id, sampled_at, "up",
                                    metrics["rssi_dbm"], metrics["snr_db"], metrics["ccq_percent"])
    else:
        observation = RFObservation(device_id, None, "unknown", None, None, None)
    proposal = propose(observation, now=now)
    return {
        "device_id": device.id,
        "device_name": device.name,
        "mode": "simulation_only",
        "execution_enabled": False,
        "decision": proposal.decision.value,
        "reason": proposal.reason if degraded and reachability_fresh else (
            "Three valid recent RF samples with sustained degradation and fresh confirmed reachability required"
        ),
        "recommendation": proposal.recommendation,
        "requires_validated_rf_telemetry": not (degraded and reachability_fresh),
    }
