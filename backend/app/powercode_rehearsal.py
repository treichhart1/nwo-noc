"""Reproducible synthetic-only 40-tower customer acceptance; no live connection."""

import json, time
from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import select, func
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import (
    Site,
    Device,
    Sector,
    NetworkLink,
    Incident,
    MaintenanceWindow,
    User,
)
from app.customers.models import CustomerAccount, CustomerService
from app.customers.providers import FixtureAPI
from app.customers.sync import synchronize
from app.customers.impact import Target, analyze, recipients


def seed(db, count=200):
    if (
        not settings.offline_mode
        or settings.app_env != "staging"
        or not settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit offline synthetic staging required")
    if not 200 <= count <= 1000 or db.scalar(select(func.count(CustomerAccount.id))):
        raise ValueError("Fresh private customer fixture required (200–1000 accounts)")
    sites = [
        Site(
            name=f"NOC95 Synthetic POP {i:02d}",
            site_type="core-pop" if i == 0 else "tower",
        )
        for i in range(40)
    ]
    db.add_all(sites)
    db.flush()
    aps = [
        Device(
            source="synthetic",
            external_id=f"noc95-ap-{i}",
            name=f'NOC95 Synthetic {"Cambium" if i%2 else "Ubiquiti"} AP {i:02d}',
            vendor="Cambium" if i % 2 else "Ubiquiti",
            role="access-point",
            wisp_site_id=sites[i].id,
        )
        for i in range(40)
    ]
    db.add_all(aps)
    db.flush()
    sectors = [
        Sector(
            name=f"NOC95 Synthetic Sector {i:02d}",
            site_id=sites[i].id,
            device_id=aps[i].id,
        )
        for i in range(40)
    ]
    db.add_all(sectors)
    links = [
        NetworkLink(
            name=f"NOC95 Synthetic backhaul {i:02d}",
            a_site_id=sites[0].id,
            z_site_id=sites[i].id,
            link_type="licensed-microwave",
            vendor="Racom" if i % 2 else "Aviat",
            status="active",
        )
        for i in range(1, 40)
    ]
    db.add_all(links)
    fiber = NetworkLink(
        name="NOC95 Synthetic fiber distribution",
        a_site_id=sites[1].id,
        z_site_id=sites[2].id,
        link_type="fiber",
        status="active",
    )
    db.add(fiber)
    db.flush()
    now = datetime.now(timezone.utc) - timedelta(seconds=1)
    payload = {
        "schema_version": "noc-powercode-export-v1",
        "snapshot_id": "noc95-synthetic-1",
        "mode": "full",
        "complete": True,
        "as_of": now.isoformat(),
        "cursor": "noc95-cursor-1",
        "accounts": [],
        "services": [],
    }
    for i in range(1, count + 1):
        payload["accounts"].append(
            {
                "external_id": f"NOC95-C{i:04d}",
                "name": f"Synthetic Powercode Customer {i:04d}",
                "phone": f"+1555555{i:04d}",
                "email": f"synthetic-{i}@example.invalid",
                "updated_at": now.isoformat(),
            }
        )
        payload["services"].append(
            {
                "external_id": f"NOC95-S{i:04d}",
                "account_id": f"NOC95-C{i:04d}",
                "status": "suspended" if i % 19 == 0 else "active",
                "service_type": (
                    "fiber" if i % 5 == 0 else "hybrid" if i % 7 == 0 else "wireless"
                ),
                "address": f"{i} Synthetic Test Lane, Test OH",
                "plan": "Synthetic 100/20",
                "download_mbps": 100,
                "upload_mbps": 20,
                "source_refs": {"tower": f"synthetic-tower-{i%40}"},
                "updated_at": now.isoformat(),
            }
        )
    # No socket can be opened by FixtureAPI's required MockTransport.
    with_transport = FixtureAPI(
        httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
        sleep=lambda _: None,
    )
    started = time.monotonic()
    result = synchronize(db, with_transport)
    assert result["status"] == "healthy"
    for s in db.scalars(select(CustomerService).order_by(CustomerService.id)):
        i = int(s.external_id[7:])
        n = i % 40
        if i == count:
            continue  # Deliberately leave one customer unmapped.
        s.site_id = sites[n].id
        s.mapping_state = "verified"
        s.mapping_note = "Synthetic operator-reviewed fixture"
        if s.service_type in ("wireless", "hybrid"):
            s.device_id = aps[n].id
            s.sector_id = sectors[n].id
        if s.service_type in ("fiber", "hybrid"):
            # Fiber fixture has explicit local endpoints; no inferred association.
            s.site_id = sites[1].id
            s.fiber_link_id = fiber.id
            if s.service_type == "hybrid":
                s.device_id = aps[1].id
                s.sector_id = sectors[1].id
    incident = Incident(title="NOC95 Synthetic AP outage", device_id=aps[1].id)
    maintenance = MaintenanceWindow(
        title="NOC95 Synthetic planned maintenance",
        site_id=sites[1].id,
        starts_at=now + timedelta(hours=1),
        ends_at=now + timedelta(hours=2),
    )
    db.add_all([incident, maintenance])
    viewer = User(
        username="noc95-viewer",
        email="noc95-viewer@example.invalid",
        password_hash=hash_password("synthetic-customer-viewer-only"),
        role="viewer",
        is_active=True,
    )
    db.add(viewer)
    db.flush()
    elapsed = round((time.monotonic() - started) * 1000, 2)
    services = list(db.scalars(select(CustomerService).order_by(CustomerService.id)))
    at = time.monotonic()
    impacts = analyze(db, Target(site_id=sites[1].id), services)
    impact_ms = round((time.monotonic() - at) * 1000, 2)
    assert recipients(db, impacts)["eligible_count"] == 0
    return {
        "customers": count,
        "sites_created": 40,
        "source": "synthetic NOC export contract; not native Powercode",
        "live_connections": 0,
        "fixture_import_and_mapping_ms": elapsed,
        "impact_ms": impact_ms,
        "site_id": sites[1].id,
        "device_id": aps[1].id,
        "sector_id": sectors[1].id,
        "fiber_link_id": fiber.id,
        "incident_id": incident.id,
        "maintenance_id": maintenance.id,
    }


if __name__ == "__main__":
    import sys

    with SessionLocal() as db:
        try:
            result = seed(db, int(sys.argv[1]) if len(sys.argv) > 1 else 200)
            db.commit()
            print(json.dumps(result))
        except Exception:
            db.rollback()
            raise SystemExit(
                "Synthetic customer rehearsal failed; preserve isolated state and inspect the private acceptance database"
            ) from None
