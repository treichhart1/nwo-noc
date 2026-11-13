from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.entities import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    username = decode_access_token(token)
    user = db.scalar(select(User).where(User.username == username)) if username else None
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


def require_operator(user: User = Depends(current_user)):
    if user.role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="Operator access required")
    return user
