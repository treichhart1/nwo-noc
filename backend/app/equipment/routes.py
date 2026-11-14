"""Private inventory documentation and opaque attachments; no network actions."""

from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, Query, HTTPException, Response
from sqlalchemy import select, func, text, case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.api.deps import current_user, require_admin
from app.core.database import get_db
from app.models.entities import AuditLog, Device, Site, User
from .models import (
    Category,
    Manufacturer,
    Product,
    Location,
    Asset,
    Movement,
    BulkBalance,
    BulkMovement,
    Maintenance,
    Attachment,
    StockPolicy,
    ImportBatch,
    RequestBudget,
)
from .contracts import (
    CategoryInput,
    ProductInput,
    LocationInput,
    AssetInput,
    AssetMove,
    Replacement,
    BulkInput,
    MaintenanceInput,
    AttachmentInput,
    FinancialInput,
    CostInput,
    StockInput,
    ImportInput,
    Approval,
    DeviceImport,
)
from . import service as s


def access(user=Depends(current_user), db=Depends(get_db)):
    try:
        if user.role not in {"operator", "admin"}:
            s.audit(db, user.id, "access_denied", "equipment_inventory", "private")
            db.commit()
            raise HTTPException(403, "Inventory operator access required")
        minute = s.clock().replace(second=0, microsecond=0)
        stmt = insert(RequestBudget).values(user_id=user.id, minute=minute, count=1)
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
            raise HTTPException(
                429, "Inventory request budget exceeded; retry next minute"
            )
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Inventory access audit unavailable") from None
    return user


router = APIRouter(
    prefix="/api/equipment",
    tags=["Equipment inventory"],
    dependencies=[Depends(access)],
)


def run(db, fn, write=False):
    try:
        if db.get_bind().dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        db.execute(text("SET LOCAL statement_timeout='5000ms'"))
        result = fn()
        if write:
            db.commit()
        return result
    except PermissionError:
        db.rollback()
        raise HTTPException(
            403, "Administrator authorization required for financial data"
        ) from None
    except LookupError:
        db.rollback()
        raise HTTPException(404, "Equipment record unavailable") from None
    except (ValueError, IntegrityError):
        db.rollback()
        raise HTTPException(
            409,
            "Inventory validation conflict; review lifecycle, revisions, identity, locations, quantities and bindings. No partial changes committed.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            503, "Inventory database unavailable; retry after recovery"
        ) from None


@router.get("")
def summary(db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.dashboard(db, u.role == "admin"))


@router.get("/catalog")
def catalog(
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(100, ge=1, le=200),
    db=Depends(get_db),
):
    def read():
        products = db.execute(
            select(Product, Manufacturer.name, Category.name)
            .join(Manufacturer, Manufacturer.id == Product.manufacturer_id)
            .join(Category, Category.id == Product.category_id)
            .order_by(Product.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return {
            "products": [
                s.view(p) | {"manufacturer": maker, "category": cat}
                for p, maker, cat in products
            ],
            "next_offset": offset + limit if len(products) == limit else None,
            "categories": [
                s.view(r)
                for r in db.scalars(select(Category).order_by(Category.id).limit(500))
            ],
        }

    return run(db, read)


@router.post("/categories")
def category_create(b: CategoryInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.create_category(db, b, u.id)), True)


@router.post("/products")
def product_create(b: ProductInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.create_product(db, b, u.id)), True)


@router.get("/locations")
def locations(offset: int = Query(0, ge=0, le=100000), db=Depends(get_db)):
    return run(
        db,
        lambda: {
            "items": [
                s.view(r)
                for r in db.scalars(
                    select(Location).order_by(Location.id).offset(offset).limit(100)
                )
            ],
            "next_offset": (
                offset + 100
                if db.scalar(select(func.count()).select_from(Location)) > offset + 100
                else None
            ),
        },
    )


@router.post("/locations")
def location_create(b: LocationInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.create_location(db, b, u.id)), True)


@router.get("/technicians")
def technicians(db=Depends(get_db)):
    return run(
        db,
        lambda: [
            {"id": r.id, "username": r.username}
            for r in db.scalars(
                select(User)
                .where(User.is_active.is_(True), User.role.in_(["operator", "admin"]))
                .order_by(User.id)
                .limit(200)
            )
        ],
    )


@router.get("/assets")
def assets(
    q: str = Query("", max_length=100),
    state: str | None = Query(None, max_length=30),
    site_id: int | None = Query(None, ge=1),
    category_id: int | None = Query(None, ge=1),
    product_id: int | None = Query(None, ge=1),
    location_id: int | None = Query(None, ge=1),
    manufacturer_id: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    db=Depends(get_db),
):
    def read():
        query = s.asset_query(
            q=q,
            state=state,
            site_id=site_id,
            category_id=category_id,
            product_id=product_id,
            location_id=location_id,
            manufacturer_id=manufacturer_id,
        )
        rows = db.execute(
            query.add_columns(Product.model, Manufacturer.name, Location.name)
            .order_by(Asset.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return {
            "items": [
                s.view(a)
                | {"model": model, "manufacturer": maker, "location_name": location}
                for a, model, maker, location in rows
            ],
            "offset": offset,
            "next_offset": offset + limit if len(rows) == limit else None,
        }

    return run(db, read)


@router.post("/assets")
def asset_create(b: AssetInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.create_asset(db, b, u.id)), True)


@router.get("/assets/{ident}")
def asset_detail(ident: str, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.detail(db, s.resolve(db, ident), u.role == "admin"))


@router.get("/assets/{ident}/qr")
def qr(ident: str, db=Depends(get_db)):
    def read():
        a = s.resolve(db, ident)
        return {
            "asset_id": str(a.public_id),
            "url": "/?asset=" + str(a.public_id),
            "authentication_required": True,
            "contains_access_token": False,
        }

    return run(db, read)


@router.post("/assets/{ident}/move")
def move(ident: str, b: AssetMove, db=Depends(get_db), u=Depends(access)):
    return run(
        db, lambda: s.view(s.move_asset(db, s.resolve(db, ident), b, u.id)), True
    )


@router.post("/assets/{ident}/replace")
def replace(ident: str, b: Replacement, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.replace_asset(db, s.resolve(db, ident), b, u.id), True)


@router.post("/assets/{ident}/maintenance")
def maintenance_create(
    ident: str, b: MaintenanceInput, db=Depends(get_db), u=Depends(access)
):
    return run(
        db, lambda: s.view(s.add_maintenance(db, s.resolve(db, ident), b, u.id)), True
    )


@router.get("/maintenance/{ident}/attachments")
def attachments(ident: int, db=Depends(get_db), u=Depends(access)):
    def read():
        s.required(db, Maintenance, ident)
        return [
            s.view(r, exclude={"data"}) | {"unscanned": True, "download_only": True}
            for r in db.scalars(
                select(Attachment)
                .where(
                    Attachment.maintenance_id == ident,
                    (
                        True
                        if u.role == "admin"
                        else Attachment.restricted_financial.is_(False)
                    ),
                )
                .order_by(Attachment.id)
                .limit(5)
            )
        ]

    return run(db, read)


@router.post("/maintenance/{ident}/attachments")
def attachment_create(
    ident: int, b: AttachmentInput, db=Depends(get_db), u=Depends(access)
):
    if b.restricted_financial and u.role != "admin":
        raise HTTPException(403, "Administrator required for financial attachments")
    return run(
        db,
        lambda: s.add_attachment(db, s.required(db, Maintenance, ident), b, u.id),
        True,
    )


@router.get("/attachments/{ident}")
def attachment_download(ident: int, db=Depends(get_db), u=Depends(access)):
    def read():
        row = s.required(db, Attachment, ident)
        if row.restricted_financial and u.role != "admin":
            raise PermissionError()
        s.audit(db, u.id, "attachment_downloaded", "equipment_attachment", row.id)
        ext = {"image/png": "png", "image/jpeg": "jpg", "text/plain": "txt"}[
            row.media_type
        ]
        return Response(
            row.data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="equipment-attachment-{row.id}.{ext}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    return run(db, read, True)


@router.get("/bulk")
def bulk(
    offset: int = Query(0, ge=0, le=100000),
    location_id: int | None = Query(None, ge=1),
    db=Depends(get_db),
):
    def read():
        query = (
            select(BulkBalance, Product.model, Product.unit, Location.name)
            .join(Product, Product.id == BulkBalance.product_id)
            .join(Location, Location.id == BulkBalance.location_id)
        )
        if location_id:
            query = query.where(BulkBalance.location_id == location_id)
        rows = db.execute(
            query.order_by(BulkBalance.id).offset(offset).limit(100)
        ).all()
        return {
            "items": [
                s.view(b) | {"model": model, "unit": unit, "location": location}
                for b, model, unit, location in rows
            ],
            "next_offset": offset + 100 if len(rows) == 100 else None,
        }

    return run(db, read)


@router.post("/bulk/movements")
def bulk_move(b: BulkInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.bulk_move(db, b, u.id), True)


@router.get("/bulk/movements")
def bulk_history(
    product_id: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
):
    query = select(BulkMovement)
    if product_id:
        query = query.where(BulkMovement.product_id == product_id)
    return run(
        db,
        lambda: {
            "items": [
                s.view(r)
                for r in db.scalars(
                    query.order_by(BulkMovement.id.desc()).offset(offset).limit(100)
                )
            ]
        },
    )


@router.get("/planning")
def planning(db=Depends(get_db)):
    return run(db, lambda: s.planning(db))


@router.get("/stock-policies")
def stock_policies(db=Depends(get_db)):
    return run(
        db,
        lambda: [
            s.view(r)
            for r in db.scalars(select(StockPolicy).order_by(StockPolicy.id).limit(100))
        ],
    )


@router.post("/stock-policies")
def stock_create(b: StockInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.set_stock_policy(db, b, u.id)), True)


@router.put("/stock-policies/{ident}")
def stock_update(ident: int, b: StockInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.set_stock_policy(db, b, u.id, ident)), True)


@router.get("/financials")
def financial_summary(
    site_id: int | None = Query(None, ge=1),
    db=Depends(get_db),
    u=Depends(require_admin),
):
    return run(db, lambda: s.cost_report(db, site_id))


@router.put("/assets/{ident}/financial")
def financial_save(
    ident: str, b: FinancialInput, db=Depends(get_db), u=Depends(require_admin)
):
    return run(db, lambda: s.set_financial(db, s.resolve(db, ident), b, u.id), True)


@router.post("/costs")
def cost_create(b: CostInput, db=Depends(get_db), u=Depends(require_admin)):
    return run(db, lambda: s.add_cost(db, b, u.id), True)


@router.post("/imports/preview")
def import_preview(b: ImportInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.csv_preview(db, b, u.id, u.role == "admin"), True)


@router.post("/imports/devices-preview")
def devices_preview(b: DeviceImport, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.device_preview(db, b, u.id), True)


@router.post("/imports/{ident}/apply")
def import_apply(ident: int, b: Approval, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.apply_import(db, ident, b, u.id, u.role == "admin"), True)


@router.get("/imports/{ident}")
def import_status(ident: int, db=Depends(get_db), u=Depends(access)):
    def read():
        b = s.required(db, ImportBatch, ident)
        result = s.view(b, exclude={"rows"})
        if u.role == "admin":
            result["rows"] = b.rows
        else:
            result["rows"] = [
                {
                    "asset": r["asset"],
                    "financial_fields_restricted": bool(r.get("financial")),
                }
                for r in b.rows
            ]
        return result

    return run(db, read)


@router.get("/export.csv")
def export(
    q: str = Query("", max_length=100),
    site_id: int | None = Query(None, ge=1),
    state: str | None = Query(None, max_length=30),
    offset: int = Query(0, ge=0, le=100000),
    financial: bool = False,
    db=Depends(get_db),
    u=Depends(access),
):
    if financial and u.role != "admin":
        raise HTTPException(403, "Administrator required for financial export")

    def read():
        content, next_offset = s.export_assets(
            db, s.asset_query(q=q, site_id=site_id, state=state), offset, financial
        )
        s.audit(
            db,
            u.id,
            "csv_export",
            "equipment_inventory",
            "selected",
            {"financial_fields": financial, "offset": offset},
        )
        return Response(
            content,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="equipment-inventory.csv"',
                "Cache-Control": "no-store",
                "X-Next-Offset": str(next_offset) if next_offset is not None else "",
            },
        )

    return run(db, read, True)


from .contracts import MetadataPatch, ProductPlanning


@router.patch("/assets/{ident}")
def metadata(ident: str, b: MetadataPatch, db=Depends(get_db), u=Depends(access)):
    return run(
        db, lambda: s.view(s.patch_metadata(db, s.resolve(db, ident), b, u.id)), True
    )


@router.patch("/products/{ident}/planning")
def product_plan(ident: int, b: ProductPlanning, db=Depends(get_db), u=Depends(access)):
    return run(
        db,
        lambda: s.view(s.product_planning(db, s.required(db, Product, ident), b, u.id)),
        True,
    )


from .contracts import MaintenanceStatus


@router.post("/maintenance/{ident}/status")
def maintenance_status(
    ident: int, b: MaintenanceStatus, db=Depends(get_db), u=Depends(access)
):
    return run(
        db,
        lambda: s.view(
            s.maintenance_status(db, s.required(db, Maintenance, ident), b, u.id)
        ),
        True,
    )
