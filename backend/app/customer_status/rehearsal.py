"""Explicit isolated browser fixture; canonical upstream customers and incidents reused."""

import json
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.config import settings
from app.core.security import hash_password
from app.models.entities import User, Incident, Device
from app.customers.models import CustomerService, CustomerAccount
from . import service as s
from .contracts import *
from .models import Publication, Policy


def seed():
    if not (
        settings.app_env == "staging"
        and settings.offline_mode
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Offline synthetic staging required")
    from pathlib import Path
    import os, secrets

    rate_path = Path(
        os.environ.get(
            "NOC_STATUS_REHEARSAL_RATE_FILE",
            "/var/lib/nwo-noc/monitoring/customer-status-rate-key",
        )
    )
    if rate_path.exists():
        raise ValueError("Fresh isolated public rate credential required")
    rate_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(rate_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(secrets.token_hex(32))
    with SessionLocal() as db:
        if db.scalar(select(Publication.id)) or db.get(Policy, 1):
            raise ValueError("Empty isolated communications fixture required")
        u = User(
            username="communications-acceptance",
            email="communications-acceptance@example.invalid",
            role="admin",
            is_active=True,
            password_hash=hash_password(settings.admin_password),
        )
        db.add(u)
        db.flush()
        service = db.scalar(
            select(CustomerService)
            .where(
                CustomerService.mapping_state == "verified",
                CustomerService.status == "active",
                CustomerService.site_id.is_not(None),
            )
            .order_by(CustomerService.id)
            .limit(1)
        )
        if not service:
            raise ValueError("Existing synthetic Powercode mapping fixture required")
        account = db.get(CustomerAccount, service.customer_id)
        account.last_synced_at = s.clock()
        service.last_synced_at = s.clock()
        i = Incident(
            title="PRIVATE synthetic communications source",
            site_id=service.site_id,
            device_id=service.device_id,
            severity="critical",
            started_at=s.clock() - timedelta(hours=1),
        )
        db.add(i)
        db.flush()

        def b(cls, **kw):
            return cls(
                operation_key=uuid4(),
                reason="Synthetic communications fixture; no public deployment or external delivery",
                **kw
            )

        a = s.area(
            db,
            u,
            b(AreaInput, label="Northwest service communities", publicly_approved=True),
        )
        p = s.create(db, u, b(SourceInput, incident_id=i.id))
        s.install_templates(db, u, b(Mutation))
        db.commit()
        return {
            "username": u.username,
            "incident_id": i.id,
            "case_id": p["id"],
            "area_id": a["id"],
            "site_id": service.site_id,
            "private_title": i.title,
            "customer_phone": account.phone,
            "customer_name": account.name,
            "public_delivery": False,
        }


if __name__ == "__main__":
    print(json.dumps(seed()))
