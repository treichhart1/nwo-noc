"""One-shot stdin bootstrap inside the restricted app container; no setup endpoint."""

import json
import re
import sys
from sqlalchemy import select, text
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import User, AuditLog


def main():
    body = json.loads(sys.stdin.buffer.read(4096))
    if (
        not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", body["username"])
        or not 16 <= len(body["password"].encode()) <= 72
        or any(c in body["password"] for c in "\n\r\x00")
    ):
        raise ValueError("Validated privileged setup input required")
    with SessionLocal() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(1130113)"))
        if db.scalar(select(User.id).where(User.role == "admin").limit(1)):
            raise ValueError("Administrator already exists; setup is one-time only")
        user = User(
            username=body["username"],
            email=body["email"],
            password_hash=hash_password(body["password"]),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(
            AuditLog(
                user_id=user.id,
                action="installer.admin_initialized",
                entity_type="installation",
                entity_id=body["installation_id"],
                details={"method": "privileged_stdin", "one_time": True},
            )
        )
        db.commit()
    print("Administrator initialized; no setup endpoint or retained setup password")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(
            "Administrator bootstrap rejected ("
            + type(error).__name__
            + "); preserve the database and use documented recovery"
        )
