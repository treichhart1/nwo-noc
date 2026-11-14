"""Synthetic stored asset/stock workloads; not procurement or live monitoring."""

import json, time, resource
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select, func, insert, event
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Site, Device, User
from .models import (
    Category,
    Manufacturer,
    Product,
    Location,
    Asset,
    DeviceBinding,
    Movement,
    BulkBalance,
    Financial,
    StockPolicy,
)
from . import service as s
from .contracts import AssetMove


def measure(db, count):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit offline synthetic staging required")
    if db.scalar(select(Asset.id)) or db.scalar(select(Category.id)):
        raise ValueError("Empty isolated equipment fixture required")
    actor = User(
        username="noc100-scale",
        email="noc100-scale@example.invalid",
        password_hash="synthetic-unusable-hash",
        role="operator",
        is_active=True,
    )
    db.add(actor)
    db.flush()
    category = Category(code="synthetic-equipment", name="Synthetic physical equipment")
    maker = Manufacturer(
        name="Synthetic benchmark maker", key="synthetic-benchmark-maker"
    )
    db.add_all([category, maker])
    db.flush()
    products = [
        Product(
            manufacturer_id=maker.id,
            category_id=category.id,
            model=f"Synthetic model {i}",
            model_key=f"synthetic model {i}",
            individually_tracked=True,
            unit="each",
            critical=i < 3,
        )
        for i in range(100)
    ]
    material = Product(
        manufacturer_id=maker.id,
        category_id=category.id,
        model="Synthetic measured fiber",
        model_key="synthetic measured fiber",
        individually_tracked=False,
        unit="metre",
    )
    db.add_all(products + [material])
    db.flush()
    sites = [Site(name=f"NOC100 Synthetic POP {i:02}") for i in range(40)]
    db.add_all(sites)
    db.flush()
    locations = [
        Location(
            code=f"noc100-tower-{i}",
            name=f"Synthetic tower {i}",
            kind="tower",
            site_id=sites[i].id,
        )
        for i in range(40)
    ]
    warehouse = Location(
        code="noc100-warehouse", name="Synthetic warehouse", kind="warehouse"
    )
    office = Location(code="noc100-office", name="Synthetic office", kind="office")
    db.add_all(locations + [warehouse, office])
    db.flush()
    devices = [
        Device(
            source="synthetic",
            external_id=f"noc100-scale-{i}",
            name=f"Synthetic equipment {i}",
            wisp_site_id=sites[i % 40].id,
            vendor=maker.name,
        )
        for i in range(400)
    ]
    db.add_all(devices)
    db.flush()
    now = s.clock()
    states = ["available", "received", "reserved", "failed", "under_maintenance"]
    records = []
    for i in range(count):
        installed = i < 400
        records.append(
            {
                "asset_tag": f"NWO-SCALE-{i:06}",
                "product_id": products[i % 100].id,
                "manufacturer_id": maker.id,
                "serial_number": f"SYN100-{i:08}",
                "serial_key": f"syn100-{i:08}",
                "location_id": locations[i % 40].id if installed else warehouse.id,
                "state": "in_service" if installed else states[i % 5],
                "condition": "unknown" if installed else "good",
                "device_id": devices[i].id if installed else None,
                "recorded_firmware": None,
                "purchase_date": None,
            }
        )
    at = time.perf_counter()
    db.execute(insert(Asset), records)
    db.flush()
    insert_ms = (time.perf_counter() - at) * 1000
    assets = list(db.scalars(select(Asset).order_by(Asset.id)))
    db.execute(
        insert(Movement),
        [
            {
                "asset_id": a.id,
                "actor_id": actor.id,
                "action": "initial_documentation",
                "reason": "Synthetic benchmark snapshot, no fabricated physical history",
                "before": {},
                "after": {"state": a.state, "asset_tag": a.asset_tag},
            }
            for a in assets
        ],
    )
    db.add_all(
        [
            DeviceBinding(asset_id=a.id, device_id=a.device_id, started_at=now)
            for a in assets
            if a.device_id
        ]
    )
    db.add_all(
        [
            Financial(
                asset_id=a.id,
                currency="USD",
                purchase_cost=Decimal("100.00"),
                updated_by=actor.id,
            )
            for i, a in enumerate(assets)
            if i % 10 == 0
        ]
    )
    db.add_all(
        [
            BulkBalance(
                product_id=material.id,
                location_id=loc.id,
                state="available",
                quantity=Decimal("100.500"),
            )
            for loc in locations
        ]
    )
    db.add_all(
        [
            StockPolicy(
                product_id=p.id,
                unit="each",
                minimum_available=Decimal("5"),
                critical=False,
            )
            for p in products[:20]
        ]
    )
    db.flush()
    queries = []
    conn = db.connection()

    def measured(conn, cursor, statement, params, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(conn, "before_cursor_execute", measured)
    try:
        at = time.perf_counter()
        page = list(db.scalars(s.asset_query().order_by(Asset.id).limit(100)))
        page_ms = (time.perf_counter() - at) * 1000
        at = time.perf_counter()
        search = list(db.scalars(s.asset_query(q=f"SYN100-{count-1:08}").limit(100)))
        search_ms = (time.perf_counter() - at) * 1000
        at = time.perf_counter()
        report = s.dashboard(db, True)
        dashboard_ms = (time.perf_counter() - at) * 1000
        at = time.perf_counter()
        recommendations = s.planning(db)
        planning_ms = (time.perf_counter() - at) * 1000
        spare = next(a for a in assets if a.state == "available")
        at = time.perf_counter()
        s.move_asset(
            db,
            spare,
            AssetMove(
                expected_revision=spare.revision,
                to_state="available",
                to_location_id=office.id,
                reason="Synthetic benchmark revision-protected movement",
            ),
            actor.id,
        )
        db.flush()
        transfer_ms = (time.perf_counter() - at) * 1000
        assert (
            len(page) == 100
            and len(search) == 1
            and report["asset_count"] == count
            and spare.location_id == office.id
        )
        assert (
            len(report["by_tower"]) == 40
            and db.scalar(select(func.count(DeviceBinding.id))) == 400
        )
        return {
            "sites": 40,
            "monitored_devices": 400,
            "assets": count,
            "models": 101,
            "bulk_locations": 40,
            "history_rows": count + 1,
            "asset_insert_ms": round(insert_ms, 2),
            "page_100_ms": round(page_ms, 2),
            "identity_search_ms": round(search_ms, 2),
            "dashboard_with_financials_ms": round(dashboard_ms, 2),
            "spare_planning_ms": round(planning_ms, 2),
            "revision_protected_transfer_ms": round(transfer_ms, 2),
            "selects_for_measured_operations": len(queries),
            "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "external_calls": 0,
            "limitations": "Synthetic stored snapshots/history and one actual service movement; not device polling, real lifecycle reliability, attachment-volume stress or production sizing",
        }
    finally:
        event.remove(conn, "before_cursor_execute", measured)


if __name__ == "__main__":
    for count in [1000, 10000]:
        with SessionLocal() as db:
            try:
                print(json.dumps(measure(db, count)), flush=True)
            finally:
                db.rollback()
