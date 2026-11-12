"""RF read permission: explicit allowlist; fail closed for unknown roles."""
from fastapi import Depends, HTTPException

from app.api.deps import current_user
from app.models.entities import User

RF_READ_ROLES = frozenset({"admin", "operator"})


def require_rf_reader(user: User = Depends(current_user)) -> User:
    if user.role not in RF_READ_ROLES:
        raise HTTPException(status_code=403, detail="RF diagnostics access denied")
    return user
