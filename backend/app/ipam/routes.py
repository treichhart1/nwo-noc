"""Authenticated operator documentation APIs. No configuration deployment endpoints."""

from datetime import datetime, timezone
from typing import Literal
from ipaddress import ip_network, ip_address
from fastapi import APIRouter, Depends, Query, HTTPException, Response
from pydantic import Field
from sqlalchemy import select, func, or_, cast, String, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from app.api.deps import require_admin
from app.core.database import get_db
from app.customers.routes import customer_operator
from app.customers.contracts import SafeModel
from app.models.entities import Site, Device, NetworkLink, Incident, AuditLog
from app.customers.models import CustomerService, CustomerAccount
from . import service as s
from .models import (
    VRF,
    VLAN,
    Interface,
    Prefix,
    Pool,
    Assignment,
    DNSRecord,
    Observation,
    ImportBatch,
    Change,
    ReadBudget,
)
from .contracts import (
    VRFInput,
    VLANInput,
    InterfaceInput,
    PrefixInput,
    PoolInput,
    AssignmentInput,
    Reason,
    Suggestion,
    DiscoveryInput,
    ImportInput,
    Approval,
)
from . import discovery


def access(user=Depends(customer_operator), db=Depends(get_db)):
    now = s.clock().replace(second=0, microsecond=0)
    try:
        stmt = insert(ReadBudget).values(user_id=user.id, minute=now, count=1)
        from sqlalchemy import case

        count = db.scalar(
            stmt.on_conflict_do_update(
                index_elements=[ReadBudget.user_id],
                set_={
                    "minute": now,
                    "count": case(
                        (ReadBudget.minute == now, ReadBudget.count + 1), else_=1
                    ),
                },
            ).returning(ReadBudget.count)
        )
        db.commit()
        if count > 120:
            raise HTTPException(429, "IPAM request budget exceeded; retry next minute")
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "IPAM access budget unavailable") from None
    return user


router = APIRouter(prefix="/api/ipam", tags=["AI IPAM"], dependencies=[Depends(access)])


def run(db, fn, write=False):
    try:
        if db.get_bind().dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        db.execute(text("SET LOCAL statement_timeout='5000ms'"))
        result = fn()
        if write:
            db.commit()
        return result
    except LookupError:
        db.rollback()
        raise HTTPException(404, "IPAM record unavailable") from None
    except ValueError:
        db.rollback()
        raise HTTPException(
            409,
            "IPAM validation conflict; review address, hierarchy, routing context, ownership and current mappings",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            503,
            "IPAM database unavailable or constraint conflict; no partial allocation committed",
        ) from None


@router.get("")
def dashboard(db=Depends(get_db)):
    def read():
        counts = {
            m.__tablename__: db.scalar(select(func.count()).select_from(m))
            for m in [VRF, Prefix, VLAN, Interface, Assignment, Observation]
        }
        grouped = db.execute(
            select(Prefix.scope, func.family(Prefix.network), func.count()).group_by(
                Prefix.scope, func.family(Prefix.network)
            )
        ).all()
        return {
            "counts": counts,
            "prefix_families": [
                {"scope": a, "family": b, "prefixes": c} for a, b, c in grouped
            ],
            "discovery_enabled": __import__(
                "app.core.config", fromlist=["settings"]
            ).settings.ipam_discovery_enabled,
            "read_only_network": True,
            "documentation_only": True,
            "changes": [
                s.view(r)
                for r in db.scalars(select(Change).order_by(Change.id.desc()).limit(20))
            ],
        }

    return run(db, read)


@router.get("/vrfs")
def vrfs(db=Depends(get_db)):
    return run(
        db,
        lambda: [
            s.view(r) for r in db.scalars(select(VRF).order_by(VRF.id).limit(500))
        ],
    )


@router.post("/vrfs")
def vrf_create(b: VRFInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.create_vrf(db, b, u.id)), True)


@router.get("/vlans")
def vlans(
    vrf_id: int | None = Query(None, ge=1),
    site_id: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
):
    q = select(VLAN)
    if vrf_id:
        q = q.where(VLAN.vrf_id == vrf_id)
    if site_id:
        q = q.where(VLAN.site_id == site_id)
    return run(
        db,
        lambda: [
            s.view(r) for r in db.scalars(q.order_by(VLAN.id).offset(offset).limit(100))
        ],
    )


@router.post("/vlans")
def vlan_create(b: VLANInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.create_vlan(db, b, u.id)), True)


@router.get("/interfaces")
def interfaces(device_id: int | None = Query(None, ge=1), db=Depends(get_db)):
    q = select(Interface)
    if device_id:
        q = q.where(Interface.device_id == device_id)
    return run(
        db, lambda: [s.view(r) for r in db.scalars(q.order_by(Interface.id).limit(200))]
    )


@router.post("/interfaces")
def interface_create(b: InterfaceInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.create_interface(db, b, u.id)), True)


@router.get("/prefixes")
def prefixes(
    vrf_id: int | None = Query(None, ge=1),
    site_id: int | None = Query(None, ge=1),
    vlan_id: int | None = Query(None, ge=1),
    family: int | None = Query(None, ge=4, le=6),
    scope: str | None = Query(
        None, pattern="^(public|private|shared|documentation|other)$"
    ),
    q: str = Query("", max_length=80),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
):
    def read():
        query = select(Prefix)
        for col, value in [
            (Prefix.vrf_id, vrf_id),
            (Prefix.site_id, site_id),
            (Prefix.vlan_id, vlan_id),
            (Prefix.scope, scope),
        ]:
            if value is not None:
                query = query.where(col == value)
        if family:
            query = query.where(func.family(Prefix.network) == family)
        if q:
            try:
                value = str(ip_network(q, strict=False))
                query = query.where(Prefix.network.op("&&")(value))
            except ValueError:
                query = query.where(Prefix.purpose.contains(q, autoescape=True))
        rows = list(db.scalars(query.order_by(Prefix.id).offset(offset).limit(50)))
        usage = s.utilization_many(db, rows)
        return {
            "items": [s.view(p) | {"utilization": usage[p.id]} for p in rows],
            "offset": offset,
            "next_offset": offset + 50 if len(rows) == 50 else None,
        }

    return run(db, read)


@router.post("/prefixes")
def prefix_create(b: PrefixInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.create_prefix(db, b, u.id)), True)


@router.get("/prefixes/{ident}")
def prefix_detail(ident: int, db=Depends(get_db)):
    def read():
        p = s.required(db, Prefix, ident)
        return s.view(p) | {
            "utilization": s.utilization(db, p),
            "pools": [
                s.view(r)
                for r in db.scalars(
                    select(Pool).where(Pool.prefix_id == p.id).limit(200)
                )
            ],
            "recommendations": s.recommendations(db, p),
            "child_prefixes": [
                s.view(r)
                for r in db.scalars(
                    select(Prefix)
                    .where(Prefix.parent_id == p.id)
                    .order_by(Prefix.id)
                    .limit(200)
                )
            ],
            "discovery_context": "Documentation and observations do not establish network reachability; use shared Down Detector and topology views",
        }

    return run(db, read)


@router.post("/pools")
def pool_create(b: PoolInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.create_pool(db, b, u.id)), True)


@router.post("/suggestions")
def suggestions(b: Suggestion, db=Depends(get_db)):
    return run(db, lambda: s.suggest_prefixes(db, b))


@router.get("/assignments")
def assignments(
    vrf_id: int | None = Query(None, ge=1),
    prefix_id: int | None = Query(None, ge=1),
    site_id: int | None = Query(None, ge=1),
    device_id: int | None = Query(None, ge=1),
    service_id: int | None = Query(None, ge=1),
    customer_id: int | None = Query(None, ge=1),
    vlan_id: int | None = Query(None, ge=1),
    state: str | None = Query(None, pattern="^(assigned|reserved|released)$"),
    q: str = Query("", max_length=100),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
):
    def read():
        query = (
            select(Assignment)
            .join(Prefix, Prefix.id == Assignment.prefix_id)
            .outerjoin(CustomerService, CustomerService.id == Assignment.service_id)
            .outerjoin(
                CustomerAccount, CustomerAccount.id == CustomerService.customer_id
            )
            .outerjoin(Device, Device.id == Assignment.device_id)
        )
        for col, value in [
            (Assignment.vrf_id, vrf_id),
            (Assignment.prefix_id, prefix_id),
            (Prefix.site_id, site_id),
            (Assignment.device_id, device_id),
            (Assignment.service_id, service_id),
            (CustomerService.customer_id, customer_id),
            (Prefix.vlan_id, vlan_id),
            (Assignment.state, state),
        ]:
            if value is not None:
                query = query.where(col == value)
        if q:
            try:
                query = query.where(
                    Assignment.address.op("<<=")(str(ip_network(q, strict=False)))
                )
            except ValueError:
                query = query.where(
                    or_(
                        Assignment.owner.contains(q, autoescape=True),
                        Assignment.dns_name.contains(q, autoescape=True),
                        Device.name.contains(q, autoescape=True),
                        CustomerService.external_id.contains(q, autoescape=True),
                        CustomerAccount.external_id.contains(q, autoescape=True),
                    )
                )
        rows = list(db.scalars(query.order_by(Assignment.id).offset(offset).limit(100)))
        return {
            "items": [s.view(r) for r in rows],
            "offset": offset,
            "next_offset": offset + 100 if len(rows) == 100 else None,
        }

    return run(db, read)


@router.post("/assignments")
def allocate(b: AssignmentInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.view(s.allocate(db, b, u.id)), True)


@router.post("/assignments/{ident}/assign")
def assign(ident: int, b: AssignmentInput, db=Depends(get_db), u=Depends(access)):
    return run(
        db,
        lambda: s.view(
            s.allocate(db, b, u.id, update=s.required(db, Assignment, ident))
        ),
        True,
    )


@router.post("/assignments/{ident}/release")
def release(ident: int, b: Reason, db=Depends(get_db), u=Depends(access)):
    return run(
        db,
        lambda: s.view(
            s.release(db, s.required(db, Assignment, ident), b.reason, u.id)
        ),
        True,
    )


@router.get("/assignments/{ident}/history")
def history(ident: int, db=Depends(get_db)):
    return run(
        db,
        lambda: {
            "assignment": s.view(s.required(db, Assignment, ident)),
            "changes": [
                s.view(r)
                for r in db.scalars(
                    select(Change)
                    .where(
                        Change.entity == "ipam_assignments", Change.entity_id == ident
                    )
                    .order_by(Change.id.desc())
                    .limit(100)
                )
            ],
            "dns": [
                s.view(r)
                for r in db.scalars(
                    select(DNSRecord).where(DNSRecord.assignment_id == ident).limit(100)
                )
            ],
        },
    )


@router.get("/reconciliation")
def reconciliation(vrf_id: int = Query(ge=1), db=Depends(get_db)):
    return run(db, lambda: s.reconcile(db, vrf_id))


@router.get("/observations")
def observations(
    vrf_id: int = Query(ge=1),
    device_id: int | None = Query(None, ge=1),
    kind: str | None = Query(None, max_length=30),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
):
    q = select(Observation).where(Observation.vrf_id == vrf_id)
    if device_id:
        q = q.where(Observation.device_id == device_id)
    if kind:
        q = q.where(Observation.kind == kind)
    return run(
        db,
        lambda: {
            "items": [
                s.view(r)
                | {
                    "stale": s.clock() - r.observed_at
                    > __import__("datetime").timedelta(minutes=15),
                    "authoritative": False,
                }
                for r in db.scalars(
                    q.order_by(Observation.id.desc()).offset(offset).limit(100)
                )
            ]
        },
    )


@router.post("/discovery/recorded")
def recorded(b: DiscoveryInput, db=Depends(get_db), u=Depends(require_admin)):
    return run(db, lambda: discovery.ingest(db, b, u.id), True)


@router.get("/discovery/inventory-preview")
def inventory_preview(vrf_id: int = Query(ge=1), db=Depends(get_db)):
    return run(db, lambda: discovery.inventory_preview(db, vrf_id))


class Collect(SafeModel):
    device_id: int = Field(ge=1)
    vrf_id: int = Field(ge=1)


@router.post("/discovery/collect")
def collect(b: Collect, db=Depends(get_db), u=Depends(require_admin)):
    def work():
        try:
            return discovery.collect(db, b.device_id, b.vrf_id, u.id)
        except discovery.IntegrationFailure:
            raise ValueError("Read-only discovery disabled or unavailable") from None

    return run(db, work, True)


@router.post("/imports/preview")
def preview(b: ImportInput, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.preview_import(db, b, u.id), True)


@router.post("/imports/{ident}/apply")
def apply(ident: int, b: Approval, db=Depends(get_db), u=Depends(access)):
    return run(db, lambda: s.apply_import(db, ident, b, u.id), True)


@router.get("/export.csv")
def export(
    vrf_id: int = Query(ge=1),
    customer_data: bool = False,
    db=Depends(get_db),
    u=Depends(access),
):
    if customer_data and u.role != "admin":
        raise HTTPException(
            403, "Administrator approval required for customer allocation export"
        )

    def work():
        result = s.export_csv(db, vrf_id, customer_data=customer_data)
        db.add(
            AuditLog(
                user_id=u.id,
                action="ipam.csv_export",
                entity_type="ipam_vrfs",
                entity_id=str(vrf_id),
                details={"customer_data": customer_data},
            )
        )
        return Response(
            result,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="ipam-allocations.csv"',
                "Cache-Control": "no-store",
            },
        )

    return run(db, work, True)


@router.get("/topology")
def topology(
    vrf_id: int = Query(ge=1),
    site_id: int | None = Query(None, ge=1),
    db=Depends(get_db),
):
    def read():
        q = select(Prefix).where(Prefix.vrf_id == vrf_id)
        if site_id:
            q = q.where(Prefix.site_id == site_id)
        ps = list(db.scalars(q.order_by(Prefix.id).limit(100)))
        links = list(
            db.scalars(
                select(NetworkLink).where(
                    NetworkLink.id.in_([p.link_id for p in ps if p.link_id])
                )
            )
        )
        incidents = list(
            db.scalars(
                select(Incident)
                .where(
                    Incident.site_id.in_([p.site_id for p in ps if p.site_id]),
                    Incident.status.not_in(["resolved", "closed"]),
                )
                .order_by(Incident.id.desc())
                .limit(50)
            )
        )
        iface_query = (
            select(Interface)
            .join(Device, Device.id == Interface.device_id)
            .where(Interface.vrf_id == vrf_id)
        )
        if site_id:
            iface_query = iface_query.where(Device.wisp_site_id == site_id)
        return {
            "interfaces": [
                s.view(i)
                for i in db.scalars(iface_query.order_by(Interface.id).limit(200))
            ],
            "prefixes": [s.view(p) for p in ps],
            "links": [
                {
                    "id": l.id,
                    "name": l.name,
                    "a_site_id": l.a_site_id,
                    "z_site_id": l.z_site_id,
                    "a_device_id": l.a_device_id,
                    "z_device_id": l.z_device_id,
                }
                for l in links
            ],
            "shared_incidents": [
                {"id": i.id, "site_id": i.site_id, "title": i.title} for i in incidents
            ],
            "equipment_changes": 0,
            "root_cause_inferred": False,
        }

    return run(db, read)


@router.get("/lookup")
def lookup(
    vrf_id: int = Query(ge=1), address: str = Query(max_length=60), db=Depends(get_db)
):
    def read():
        a = str(ip_address(address))
        s.required(db, VRF, vrf_id)
        doc = db.scalar(
            select(Assignment).where(
                Assignment.vrf_id == vrf_id,
                Assignment.address == a,
                Assignment.state.in_(s.ACTIVE),
            )
        )
        obs = list(
            db.scalars(
                select(Observation)
                .where(Observation.vrf_id == vrf_id, Observation.address == a)
                .order_by(Observation.observed_at.desc())
                .limit(20)
            )
        )
        p = db.scalar(
            select(Prefix)
            .where(
                Prefix.vrf_id == vrf_id,
                Prefix.kind == "subnet",
                Prefix.network.op(">>=")(a),
            )
            .limit(1)
        )
        classification = (
            doc.state
            if doc
            else (
                "observed_unverified"
                if obs
                else (
                    "available"
                    if p
                    and s.bounds(p.network)[0]
                    <= int(ip_address(a))
                    <= s.bounds(p.network)[1]
                    and (not p.gateway or a != str(p.gateway))
                    else "unknown"
                )
            )
        )
        return {
            "address": a,
            "vrf_id": vrf_id,
            "classification": classification,
            "assignment": s.view(doc) if doc else None,
            "observations": [s.view(o) for o in obs],
            "availability_basis": "Documentation-only candidate, never proof of live availability",
            "requires_operator_review": True,
        }

    return run(db, read)


class VLANStatus(Reason):
    status: Literal["planned", "documented", "retired"]


@router.post("/vlans/{ident}/status")
def vlan_status(ident: int, b: VLANStatus, db=Depends(get_db), u=Depends(access)):
    def work():
        s.lock(db)
        row = s.required(db, VLAN, ident)
        before = s.view(row)
        row.status = b.status
        s.record(db, u.id, row, "vlan_status_updated", b.reason, before)
        return s.view(row)

    return run(db, work, True)


@router.get("/imports/{ident}")
def import_status(ident: int, db=Depends(get_db)):
    return run(db, lambda: s.view(s.required(db, ImportBatch, ident)))
