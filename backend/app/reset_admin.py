"""Privileged local recovery only; no password argv/environment or public endpoint."""
import argparse
import getpass
import os
import re
import sys
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import User, AuditLog


def read_password(*, stdin=False):
    if stdin:
        raw = sys.stdin.buffer.read(74)
        if raw.endswith(b"\n"):
            raw = raw[:-1]
        value = raw.decode("utf-8")
    else:
        value = getpass.getpass("New recovery password: ")
        if value != getpass.getpass("Confirm recovery password: "):
            raise ValueError("Password confirmation mismatch")
    if not 16 <= len(value.encode()) <= 72 or any(c in value for c in "\n\r\0"):
        raise ValueError("Password must contain16–72 UTF-8 bytes without control line delimiters")
    return value


def reset(db, username, password, reason):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", username) or not 10 <= len(reason) <= 200:
        raise ValueError("Bounded username and meaningful recovery reason required")
    if not 16 <= len(password.encode()) <= 72 or any(c in password for c in "\n\r\0"):
        raise ValueError("Invalid recovery password")
    user = db.scalar(select(User).where(User.username == username).with_for_update())
    if not user:
        raise ValueError("Recovery target unavailable")
    user.password_hash = hash_password(password)
    db.add(AuditLog(action="auth.password_reset", entity_type="user", entity_id=str(user.id),
                    details={"method": "privileged_local_recovery", "operator_os_uid": os.getuid(),
                             "reason": reason, "prior_sessions_invalidated": True}))
    db.flush()
    return user


def main():
    parser = argparse.ArgumentParser(description="Approved privileged local password recovery", allow_abbrev=False)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--approve-local-recovery", required=True, action="store_true")
    parser.add_argument("--reason", required=True)
    args, unknown = parser.parse_known_args()
    if unknown:
        parser.exit(2, "Unexpected recovery argument; never supply credentials in argv\n")
    password = read_password(stdin=args.password_stdin)
    with SessionLocal() as db:
        reset(db, args.username, password, args.reason)
        db.commit()
    print("Password recovered and prior sessions invalidated; wait one second before login")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, UnicodeError):
        raise SystemExit("Recovery rejected; check protected input and authorized target") from None
