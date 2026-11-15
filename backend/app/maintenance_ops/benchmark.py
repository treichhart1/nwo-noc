"""Rollback-only stored metadata/service workloads; no file/scan throughput claim."""

import json, time, resource, hashlib
from datetime import timedelta
from sqlalchemy import select, insert, event
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Site, User
from app.equipment.models import (
    Category as EquipmentCategory,
    Manufacturer,
    Product,
    Asset,
    Location,
    Maintenance,
)
from app.field.models import WorkOrder, now
from .models import Category, Document, Version, ServiceRecord, Schedule
from . import service as s


def measure(count):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Offline synthetic staging required")
    with SessionLocal() as db:
        if db.scalar(select(Document.id)) or db.scalar(select(Schedule.id)):
            raise ValueError("Empty maintenance fixture required")
        user = User(
            username="noc102-scale",
            email="noc102-scale@example.invalid",
            role="admin",
            password_hash="synthetic-unusable",
            is_active=True,
        )
        db.add(user)
        db.flush()
        sites = [Site(name=f"NOC102 Synthetic POP {i}") for i in range(40)]
        db.add_all(sites)
        db.flush()
        locations = [
            Location(
                code=f"noc102-scale-{i}",
                name=f"Synthetic tower {i}",
                kind="tower",
                site_id=t.id,
            )
            for i, t in enumerate(sites)
        ]
        db.add_all(locations)
        cat = EquipmentCategory(code="noc102-scale", name="Synthetic equipment")
        maker = Manufacturer(name="Synthetic maker", key="noc102-scale")
        doccat = Category(code="noc102-scale", name="Synthetic service documents")
        db.add_all([cat, maker, doccat])
        db.flush()
        product = Product(
            manufacturer_id=maker.id,
            category_id=cat.id,
            model="Synthetic inspection equipment",
            model_key="noc102-scale",
            unit="each",
            individually_tracked=True,
        )
        db.add(product)
        db.flush()
        assets = [
            Asset(
                asset_tag=f"NOC102-SCALE-{i}",
                manufacturer_id=maker.id,
                product_id=product.id,
                state="installed",
                condition="unknown",
                location_id=locations[i % 40].id,
            )
            for i in range(400)
        ]
        db.add_all(assets)
        db.flush()
        at = now()
        begin = time.perf_counter()
        docs = db.scalars(
            insert(Document).returning(Document.id),
            [
                {
                    "site_id": sites[i % 40].id,
                    "asset_id": assets[i % 400].id,
                    "category_id": doccat.id,
                    "title": f"Synthetic report {i}",
                    "visibility": "technician",
                    "retention_days": 2555,
                    "created_by": user.id,
                }
                for i in range(count)
            ],
        ).all()
        db.execute(
            insert(Version),
            [
                {
                    "document_id": id,
                    "number": 1,
                    "filename": "synthetic.txt",
                    "media_type": "text/plain",
                    "storage_key": hashlib.sha256(str(id).encode()).hexdigest(),
                    "sha256": hashlib.sha256(str(id).encode()).hexdigest(),
                    "size": 1,
                    "scan_state": "quarantined",
                    "scan_source": "disabled",
                    "uploaded_by": user.id,
                    "change_description": "Synthetic stored metadata only; no file exists",
                }
                for id in docs
            ],
        )
        works = db.scalars(
            insert(WorkOrder).returning(WorkOrder.id),
            [
                {
                    "site_id": sites[i % 40].id,
                    "asset_id": assets[i % 400].id,
                    "description": "Synthetic completed maintenance snapshot",
                    "technician_id": user.id,
                    "status": "completed",
                    "created_by": user.id,
                    "completed_at": at,
                }
                for i in range(count)
            ],
        ).all()
        ms = db.scalars(
            insert(Maintenance).returning(Maintenance.id),
            [
                {
                    "asset_id": assets[i % 400].id,
                    "technician_id": user.id,
                    "actor_id": user.id,
                    "inspected_at": at,
                    "status": "completed",
                    "condition": "unknown",
                    "work_performed": "Synthetic completed inspection evidence",
                    "parts": [],
                }
                for i in range(count)
            ],
        ).all()
        db.execute(
            insert(ServiceRecord),
            [
                {
                    "work_order_id": works[i],
                    "asset_id": assets[i % 400].id,
                    "site_id": sites[i % 40].id,
                    "technician_id": user.id,
                    "equipment_maintenance_id": ms[i],
                    "serviced_at": at,
                    "diagnostics": "Synthetic diagnostic history; no real equipment",
                    "labor_minutes": 30,
                }
                for i in range(count)
            ],
        )
        db.add_all(
            [
                Schedule(
                    site_id=t.id,
                    asset_id=assets[i].id,
                    technician_id=user.id,
                    name="Synthetic preventive equipment inspection",
                    maintenance_type="router_switch",
                    recurrence="monthly",
                    starts_at=at - timedelta(hours=1),
                    next_due_at=at - timedelta(hours=1),
                    lead_days=0,
                    created_by=user.id,
                )
                for i, t in enumerate(sites)
            ]
        )
        db.flush()
        insert_ms = (time.perf_counter() - begin) * 1000
        queries = []

        def observe(*a):
            queries.append(1)

        event.listen(db.get_bind(), "before_cursor_execute", observe)

        def timed(fn):
            b = time.perf_counter()
            value = fn()
            return round((time.perf_counter() - b) * 1000, 2), value

        try:
            page, _ = timed(
                lambda: db.scalars(
                    select(Document).order_by(Document.id.desc()).limit(50)
                ).all()
            )
            search, _ = timed(
                lambda: db.scalars(
                    select(Document)
                    .where(Document.title.icontains("report 99"))
                    .limit(50)
                ).all()
            )
            calendar, cal = timed(
                lambda: s.calendar_rows(
                    db, user, at - timedelta(days=1), at + timedelta(days=30)
                )
            )
            report, result = timed(
                lambda: s.report(
                    db, user, at - timedelta(days=1), at + timedelta(days=1)
                )
            )
            read_queries = len(queries)
            generation, created = timed(lambda: s.generate(db, at))
            return {
                "sites": 40,
                "assets": 400,
                "documents_and_services_each": count,
                "insert_ms": round(insert_ms, 2),
                "document_page50_ms": page,
                "title_search_ms": search,
                "calendar_ms": calendar,
                "calendar_truncated": cal["truncated"],
                "aggregate_service_report_ms": report,
                "report_detail_truncated": result["truncated"],
                "read_statements": read_queries,
                "generate40_ms": generation,
                "unique_work_orders_generated": len(created["generated"]),
                "peak_process_rss_kib": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
                "external_calls": 0,
                "scope": "Stored metadata and service aggregates; no physical file IO, malware scan, HTTP or production sizing claim",
            }
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", observe)
            db.rollback()


if __name__ == "__main__":
    for count in (1000, 10000):
        print(json.dumps(measure(count)), flush=True)
