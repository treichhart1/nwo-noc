"""Offline synthetic fixtures and restart verification; no messages/equipment."""

import json, time
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select, func
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import User
from app.equipment.models import Asset
from app.equipment.contracts import MaintenanceInput
from app.field import service as field
from app.field.contracts import LabelInput, Update, GrantInput
from app.field.models import WorkOrder
from .models import *
from .contracts import *
from . import service as s


def guard():
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
        and settings.maintenance_document_scanner == "synthetic"
    ):
        raise ValueError(
            "Explicit isolated synthetic maintenance configuration required"
        )


def b(cls, **kw):
    return cls(
        operation_key=uuid4(),
        reason="Synthetic maintenance release acceptance fixture",
        **kw
    )


def seed():
    guard()
    with SessionLocal() as db:
        if db.scalar(select(Schedule.id)):
            raise ValueError("Empty maintenance fixture required")
        admin = db.scalar(select(User).where(User.username == "acceptance"))
        tech = db.scalar(select(User).where(User.username == "field-acceptance"))
        asset = db.scalar(select(Asset).where(Asset.asset_tag == "NWO101-FIELD-SPARE"))
        if not admin or not tech or not asset or asset.state != "installed":
            raise ValueError("Completed NOC101 replacement fixture required")
        # Separate fixture users avoid consuming preceding feature-stage request budgets.
        admin = User(
            username="maintenance-acceptance",
            email="maintenance-acceptance@example.invalid",
            role="admin",
            is_active=True,
            password_hash=hash_password(settings.admin_password),
        )
        tech = User(
            username="maintenance-tech",
            email="maintenance-tech@example.invalid",
            role="technician",
            is_active=True,
            password_hash=hash_password(settings.admin_password),
        )
        db.add_all([admin, tech])
        db.flush()
        site = field.asset_site(db, asset)
        field.grant(db, admin, b(GrantInput, site_id=site, technician_id=tech.id))
        schedule = s.create_schedule(
            db,
            admin,
            b(
                ScheduleInput,
                site_id=site,
                asset_id=asset.id,
                technician_id=tech.id,
                name="NOC102 Synthetic preventive radio inspection",
                maintenance_type="backhaul_radio",
                recurrence="monthly",
                starts_at=now() - timedelta(hours=1),
                lead_days=0,
                estimated_minutes=30,
            ),
        )
        result = s.generate(db)
        work = db.get(WorkOrder, result["generated"][0]["work_order_id"])
        field.update_work(
            db,
            admin,
            work,
            b(
                Update,
                expected_revision=work.revision,
                status="in_progress",
                notes="Started synthetic equipment service inspection",
            ),
        )
        s.service_record(
            db,
            admin,
            b(
                ServiceInput,
                work_order_id=work.id,
                expected_revision=work.revision,
                maintenance=MaintenanceInput(
                    inspected_at=now(),
                    technician_id=tech.id,
                    work_performed="Inspected synthetic connectors and RF service history",
                    fault_description="Synthetic intermittent link report",
                    repair_actions="Documented visual connector inspection only",
                    reason="Synthetic equipment service documentation",
                ),
                diagnostics="Synthetic RF and connector evidence reviewed; no equipment access",
                labor_minutes=30,
                warranty_claim="Synthetic manufacturer review pending",
                repair_cost="19.50",
                currency="USD",
            ),
        )
        category = Category(code="inspection-report", name="Inspection reports")
        db.add(category)
        db.flush()
        doc = s.create_document(
            db,
            admin,
            b(
                DocumentInput,
                site_id=site,
                asset_id=asset.id,
                category_id=category.id,
                title="NOC102 Synthetic approved tower service report",
            ),
        )
        import base64

        d = db.get(Document, doc["id"])
        v = s.upload_version(
            db,
            admin,
            d,
            b(
                VersionInput,
                expected_revision=d.revision,
                filename="service-report.txt",
                media_type="text/plain",
                contents_base64=base64.b64encode(
                    b"Synthetic service report, no credentials or equipment access"
                ).decode(),
                change_description="Initial synthetic service report for release acceptance",
            ),
        )
        s.control_document(
            db,
            admin,
            d,
            b(
                DocumentControl,
                expected_revision=d.revision,
                action="approve",
                version_id=v["id"],
            ),
        )
        private = s.create_document(
            db,
            admin,
            b(
                DocumentInput,
                site_id=site,
                category_id=category.id,
                title="NOC102 Private purchase invoice metadata",
                visibility="admin",
            ),
        )
        qr = field.label(db, admin, b(LabelInput, site_id=site))
        db.commit()
        return {
            "site_id": site,
            "admin_username": admin.username,
            "tech_username": tech.username,
            "asset_id": asset.id,
            "technician_id": tech.id,
            "schedule_id": schedule["id"],
            "work_id": work.id,
            "document_id": d.id,
            "version_id": v["id"],
            "private_document_id": private["id"],
            "tower_qr": qr["identifier"],
        }


def queue():
    guard()
    with SessionLocal() as db:
        first = db.scalar(
            select(Schedule).where(
                Schedule.name == "NOC102 Synthetic preventive radio inspection"
            )
        )
        admin = db.get(User, first.created_by)
        s.create_schedule(
            db,
            admin,
            b(
                ScheduleInput,
                site_id=first.site_id,
                asset_id=first.asset_id,
                technician_id=first.technician_id,
                name="NOC102 Synthetic restart occurrence",
                maintenance_type="backhaul_radio",
                recurrence="monthly",
                starts_at=now() - timedelta(minutes=1),
                lead_days=0,
            ),
        )
        db.commit()
    return {"queued": True, "external_delivery": False}


def verify():
    guard()
    for _ in range(120):
        try:
            with SessionLocal() as db:
                sc = db.scalar(
                    select(Schedule).where(
                        Schedule.name == "NOC102 Synthetic restart occurrence"
                    )
                )
                count = db.scalar(
                    select(func.count())
                    .select_from(Occurrence)
                    .where(Occurrence.schedule_id == sc.id)
                )
                if count == 1:
                    for v in db.scalars(select(Version)):
                        s.storage.read(v.storage_key)
                    return {
                        "unique_restart_occurrences": count,
                        "document_integrity": "passed",
                        "external_delivery": False,
                    }
        except __import__("sqlalchemy").exc.SQLAlchemyError:
            pass
        time.sleep(1)
    raise ValueError("Scheduled occurrence did not recover within120 seconds")


if __name__ == "__main__":
    import sys

    action = sys.argv[1] if len(sys.argv) > 1 else "seed"
    print(json.dumps({"seed": seed, "queue": queue, "verify": verify}[action]()))
