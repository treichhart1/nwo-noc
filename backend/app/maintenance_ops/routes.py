from datetime import datetime
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select, func
from app.core.database import get_db
from app.field.routes import access, run as field_run
from app.field import service as field
from app.field.models import WorkOrder
from app.equipment import service as inv
from .models import *
from .contracts import *
from . import service as s


def run(db, u, fn, write=False):
    def checked():
        try:
            return fn()
        except PermissionError:
            raise
        except OSError:
            from fastapi import HTTPException

            db.rollback()
            raise HTTPException(
                503,
                "Private document storage unavailable; preserve files and contact administrator",
            ) from None

    return field_run(db, u, checked, write)


router = APIRouter(
    prefix="/api/maintenance-ops",
    tags=["Maintenance and documents"],
    dependencies=[Depends(access)],
)


def change(db, u, b, action, fn, administrator=False):
    def apply():
        if administrator:
            field.admin(u)
        return field.mutation(db, u, "maintenance_" + action, b, fn)

    return run(db, u, apply, True)


@router.get("/me")
def me(u=Depends(access)):
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "maintenance_types": s.TYPES,
    }


@router.get("/schedules")
def schedules(
    site_id: int | None = None,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    def read():
        q = select(Schedule)
        if site_id:
            q = q.where(Schedule.site_id == site_id)
        if u.role == "technician":
            q = q.where(
                __import__("sqlalchemy").or_(
                    Schedule.technician_id == u.id,
                    Schedule.site_id.in_(s.visible_sites(db, u)),
                )
            )
        rows = db.scalars(
            q.order_by(Schedule.next_due_at, Schedule.id).offset(offset).limit(50)
        ).all()
        return {
            "items": [inv.view(r) for r in rows],
            "next_offset": offset + 50 if len(rows) == 50 else None,
        }

    return run(db, u, read)


@router.post("/schedules")
def schedule_create(b: ScheduleInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "schedule_create", lambda: s.create_schedule(db, u, b), True
    )


@router.post("/schedules/{id}")
def schedule_update(id: int, b: ScheduleUpdate, db=Depends(get_db), u=Depends(access)):
    return change(
        db,
        u,
        b,
        "schedule_update_" + str(id),
        lambda: s.update_schedule(db, u, s.schedule_access(db, u, id, True), b),
        True,
    )


@router.post("/generate")
def generate(b: Mutation, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "generate", lambda: s.generate(db), True)


@router.post("/reminders/refresh")
def refresh(b: Mutation, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "reminders", lambda: s.reminders(db), True)


@router.get("/reminders")
def reminders(
    site_id: int | None = None,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    def read():
        q = select(Reminder).where(Reminder.active.is_(True))
        if site_id:
            q = q.where(Reminder.site_id == site_id)
        if u.role == "technician":
            q = q.where(Reminder.site_id.in_(s.visible_sites(db, u)))
        rows = db.scalars(q.order_by(Reminder.id.desc()).offset(offset).limit(50)).all()
        return {
            "items": [inv.view(r) | {"external_delivery": False} for r in rows],
            "next_offset": offset + 50 if len(rows) == 50 else None,
        }

    return run(db, u, read)


@router.get("/calendar")
def calendar(
    start: datetime,
    end: datetime,
    site_id: int | None = None,
    technician_id: int | None = None,
    category_id: int | None = None,
    maintenance_type: str | None = Query(None, max_length=60),
    db=Depends(get_db),
    u=Depends(access),
):
    return run(
        db,
        u,
        lambda: s.calendar_rows(
            db, u, start, end, site_id, technician_id, category_id, maintenance_type
        ),
    )


@router.post("/availability")
def availability(b: AvailabilityInput, db=Depends(get_db), u=Depends(access)):
    def create():
        field.tech(db, b.technician_id)
        r = Availability(
            **b.model_dump(exclude={"operation_key", "reason"}), created_by=u.id
        )
        db.add(r)
        db.flush()
        return inv.view(r)

    return change(db, u, b, "availability", create, True)


@router.post("/services")
def service_create(b: ServiceInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "service", lambda: s.service_record(db, u, b))


@router.get("/services")
def services(
    site_id: int | None = None,
    asset_id: int | None = None,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    def read():
        q = select(ServiceRecord)
        if site_id:
            q = q.where(ServiceRecord.site_id == site_id)
        if asset_id:
            q = q.where(ServiceRecord.asset_id == asset_id)
        if u.role == "technician":
            q = q.where(ServiceRecord.technician_id == u.id)
        rows = db.scalars(
            q.order_by(ServiceRecord.serviced_at.desc(), ServiceRecord.id.desc())
            .offset(offset)
            .limit(50)
        ).all()
        return {
            "items": s.service_views(db, rows, u.role == "admin"),
            "next_offset": offset + 50 if len(rows) == 50 else None,
        }

    return run(db, u, read)


@router.get("/reports")
def report(
    start: datetime,
    end: datetime,
    site_id: int | None = None,
    technician_id: int | None = None,
    financial: bool = False,
    db=Depends(get_db),
    u=Depends(access),
):
    return run(
        db, u, lambda: s.report(db, u, start, end, site_id, technician_id, financial)
    )


@router.get("/reports.csv")
def export(
    start: datetime,
    end: datetime,
    site_id: int | None = None,
    technician_id: int | None = None,
    financial: bool = False,
    db=Depends(get_db),
    u=Depends(access),
):
    def read():
        data = s.report(db, u, start, end, site_id, technician_id, financial)
        if data["truncated"]:
            from fastapi import HTTPException

            raise HTTPException(
                413,
                "More than1000 service rows: narrow date/tower/technician filters before CSV export",
            )
        field.audit(db, u.id, "maintenance_export", "maintenance_report", "private")
        db.commit()
        return Response(
            s.csv_report(data, financial),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="maintenance-report.csv"',
                "Cache-Control": "no-store",
            },
        )

    return run(db, u, read)


@router.get("/categories")
def categories(db=Depends(get_db), u=Depends(access)):
    return run(
        db,
        u,
        lambda: [
            inv.view(c)
            for c in db.scalars(select(Category).order_by(Category.id).limit(500))
        ],
    )


@router.post("/categories")
def category(b: CategoryInput, db=Depends(get_db), u=Depends(access)):
    def create():
        r = Category(code=b.code, name=b.name)
        db.add(r)
        db.flush()
        return inv.view(r)

    return change(db, u, b, "category", create, True)


@router.get("/documents")
def documents(
    site_id: int | None = None,
    asset_id: int | None = None,
    category_id: int | None = None,
    q: str = Query("", max_length=100),
    include_deleted: bool = False,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    def read():
        query = select(Document)
        if not include_deleted or u.role != "admin":
            query = query.where(Document.deleted_at.is_(None))
        if site_id:
            query = query.where(Document.site_id == site_id)
        if asset_id:
            query = query.where(Document.asset_id == asset_id)
        if category_id:
            query = query.where(Document.category_id == category_id)
        if q:
            query = query.where(Document.title.icontains(q))
        if u.role == "operator":
            query = query.where(Document.visibility != "admin")
        if u.role == "technician":
            query = query.where(
                Document.visibility == "technician",
                Document.site_id.in_(s.visible_sites(db, u)),
            )
        rows = db.scalars(
            query.order_by(Document.id.desc()).offset(offset).limit(50)
        ).all()
        return {
            "items": [inv.view(d) for d in rows],
            "next_offset": offset + 50 if len(rows) == 50 else None,
        }

    return run(db, u, read)


@router.post("/documents")
def document_create(b: DocumentInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "document_create", lambda: s.create_document(db, u, b))


@router.get("/documents/{id}")
def document(
    id: int,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    return run(db, u, lambda: s.document_detail(db, u, s.doc_access(db, u, id), offset))


@router.post("/documents/{id}/versions")
def version(id: int, b: VersionInput, db=Depends(get_db), u=Depends(access)):
    # Reauthorize before the idempotency cache as well as before storage mutation.
    def apply():
        d = s.doc_access(db, u, id, True)
        return field.mutation(
            db,
            u,
            "document_version_" + str(id),
            b,
            lambda: s.upload_version(db, u, d, b),
        )

    return run(db, u, apply, True)


@router.post("/documents/{id}/control")
def control(id: int, b: DocumentControl, db=Depends(get_db), u=Depends(access)):
    return change(
        db,
        u,
        b,
        "document_control_" + str(id),
        lambda: s.control_document(db, u, s.doc_access(db, u, id, True), b),
        True,
    )


@router.get("/documents/{id}/versions/{version_id}/download")
def download(id: int, version_id: int, db=Depends(get_db), u=Depends(access)):
    def read():
        d = s.doc_access(db, u, id)
        v = field.get(db, Version, version_id)
        raw = s.download(db, u, d, v)
        s.doc_event(db, u, d, "download", "Authenticated document retrieval", v.id)
        db.commit()
        return Response(
            raw,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="document-{d.id}-v{v.number}.{v.filename.rsplit(".",1)[-1]}"',
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "no-store",
            },
        )

    return run(db, u, read)


@router.get("/tower-context")
def tower_context(site_id: int, db=Depends(get_db), u=Depends(access)):
    def read():
        tower = field.tower(db, u, site_id)
        from app.equipment.models import Asset, Location
        from app.models.entities import Incident, Device

        assets = db.scalars(
            select(Asset)
            .join(Location, Location.id == Asset.location_id)
            .where(Location.site_id == site_id)
            .order_by(Asset.id)
            .limit(50)
        ).all()
        incidents = db.scalars(
            select(Incident)
            .where(
                __import__("sqlalchemy").or_(
                    Incident.site_id == site_id,
                    Incident.device_id.in_(
                        select(Device.id).where(Device.wisp_site_id == site_id)
                    ),
                )
            )
            .order_by(Incident.id.desc())
            .limit(50)
        ).all()
        return {
            "tower": tower,
            "assets": [
                {
                    "id": a.id,
                    "asset_tag": a.asset_tag,
                    "state": a.state,
                    "warranty_end": (
                        a.warranty_end.isoformat() if a.warranty_end else None
                    ),
                }
                for a in assets
            ],
            "incidents": [
                {"id": i.id, "title": i.title, "status": i.status} for i in incidents
            ],
        }

    return run(db, u, read)
