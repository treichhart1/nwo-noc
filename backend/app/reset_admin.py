import argparse
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import User

def main():
    parser = argparse.ArgumentParser(description="Reset an NWO NOC user password")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.username))
        if not user:
            raise SystemExit(f"User '{args.username}' does not exist")
        user.password_hash = hash_password(args.password)
        db.commit()
    print(f"Password reset for {args.username}")

if __name__ == "__main__":
    main()
