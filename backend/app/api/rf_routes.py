"""Authenticated, read-only RF diagnostics endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import get_db
from app.models.entities import User
from app.services.rf_diagnostics import get_rf_diagnostics

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
