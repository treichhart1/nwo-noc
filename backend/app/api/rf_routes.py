"""Authenticated, read-only RF diagnostics endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.api.rf_permissions import require_rf_reader
from app.core.database import get_db
from app.models.entities import User, RFCollectionRun, Device
from app.services.rf_diagnostics import get_rf_diagnostics
from app.services.rf_collection_health import get_rf_collection_health
from app.services.rf_collection_reliability import collection_reliability
from app.services.rf_site_health import get_rf_site_health
from app.services.rf_self_healing import RFObservation, propose

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
    """Read-only, fail-closed simulation. No approval or execution is possible here.

    RF diagnostic metrics are deliberately not mapped from reachability latency
    or generic device signal fields: they are not equivalent to fresh RF samples.
    Until validated RF sample integration is implemented, this always blocks.
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    proposal = propose(RFObservation(
        device_id=device.id,
        checked_at=None,
        status="unknown",
        rssi_dbm=None,
        snr_db=None,
        ccq_percent=None,
    ))
    return {
        "device_id": device.id,
        "device_name": device.name,
        "mode": "simulation_only",
        "execution_enabled": False,
        "decision": proposal.decision.value,
        "reason": proposal.reason,
        "recommendation": proposal.recommendation,
        "requires_validated_rf_telemetry": True,
    }
