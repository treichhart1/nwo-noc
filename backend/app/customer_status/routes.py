from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from app.core.database import get_db
from app.field.routes import access, run
from app.field import service as field
from app.models.entities import Incident
from app.notifications.models import NotificationMaintenance, NotificationPlan
from . import service as s
from .models import *
from .contracts import *

router = APIRouter(
    prefix="/api/customer-status/v1",
    tags=["Private customer communications"],
    dependencies=[Depends(access)],
)


def read(db, u, fn):
    return run(db, u, lambda: (s.reader(u), fn())[1])


def change(db, u, b, name, fn, administrator=False):
    def call():
        (s.admin if administrator else s.reader)(u)
        return field.mutation(db, u, "customer-status:" + name, b, fn)

    return run(db, u, call, True)


@router.get("/me")
def me(u=Depends(access)):
    s.reader(u)
    return {
        "role": u.role,
        "external_delivery_enabled": False,
        "customer_accounts_owned_by": "Powercode",
        "public_approval_independent_of_sms": True,
    }


@router.get("/policy")
def policy(db=Depends(get_db), u=Depends(access)):
    return read(db, u, lambda: s.policy(db))


@router.post("/policy")
def configure(b: PolicyInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "configure", lambda: s.configure(db, u, b), True)


@router.get("/areas")
def areas(db=Depends(get_db), u=Depends(access)):
    return read(
        db,
        u,
        lambda: {
            "items": [
                s.view(a)
                for a in db.scalars(select(Area).order_by(Area.id).limit(1000))
            ]
        },
    )


@router.post("/areas")
def area(b: AreaInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "area", lambda: s.area(db, u, b), True)


@router.get("/sources")
def sources(
    offset: int = Query(0, ge=0, le=10000), db=Depends(get_db), u=Depends(access)
):
    return read(
        db,
        u,
        lambda: {
            "incidents": [
                {
                    "id": i.id,
                    "title": i.title,
                    "status": i.status,
                    "site_id": i.site_id,
                    "revision": i.revision,
                }
                for i in db.scalars(
                    select(Incident)
                    .order_by(Incident.started_at.desc())
                    .offset(offset)
                    .limit(50)
                )
            ],
            "maintenance": [
                s.view(i)
                for i in db.scalars(
                    select(NotificationMaintenance)
                    .order_by(NotificationMaintenance.id.desc())
                    .offset(offset)
                    .limit(50)
                )
            ],
            "detection": "Existing NOC-97 candidates and native incidents; no second incident engine",
        },
    )


@router.post("/cases")
def create(b: SourceInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "case", lambda: s.create(db, u, b))


@router.get("/cases")
def cases(
    incident_id: int | None = Query(None, ge=1),
    site_id: int | None = Query(None, ge=1),
    area_id: int | None = Query(None, ge=1),
    since: str | None = None,
    until: str | None = None,
    offset: int = Query(0, ge=0, le=10000),
    db=Depends(get_db),
    u=Depends(access),
):
    def call():
        q = select(Publication)
        if incident_id:
            q = q.where(Publication.incident_id == incident_id)
        if site_id:
            q = q.where(
                (
                    Publication.incident_id.in_(
                        select(Incident.id).where(Incident.site_id == site_id)
                    )
                )
                | (
                    Publication.maintenance_id.in_(
                        select(NotificationMaintenance.id).where(
                            NotificationMaintenance.target["site_id"].as_integer()
                            == site_id
                        )
                    )
                )
            )
        # Public area filters use frozen approved labels, never private tower names.
        if area_id:
            label = s.get(db, Area, area_id).label
            from sqlalchemy.dialects.postgresql import JSONB
            from sqlalchemy import cast

            q = q.where(
                Publication.id.in_(
                    select(Revision.publication_id).where(
                        cast(Revision.payload["areas"], JSONB).contains([label])
                    )
                )
            )
        for raw, lower in [(since, True), (until, False)]:
            if raw:
                dt = __import__("datetime").datetime.fromisoformat(raw)
                if not dt.tzinfo:
                    raise ValueError("Aware date filters required")
                q = q.where(
                    Publication.created_at >= dt
                    if lower
                    else Publication.created_at <= dt
                )
        rows = list(
            db.scalars(q.order_by(Publication.id.desc()).offset(offset).limit(51))
        )
        return {
            "items": [s.view(r) for r in rows[:50]],
            "next_offset": offset + 50 if len(rows) > 50 else None,
        }

    return read(db, u, call)


@router.get("/cases/{id}")
def case(id: int, db=Depends(get_db), u=Depends(access)):
    def call():
        p = s.get(db, Publication, id)
        return {
            **s.view(p),
            "source": s.source(db, p)[1],
            "revisions": [
                s.view(r)
                for r in db.scalars(
                    select(Revision)
                    .where(Revision.publication_id == id)
                    .order_by(Revision.id.desc())
                    .limit(50)
                )
            ],
            "messages": [
                s.message_view(db, m)
                for m in db.scalars(
                    select(Message)
                    .where(Message.publication_id == id)
                    .order_by(Message.id.desc())
                    .limit(50)
                )
            ],
            "history": [
                s.view(e)
                for e in db.scalars(
                    select(Event)
                    .where(Event.publication_id == id)
                    .order_by(Event.id.desc())
                    .limit(50)
                )
            ],
            "recoveries": [
                s.view(r)
                for r in db.scalars(
                    select(Recovery)
                    .where(Recovery.publication_id == id)
                    .order_by(Recovery.id.desc())
                    .limit(20)
                )
            ],
        }

    return read(db, u, call)


@router.get("/cases/{id}/impact")
def impact(id: int, db=Depends(get_db), u=Depends(access)):
    return read(db, u, lambda: s.impact_preview(db, s.get(db, Publication, id)))


@router.post("/cases/{id}/recovery")
def recovery(id: int, b: Mutation, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "recovery", lambda: s.assess(db, u, s.get(db, Publication, id), b)
    )


@router.post("/cases/{id}/revisions")
def draft(id: int, b: DraftInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "draft", lambda: s.draft(db, u, s.get(db, Publication, id), b)
    )


@router.post("/revisions/{id}/transition")
def transition(id: int, b: Transition, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "transition", lambda: s.transition(db, u, s.get(db, Revision, id), b)
    )


@router.post("/cases/{id}/messages")
def message(id: int, b: MessageInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db,
        u,
        b,
        "message",
        lambda: s.prepare_message(db, u, s.get(db, Publication, id), b),
    )


@router.post("/messages/{id}/decision")
def decide(id: int, b: MessageDecision, db=Depends(get_db), u=Depends(access)):
    return change(
        db,
        u,
        b,
        "message-decision",
        lambda: s.decide_message(db, u, s.get(db, Message, id), b),
        True,
    )


@router.post("/templates/initialize")
def templates(b: Mutation, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "templates", lambda: s.install_templates(db, u, b), True)
