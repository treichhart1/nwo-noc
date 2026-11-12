"""Authenticated, read-only RF diagnostics endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.api.deps import current_user
from app.core.database import get_db
from app.models.entities import User, RFCollectionRun
from app.services.rf_diagnostics import get_rf_diagnostics
from app.services.rf_collection_health import get_rf_collection_health
from app.services.rf_collection_reliability import collection_reliability
from app.services.rf_site_health import get_rf_site_health

router = APIRouter(prefix="/api", tags=["RF diagnostics"])


@router.get("/devices/{device_id}/rf-diagnostics")
def device_rf_diagnostics(
    device_id: int = Path(..., ge=1),
    lookback_hours: int = Query(24, ge=1, le=168),
    max_samples: int = Query(288, ge=6, le=1000),
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
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
    _user: User = Depends(current_user),
):
    return get_rf_collection_health(db)


@router.get("/rf/collection-history")
def rf_collection_history(
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
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
    _user: User = Depends(current_user),
):
    return collection_reliability(db, days=days)


@router.get("/rf/site-health")
def rf_site_health(
    db: Session = Depends(get_db),
    _user: User = Depends(current_user),
):
    return get_rf_site_health(db)
