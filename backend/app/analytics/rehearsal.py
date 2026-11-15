"""Guarded offline acceptance fixture. No external transport is constructed."""

import json
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.core.config import settings
from app.models.entities import User, Site, Device, TelemetrySample, Incident
from . import service as s
from .models import IntegrationConfig
from .contracts import ConfigInput


def seed():
    if not s.isolated():
        raise ValueError("Offline synthetic staging required")
    with SessionLocal() as db:
        if db.get(IntegrationConfig, 1):
            raise ValueError("Empty isolated analytics fixture required")
        u = User(
            username="analytics-acceptance",
            email="analytics@example.invalid",
            role="admin",
            is_active=True,
            password_hash=hash_password(settings.admin_password),
        )
        site = Site(name="Synthetic analytics POP")
        db.add_all([u, site])
        db.flush()
        d = Device(
            source="synthetic",
            external_id="analytics-fixture",
            name="Synthetic analytics device",
            vendor="MikroTik",
            wisp_site_id=site.id,
            enabled=True,
        )
        db.add(d)
        db.flush()
        at = s.now()
        for i in range(12):
            db.add(
                TelemetrySample(
                    device_id=d.id,
                    sampled_at=at - timedelta(minutes=12 - i),
                    cpu_percent=30 + i,
                    ram_percent=50,
                    temperature_c=35,
                    raw_data={"synthetic": True},
                )
            )
        db.add(
            Incident(
                title="Synthetic analytics incident",
                site_id=site.id,
                device_id=d.id,
                severity="warning",
                status="resolved",
                started_at=at - timedelta(hours=2),
                resolved_at=at - timedelta(hours=1),
                customer_impact=4,
            )
        )
        cfg = ConfigInput(
            operation_key=uuid4(),
            reason="Offline synthetic analytics acceptance fixture",
            expected_revision=0,
            enabled=True,
            server_url=s.SYNTHETIC_ORIGIN,
            export_device_ids=[d.id],
        )
        s.configure(db, u, cfg)
        db.commit()
        return {
            "username": u.username,
            "site_id": site.id,
            "device_id": d.id,
            "production_access": False,
        }


if __name__ == "__main__":
    print(json.dumps(seed()))
