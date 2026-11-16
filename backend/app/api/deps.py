from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import decode_access_claims
from app.models.entities import User, RevokedToken, AuditLog
from sqlalchemy.exc import SQLAlchemyError

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def current_user(
    request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
):
    claims = decode_access_claims(token)
    if claims is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    try:
        revoked = db.get(RevokedToken, claims["jti"])
        user = db.scalar(select(User).where(User.username == claims["sub"]))
        reset_at = db.scalar(select(AuditLog.created_at).where(
            AuditLog.entity_type == "user", AuditLog.entity_id == str(user.id),
            AuditLog.action == "auth.password_reset",
        ).order_by(AuditLog.id.desc()).limit(1)) if user else None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication storage unavailable") from None
    if revoked or not user or not user.is_active or claims.get("uid") != user.id or (reset_at and claims["iat"] <= int(reset_at.timestamp())):
        raise HTTPException(401, "Invalid or expired session")
    # Field-only technicians must not bypass assignment scope through legacy APIs.
    if user.role == "technician" and not (
        request.url.path.startswith(("/api/field/", "/api/maintenance-ops/", "/api/applications/"))
        or request.url.path in {"/api/auth/me", "/api/auth/logout"}
    ):
        db.add(
            AuditLog(
                user_id=user.id,
                action="field.legacy_access_denied",
                entity_type="field_operations",
                entity_id="private",
                details={"scope_enforced": True},
            )
        )
        db.commit()
        raise HTTPException(
            403, "Technician access is limited to assigned field workflows"
        )
    return user


def require_operator(user: User = Depends(current_user)):
    if user.role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="Operator access required")
    return user


def require_admin(user: User = Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user
