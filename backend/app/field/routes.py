from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select, func, case, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.api.deps import current_user
from app.core.database import get_db
from app.models.entities import Site, User
from app.equipment.models import RequestBudget
from app.equipment import service as inventory
from . import service as s
from .models import QRLabel, Template, WorkOrder, Event, Attachment, Grant
from .contracts import (
    Mutation,
    LabelInput,
    GrantInput,
    TemplateInput,
    WorkInput,
    Update,
    Note,
    InspectionInput,
    ReplacementInput,
    Upload,
    Parts,
)


def access(u=Depends(current_user), db=Depends(get_db)):
    try:
        if u.role not in {"admin", "operator", "technician"}:
            s.audit(db, u.id, "access_denied", "field_operations", "private")
            db.commit()
            raise HTTPException(403, "Field access unavailable")
        minute = s.now().replace(second=0, microsecond=0)
        stmt = insert(RequestBudget).values(user_id=u.id, minute=minute, count=1)
        count = db.scalar(
            stmt.on_conflict_do_update(
                index_elements=[RequestBudget.user_id],
                set_={
                    "minute": minute,
                    "count": case(
                        (
                            RequestBudget.minute == minute,
                            func.least(RequestBudget.count + 1, 121),
                        ),
                        else_=1,
                    ),
                },
            ).returning(RequestBudget.count)
        )
        db.commit()
        if count > 120:
            raise HTTPException(429, "Field request budget exceeded; retry next minute")
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Field authorization audit unavailable") from None
    return u


router = APIRouter(
    prefix="/api/field", tags=["Field maintenance"], dependencies=[Depends(access)]
)


def run(db, u, fn, write=False):
    try:
        if write:
            inventory.lock(db)
        else:
            db.execute(text("SET LOCAL statement_timeout='5000ms'"))
        result = fn()
        if write:
            db.commit()
        return result
    except PermissionError:
        db.rollback()
        s.audit(db, u.id, "action_denied", "field_operations", "private")
        db.commit()
        raise HTTPException(
            403, "Field action not authorized for this assignment"
        ) from None
    except LookupError:
        db.rollback()
        raise HTTPException(404, "Field record or QR unavailable") from None
    except (ValueError, IntegrityError):
        db.rollback()
        raise HTTPException(
            409,
            "Field validation conflict; refresh revision and review assignment, lifecycle, checklist and HTTPS origin. No partial changes committed.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            503,
            "Field storage unavailable; retain draft and retry with the same operation key",
        ) from None


def change(db, u, action, b, fn, work_id=None, administrator=False):
    def apply():
        if administrator:
            s.admin(u)
        w = s.work_access(db, u, work_id, True) if work_id else None
        return s.mutation(db, u, action, b, lambda: fn(w))

    return run(db, u, apply, True)


@router.get("/me")
def me(u=Depends(access)):
    return {"id": u.id, "username": u.username, "role": u.role, "offline_sync": False}


@router.get("/people")
def people(db=Depends(get_db), u=Depends(access)):
    return run(
        db,
        u,
        lambda: (
            s.admin(u),
            [
                {"id": x.id, "username": x.username}
                for x in db.scalars(
                    select(User)
                    .where(
                        User.is_active.is_(True), User.role.in_(["technician", "admin"])
                    )
                    .order_by(User.id)
                    .limit(200)
                )
            ],
        )[1],
    )


@router.get("/towers")
def towers(
    offset: int = Query(0, ge=0, le=100000), db=Depends(get_db), u=Depends(access)
):
    def read():
        q = select(Site)
        if u.role == "technician":
            q = q.where(Site.id.in_(s.scope(db, u)))
        rows = db.scalars(q.order_by(Site.id).offset(offset).limit(50)).all()
        return {
            "items": [
                {"id": x.id, "name": x.name, "site_type": x.site_type} for x in rows
            ],
            "next_offset": offset + 50 if len(rows) == 50 else None,
        }

    return run(db, u, read)


@router.get("/towers/{id}")
def tower(
    id: int,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    return run(db, u, lambda: s.tower(db, u, id, offset))


@router.get("/assets/{id}")
def asset(id: int, db=Depends(get_db), u=Depends(access)):
    return run(db, u, lambda: s.asset_detail(db, u, id))


@router.get("/qr/{identifier}")
def qr(identifier: UUID, db=Depends(get_db), u=Depends(access)):
    return run(db, u, lambda: s.resolve(db, u, identifier))


@router.get("/labels")
def labels(
    site_id: int | None = None,
    asset_id: int | None = None,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    def read():
        s.admin(u)
        q = select(QRLabel)
        if site_id:
            q = q.where(QRLabel.site_id == site_id)
        if asset_id:
            q = q.where(QRLabel.asset_id == asset_id)
        rows = db.scalars(q.order_by(QRLabel.id.desc()).offset(offset).limit(50)).all()
        return {
            "items": [inventory.view(r) for r in rows],
            "next_offset": offset + 50 if len(rows) == 50 else None,
        }

    return run(db, u, read)


@router.post("/labels")
def make_label(b: LabelInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, "qr_label", b, lambda _: s.label(db, u, b), administrator=True)


@router.post("/labels/provision-towers")
def provision(b: Mutation, db=Depends(get_db), u=Depends(access)):
    def all_sites(_):
        ids = db.scalars(select(Site.id).order_by(Site.id).limit(1001)).all()
        if len(ids) > 1000:
            raise ValueError("Provision individual labels beyond1000 sites")
        return {
            "labels": [
                s.label(
                    db,
                    u,
                    LabelInput(
                        site_id=id, reason=b.reason, operation_key=b.operation_key
                    ),
                )
                for id in ids
            ]
        }

    return change(db, u, "qr_provision", b, all_sites, administrator=True)


@router.post("/labels/{id}/revoke")
def revoke(id: int, b: Mutation, db=Depends(get_db), u=Depends(access)):
    return change(
        db,
        u,
        "qr_revoke_" + str(id),
        b,
        lambda _: s.revoke(db, u, id),
        administrator=True,
    )


@router.get("/labels/{id}/print")
def printing(id: int, db=Depends(get_db), u=Depends(access)):
    def read():
        s.admin(u)
        r = s.get(db, QRLabel, id)
        if r.revoked_at:
            raise LookupError("Revoked label")
        return {
            "url": s.label_url(r),
            "identifier": r.identifier,
            "target_kind": "tower" if r.site_id else "asset",
            "target_id": r.site_id or r.asset_id,
            "svg": s.label_svg(r).decode(),
        }

    return run(db, u, read)


@router.post("/grants")
def grant(b: GrantInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, "site_grant", b, lambda _: s.grant(db, u, b), administrator=True
    )


@router.get("/grants")
def grants(
    offset: int = Query(0, ge=0, le=100000), db=Depends(get_db), u=Depends(access)
):
    return run(
        db,
        u,
        lambda: (
            s.admin(u),
            [
                inventory.view(g)
                for g in db.scalars(
                    select(Grant).order_by(Grant.id).offset(offset).limit(50)
                )
            ],
        )[1],
    )


@router.get("/templates")
def templates(
    offset: int = Query(0, ge=0, le=100000), db=Depends(get_db), u=Depends(access)
):
    return run(
        db,
        u,
        lambda: {
            "items": [
                inventory.view(t)
                for t in db.scalars(
                    select(Template)
                    .where(Template.active.is_(True))
                    .order_by(Template.id)
                    .offset(offset)
                    .limit(50)
                )
            ],
            "default_items": s.default_items(),
        },
    )


@router.post("/templates")
def template(b: TemplateInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, "template_version", b, lambda _: s.template(db, u, b), administrator=True
    )


@router.get("/work-orders")
def orders(
    offset: int = Query(0, ge=0, le=100000),
    site_id: int | None = None,
    status: str | None = Query(None, max_length=30),
    db=Depends(get_db),
    u=Depends(access),
):
    def read():
        q = select(WorkOrder)
        if u.role == "technician":
            q = q.where(WorkOrder.technician_id == u.id)
        if site_id:
            q = q.where(WorkOrder.site_id == site_id)
        if status:
            q = q.where(WorkOrder.status == status)
        rows = db.scalars(
            q.order_by(WorkOrder.id.desc()).offset(offset).limit(50)
        ).all()
        return {
            "items": [s.work_view(w) for w in rows],
            "next_offset": offset + 50 if len(rows) == 50 else None,
        }

    return run(db, u, read)


@router.post("/work-orders")
def create(b: WorkInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, "work_create", b, lambda _: s.create_work(db, u, b), administrator=True
    )


@router.get("/work-orders/{id}")
def work(
    id: int,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    return run(db, u, lambda: s.work_detail(db, u, s.work_access(db, u, id), offset))


@router.post("/work-orders/{id}/status")
def update(id: int, b: Update, db=Depends(get_db), u=Depends(access)):
    return change(
        db,
        u,
        "work_status_" + str(id),
        b,
        lambda w: s.update_work(db, u, w, b),
        work_id=id,
    )


@router.post("/work-orders/{id}/notes")
def note(id: int, b: Note, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, "work_note_" + str(id), b, lambda w: s.note(db, u, w, b), work_id=id
    )


@router.post("/work-orders/{id}/inspections")
def inspect(id: int, b: InspectionInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db,
        u,
        "work_inspect_" + str(id),
        b,
        lambda w: s.inspect(db, u, w, b),
        work_id=id,
    )


@router.post("/work-orders/{id}/replacement")
def replace(id: int, b: ReplacementInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db,
        u,
        "work_replace_" + str(id),
        b,
        lambda w: s.replacement(db, u, w, b),
        work_id=id,
    )


@router.post("/work-orders/{id}/parts")
def parts(id: int, b: Parts, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, "work_parts_" + str(id), b, lambda w: s.parts(db, u, w, b), work_id=id
    )


@router.post("/work-orders/{id}/attachments")
def upload(id: int, b: Upload, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, "work_upload_" + str(id), b, lambda w: s.upload(db, u, w, b), work_id=id
    )


@router.get("/attachments/{id}")
def download(id: int, db=Depends(get_db), u=Depends(access)):
    def read():
        r = s.get(db, Attachment, id)
        s.work_access(db, u, r.work_order_id)
        s.audit(db, u.id, "attachment_download", "field_attachment", r.id)
        db.commit()
        return Response(
            r.data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="field-{r.id}.{r.filename.rsplit(".",1)[-1]}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    return run(db, u, read)


@router.get("/history")
def history(
    site_id: int,
    asset_id: int | None = None,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    def read():
        s.site_access(db, u, site_id)
        q = select(Event).join(WorkOrder).where(WorkOrder.site_id == site_id)
        if asset_id:
            q = q.where(WorkOrder.asset_id == asset_id)
        if u.role == "technician":
            q = q.where(WorkOrder.technician_id == u.id)
        rows = db.scalars(q.order_by(Event.id.desc()).offset(offset).limit(50)).all()
        return {
            "items": [inventory.view(r) for r in rows],
            "next_offset": offset + 50 if len(rows) == 50 else None,
        }

    return run(db, u, read)


@router.get("/work-orders/{id}/resources")
def resources(
    id: int, q: str = Query("", max_length=80), db=Depends(get_db), u=Depends(access)
):
    def read():
        w = s.work_access(db, u, id, True)
        from app.equipment.models import Asset, Location, BulkBalance, Product

        query = select(Asset).where(
            Asset.state.in_(["available", "reserved"]),
            Asset.condition.in_(["new", "good", "refurbished"]),
        )
        if u.role == "technician":
            query = query.where(
                __import__("sqlalchemy").or_(
                    Asset.technician_id.is_(None), Asset.technician_id == u.id
                )
            )
        if q:
            query = query.where(Asset.asset_tag.icontains(q))
        sources = (
            select(BulkBalance, Location.name, Product.unit)
            .join(Location, Location.id == BulkBalance.location_id)
            .join(Product, Product.id == BulkBalance.product_id)
            .where(
                BulkBalance.state.in_(["available", "reserved"]),
                BulkBalance.quantity > 0,
            )
        )
        if u.role == "technician":
            sources = sources.where(
                Location.kind == "technician_stock", Location.technician_id == u.id
            )
        return {
            "bulk_sources": [
                inventory.view(b) | {"location_name": name, "unit": unit}
                for b, name, unit in db.execute(
                    sources.order_by(BulkBalance.id).limit(50)
                )
            ],
            "installation_locations": [
                {"id": l.id, "name": l.name}
                for l in db.scalars(
                    select(Location)
                    .where(Location.site_id == w.site_id, Location.kind == "tower")
                    .limit(50)
                )
            ],
            "spares": [
                {
                    "id": a.id,
                    "asset_tag": a.asset_tag,
                    "revision": a.revision,
                    "product_id": a.product_id,
                }
                for a in db.scalars(query.order_by(Asset.id).limit(50))
            ],
            "removed_locations": [
                {"id": l.id, "name": l.name, "kind": l.kind}
                for l in db.scalars(
                    select(Location)
                    .where(
                        Location.kind.in_(
                            ["warehouse", "repair", "returned", "retired"]
                        )
                    )
                    .order_by(Location.id)
                    .limit(100)
                )
            ],
        }

    return run(db, u, read)


@router.post("/technicians/{id}")
def technician_enable(id: int, b: Mutation, db=Depends(get_db), u=Depends(access)):
    def enable(_):
        target = s.get(db, User, id)
        if target.role == "admin" or not target.is_active:
            raise ValueError("Select active non-administrator user")
        previous = target.role
        target.role = "technician"
        db.flush()
        return {"id": target.id, "previous_role": previous, "role": target.role}

    return change(db, u, "technician_enable_" + str(id), b, enable, administrator=True)


@router.get("/inspections/{id}")
def inspection_record(id: int, db=Depends(get_db), u=Depends(access)):
    def read():
        from .models import Inspection

        r = s.get(db, Inspection, id)
        s.work_access(db, u, r.work_order_id)
        return inventory.view(r)

    return run(db, u, read)
