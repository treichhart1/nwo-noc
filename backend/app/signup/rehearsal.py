"""Synthetic browser fixtures only. No public/customer/provider/network IO."""

import json, os
from pathlib import Path
from cryptography.fernet import Fernet
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import User, Site
from .models import Plan


def seed():
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Offline synthetic staging required")
    key = Path(settings.signup_pii_key_file)
    if not key.is_absolute():
        raise ValueError("Private absolute key path required")
    if not key.exists():
        key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
            f.flush()
            os.fsync(f.fileno())
    from .crypto import master

    master()
    with SessionLocal() as db:
        for name, role in [
            ("signup-acceptance", "admin"),
            ("signup-tech", "technician"),
        ]:
            if db.scalar(select(User.id).where(User.username == name)):
                raise ValueError("Fresh fixture required")
            db.add(
                User(
                    username=name,
                    email=name + "@example.invalid",
                    password_hash=hash_password(settings.admin_password),
                    role=role,
                    is_active=True,
                )
            )
        p = Plan(
            name="NOC103 Synthetic Review Plan",
            audience="both",
            technology="wireless",
            active=True,
            public=True,
            currency="USD",
            monthly="65.00",
            installation="0.00",
            eligibility="Synthetic only. Subject to field qualification.",
            equipment=[],
        )
        db.add(p)
        db.flush()
        tech = db.scalar(select(User).where(User.username == "signup-tech"))
        site = db.scalar(select(Site).order_by(Site.id))
        if site is None:
            site = Site(name="NOC103 Synthetic POP", latitude=41.4, longitude=-84.2)
            db.add(site)
            db.flush()
        result = {
            "plan_id": p.id,
            "site_id": site.id,
            "tech_id": tech.id,
            "admin_username": "signup-acceptance",
            "tech_username": "signup-tech",
        }
        db.commit()
        return result


if __name__ == "__main__":
    print(json.dumps(seed()), flush=True)
