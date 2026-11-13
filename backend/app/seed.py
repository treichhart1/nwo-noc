from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import Integration, User
from app.integrations.read_only import secret

def main():
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.username == settings.admin_username)):
            db.add(User(username=settings.admin_username, email=settings.admin_email, password_hash=hash_password(settings.admin_password), role="admin", is_active=True))
        for name in ("uisp", "mikrotik", "librenms", "powercode"):
            if not db.scalar(select(Integration).where(Integration.name == name)):
                status = "configured" if name == "uisp" and secret(settings.uisp_token) else "not_configured"
                db.add(Integration(name=name, status=status))
        db.commit()

if __name__ == "__main__":
    main()
