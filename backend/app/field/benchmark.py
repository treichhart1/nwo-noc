"""Rollback-only synthetic work-order/QR queries, no field/network equipment IO."""

import json, time, resource
from uuid import uuid4
from sqlalchemy import select, insert, event
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Site, User
from .models import QRLabel, WorkOrder, Event, now
from . import service as s


def measure(count):
    if not (
        settings.app_env == "staging"
        and settings.offline_mode
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit offline synthetic staging required")
    with SessionLocal() as db:
        if db.scalar(select(WorkOrder.id)):
            raise ValueError("Empty isolated field fixture required")
        tech = User(
            username="field-scale",
            email="field-scale@example.invalid",
            password_hash="synthetic-unusable",
            role="technician",
            is_active=True,
        )
        db.add(tech)
        db.flush()
        sites = [Site(name=f"NOC101 Synthetic POP {i}") for i in range(40)]
        db.add_all(sites)
        db.flush()
        labels = [QRLabel(site_id=x.id, created_by=tech.id) for x in sites]
        db.add_all(labels)
        db.flush()
        at = time.perf_counter()
        db.execute(
            insert(WorkOrder),
            [
                {
                    "number": "WO-" + str(uuid4()),
                    "site_id": sites[i % 40].id,
                    "technician_id": tech.id,
                    "description": "Synthetic technician scale fixture",
                    "status": "assigned",
                    "priority": "normal",
                    "required_equipment": [],
                    "created_by": tech.id,
                }
                for i in range(count)
            ],
        )
        db.flush()
        insert_ms = (time.perf_counter() - at) * 1000
        queries = []

        def before(*args):
            queries.append(1)

        event.listen(db.get_bind(), "before_cursor_execute", before)
        try:
            at = time.perf_counter()
            target = s.resolve(db, tech, labels[0].identifier)
            scan_ms = (time.perf_counter() - at) * 1000
            at = time.perf_counter()
            page = db.scalars(
                select(WorkOrder)
                .where(WorkOrder.technician_id == tech.id)
                .order_by(WorkOrder.id.desc())
                .limit(50)
            ).all()
            page_ms = (time.perf_counter() - at) * 1000
            at = time.perf_counter()
            detail = s.work_detail(db, tech, page[0])
            detail_ms = (time.perf_counter() - at) * 1000
            at = time.perf_counter()
            tower = s.tower(db, tech, target["site_id"])
            tower_ms = (time.perf_counter() - at) * 1000
            return {
                "sites": 40,
                "work_orders": count,
                "insert_ms": round(insert_ms, 2),
                "authorized_qr_lookup_ms": round(scan_ms, 2),
                "page_50_ms": round(page_ms, 2),
                "work_detail_ms": round(detail_ms, 2),
                "tower_context_ms": round(tower_ms, 2),
                "measured_statements": len(queries),
                "peak_process_rss_kib": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
                "external_calls": 0,
                "scope": "Single-process PostgreSQL stored-record reads; not physical camera scanning, WAN/mobile latency or production sizing",
            }
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", before)
            db.rollback()


if __name__ == "__main__":
    for n in (1000, 10000):
        print(json.dumps(measure(n)), flush=True)
