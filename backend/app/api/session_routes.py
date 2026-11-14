"""Logout revokes only the presented session and persists across service restarts."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError
from app.api.deps import current_user, oauth2_scheme
from app.core.database import get_db
from app.core.security import decode_access_claims
from app.models.entities import RevokedToken, AuditLog

router = APIRouter(prefix="/api/auth", tags=["Session security"])


@router.post("/logout", status_code=204)
def logout(
    user=Depends(current_user), token=Depends(oauth2_scheme), db=Depends(get_db)
):
    claims = decode_access_claims(token)
    try:
        db.add(
            RevokedToken(
                token_id=claims["jti"],
                user_id=user.id,
                expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc),
            )
        )
        db.add(
            AuditLog(
                user_id=user.id,
                action="auth.logout",
                entity_type="user",
                entity_id=str(user.id),
                details={"session_revoked": True},
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            503,
            "Logout revocation unavailable; retry when authentication storage recovers",
        ) from None
    return Response(status_code=204)
