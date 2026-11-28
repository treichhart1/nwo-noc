"""Private offline evidence API. No native activation endpoint exists."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.exc import SQLAlchemyError
from app.api.deps import require_admin, require_operator
from app.core.config import settings
from app.core.database import get_db
from app.models.entities import AuditLog
from app.services.wave_evidence import Observation, compatibility, history, projection, stage_wave

router = APIRouter(prefix='/api/rf/wave', tags=['Wave evidence readiness'])


@router.get('/compatibility')
def contracts(user=Depends(require_operator)):
    return compatibility()


@router.get('/devices/{device_id}/history')
def stored_history(device_id: int = Path(gt=0), limit: int = Query(120, ge=1, le=500),
                   db=Depends(get_db), user=Depends(require_operator)):
    try:
        return history(db, device_id, now=datetime.now(timezone.utc), limit=limit)
    except LookupError:
        raise HTTPException(404, 'Device not found') from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, 'Wave evidence storage unavailable') from None


@router.post('/devices/{device_id}/synthetic')
def synthetic(observation: Observation, device_id: int = Path(gt=0),
              db=Depends(get_db), user=Depends(require_admin)):
    if not settings.offline_mode or settings.app_env != 'staging':
        raise HTTPException(403, 'Offline staging environment required')
    try:
        row = stage_wave(db, device_id, observation, now=datetime.now(timezone.utc), offline_authorized=True)
        db.add(AuditLog(user_id=user.id, action='wave.synthetic_evidence', entity_type='device',
                        entity_id=str(device_id), details={'sample_id': row.id, 'native': False}))
        db.commit()
        return {'sample_id': row.id, 'native': False, 'projection': projection(observation)}
    except ValueError:
        db.rollback()
        raise HTTPException(409, 'Evidence conflicts with documented synthetic inventory or time bounds') from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, 'Wave evidence storage unavailable') from None
