from fastapi import APIRouter, Depends, Query, Response, HTTPException
from sqlalchemy import select, func, or_
from app.core.database import get_db
from app.field.routes import access
from app.maintenance_ops.routes import run
from app.field import service as field
from app.equipment import service as inventory
from . import service as s, crypto
from .models import *
from .contracts import *

router = APIRouter(
    prefix="/api/applications",
    tags=["Customer applications"],
    dependencies=[Depends(access)],
)


def change(db, u, b, action, fn, admin=False, authorize=None):
    def call():
        if admin:
            field.admin(u)
        else:
            s.staff(u) if authorize is None else authorize()
        return field.mutation(
            db,
            u,
            "signup_" + action,
            b,
            fn,
            audit_reason="Customer application documentation; reason encrypted in scoped application history",
        )

    return run(db, u, call, True)


@router.get("/me")
def me(u=Depends(access)):
    s.staff(u)
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "states": STATES,
        "external_delivery": False,
    }


@router.get("/plans")
def plans(
    offset: int = Query(0, ge=0, le=100000), db=Depends(get_db), u=Depends(access)
):
    return run(
        db,
        u,
        lambda: (
            s.staff(u),
            [
                inventory.view(p)
                for p in db.scalars(
                    select(Plan).order_by(Plan.id).offset(offset).limit(100)
                )
            ],
        )[1],
    )


@router.post("/plans")
def plan(b: PlanInput, db=Depends(get_db), u=Depends(access)):
    def write():
        p = Plan(**b.model_dump(exclude={"operation_key", "reason"}))
        db.add(p)
        db.flush()
        s.audit(db, u, "plan_created", p.id)
        return inventory.view(p)

    return change(db, u, b, "plan_create", write, True)


@router.post("/plans/{id}")
def plan_update(id: int, b: PlanUpdate, db=Depends(get_db), u=Depends(access)):
    def write():
        p = field.get(db, Plan, id)
        if p.revision != b.expected_revision:
            raise ValueError("Plan changed; refresh before editing")
        for k, v in b.model_dump(
            exclude={"operation_key", "reason", "expected_revision"}
        ).items():
            setattr(p, k, v)
        p.revision += 1
        s.audit(db, u, "plan_updated", p.id)
        db.flush()
        return inventory.view(p)

    return change(db, u, b, "plan_update_" + str(id), write, True)


@router.get("/areas")
def areas(
    offset: int = Query(0, ge=0, le=100000), db=Depends(get_db), u=Depends(access)
):
    return run(
        db,
        u,
        lambda: (
            s.staff(u),
            [
                inventory.view(p)
                for p in db.scalars(
                    select(Area).order_by(Area.id).offset(offset).limit(100)
                )
            ],
        )[1],
    )


@router.post("/areas")
def area(b: AreaInput, db=Depends(get_db), u=Depends(access)):
    def write():
        if b.site_id:
            field.get(
                db, __import__("app.models.entities", fromlist=["Site"]).Site, b.site_id
            )
        if b.sector_id:
            sector = field.get(
                db,
                __import__("app.models.entities", fromlist=["Sector"]).Sector,
                b.sector_id,
            )
            if sector.site_id != b.site_id:
                raise ValueError("Sector/site mismatch")
        if (
            b.fiber_link_id
            and field.get(
                db,
                __import__("app.models.entities", fromlist=["NetworkLink"]).NetworkLink,
                b.fiber_link_id,
            ).link_type
            != "fiber"
        ):
            raise ValueError("Fiber link required")
        r = Area(**b.model_dump(exclude={"operation_key", "reason"}), updated_by=u.id)
        db.add(r)
        db.flush()
        s.audit(db, u, "coverage_area_created", r.id)
        return inventory.view(r)

    return change(db, u, b, "area_create", write, True)


@router.get("")
def listing(
    state: str | None = Query(None, max_length=40),
    technology: str | None = Query(None, max_length=20),
    plan_id: int | None = None,
    assigned_to: int | None = None,
    site_id: int | None = None,
    q: str = Query("", max_length=120),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    def read():
        s.staff(u)
        stmt = select(Application)
        if state:
            stmt = stmt.where(Application.state == state)
        if technology:
            stmt = stmt.where(Application.technology == technology)
        if plan_id:
            stmt = stmt.where(Application.plan_id == plan_id)
        if assigned_to:
            stmt = stmt.where(Application.assigned_to == assigned_to)
        if site_id:
            stmt = stmt.where(Application.proposed_site_id == site_id)
        if q:
            tests = [Application.reference.icontains(q)]
            if "@" in q:
                tests.append(
                    Application.email_hash == crypto.digest("email", q.lower().strip())
                )
            phone = __import__("app.customers.contracts", fromlist=["e164"]).e164(q)
            if phone:
                tests.append(Application.phone_hash == crypto.digest("phone", phone))
            stmt = stmt.where(or_(*tests))
        rows = db.scalars(
            stmt.order_by(Application.created_at.desc(), Application.id.desc())
            .offset(offset)
            .limit(50)
        ).all()
        counts = dict(
            db.execute(
                select(Application.state, func.count()).group_by(Application.state)
            ).all()
        )
        by_plan = dict(
            db.execute(
                select(Application.plan_id, func.count()).group_by(Application.plan_id)
            ).all()
        )
        by_site = dict(
            db.execute(
                select(Application.proposed_site_id, func.count()).group_by(
                    Application.proposed_site_id
                )
            ).all()
        )
        return {
            "items": [
                {
                    "id": a.id,
                    "reference": a.reference,
                    "state": a.state,
                    "technology": a.technology,
                    "plan_id": a.plan_id,
                    "assigned_to": a.assigned_to,
                    "created_at": a.created_at.isoformat(),
                    "age_days": (now() - a.created_at).days,
                    "revision": a.revision,
                    "redacted": bool(a.redacted_at),
                }
                for a in rows
            ],
            "next_offset": offset + 50 if len(rows) == 50 else None,
            "counts": counts,
            "by_plan": by_plan,
            "by_site": by_site,
        }

    return run(db, u, read)


@router.get("/work-context/{work_id}")
def context(work_id: int, db=Depends(get_db), u=Depends(access)):
    return run(db, u, lambda: s.work_context(db, u, work_id), True)


@router.get("/analytics/summary")
def acquisition_analytics(since:datetime,until:datetime,db=Depends(get_db),u=Depends(access)):
    from .acquisition import analytics
    if since.tzinfo is None or until.tzinfo is None or not since<until or (until-since).days>366:
        raise HTTPException(422,"Aware UTC range of at most366days required")
    return run(db,u,lambda:analytics(db,u,since,until),True)


@router.get("/{id}")
def detail(
    id: int,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(access),
):
    return run(db, u, lambda: s.detail(db, u, s.get(db, id), offset), True)


@router.post("/{id}/state")
def state(id: int, b: Update, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "state_" + str(id), lambda: s.update(db, u, s.get(db, id), b)
    )


@router.post("/{id}/qualify")
def qualify(id: int, b: QualifyInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "qualify_" + str(id), lambda: s.qualify(db, u, s.get(db, id), b)
    )


@router.post("/{id}/work-orders")
def work(id: int, b: WorkInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "work_" + str(id), lambda: s.make_work(db, u, s.get(db, id), b)
    )


@router.post("/{id}/surveys")
def survey(id: int, b: SurveyInput, db=Depends(get_db), u=Depends(access)):
    def auth():
        m = field.get(db, AppWork, b.app_work_id)
        if m.application_id != id:
            raise PermissionError("Survey application mismatch")
        s.work_access(db, u, m)

    return change(
        db,
        u,
        b,
        "survey_" + str(id),
        lambda: s.survey(db, u, s.get(db, id), b),
        authorize=auth,
    )


@router.post("/{id}/quotes")
def quote(id: int, b: QuoteInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "quote_" + str(id), lambda: s.quote_draft(db, u, s.get(db, id), b)
    )


@router.post("/{id}/control")
def control(id: int, b: Control, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "control_" + str(id), lambda: s.control(db, u, s.get(db, id), b), True
    )


@router.post("/{id}/notifications")
def notification(id: int, b: NotifyInput, db=Depends(get_db), u=Depends(access)):
    return change(
        db,
        u,
        b,
        "notification_" + str(id),
        lambda: s.notification(db, u, s.get(db, id), b),
    )


@router.post("/{id}/photos")
def upload(id: int, b: PhotoInput, db=Depends(get_db), u=Depends(access)):
    def auth():
        m = field.get(db, AppWork, b.app_work_id)
        if m.application_id != id:
            raise PermissionError("Photo application mismatch")
        s.work_access(db, u, m)

    return change(
        db,
        u,
        b,
        "photo_" + str(id),
        lambda: s.upload_photo(db, u, s.get(db, id), b),
        authorize=auth,
    )


@router.post("/photos/{id}/approve")
def photo_approve(id: int, b: Mutation, db=Depends(get_db), u=Depends(access)):
    def write():
        p = field.get(db, Photo, id)
        a = s.get(db, p.application_id)
        if not a.wrapped_key or not s.trusted_clean(p):
            raise ValueError("Clean nonredacted photo required")
        p.approved = True
        s.event(db, a, u, "photo_approved", b.reason, {"photo_id": p.id})
        return {"id": p.id, "approved": True}

    return change(db, u, b, "photo_approve_" + str(id), write, True)


@router.get("/photos/{id}/download")
def photo_download(id: int, db=Depends(get_db), u=Depends(access)):
    def read():
        p = field.get(db, Photo, id)
        s.work_access(db, u, field.get(db, AppWork, p.app_work_id))
        a = s.get(db, p.application_id)
        raw = s.photo_bytes(a, p)
        s.audit(db, u, "photo_download", a.id)
        db.commit()
        return Response(
            raw,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="survey-photo-{p.id}.{p.filename.rsplit(".",1)[-1]}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    return run(db, u, read)


@router.post('/{id}/quote-access')
def quote_access(id:int,b:QuoteAccessInput,db=Depends(get_db),u=Depends(access)):
    from .acquisition import prepare_quote_access
    return change(db,u,b,'quote_access_'+str(id),lambda:prepare_quote_access(db,u,s.get(db,id),b),True)


@router.get('/{id}/quote-access/{quote_id}')
def quote_access_token(id:int,quote_id:int,response:Response,db=Depends(get_db),u=Depends(access)):
    response.headers["Cache-Control"]="no-store"
    response.headers["Referrer-Policy"]="no-referrer"
    from .acquisition import quote_token
    return run(db,u,lambda:quote_token(db,u,id,quote_id),True)


@router.post('/{id}/sms-plan/{draft_id}')
def prospect_sms(id:int,draft_id:int,b:Mutation,db=Depends(get_db),u=Depends(access)):
    from .communications import prepare
    def write():
        draft=field.get(db,NotificationDraft,draft_id)
        if draft.application_id!=id:raise ValueError('Scoped application draft required')
        return prepare(db,u,draft_id)
    return change(db,u,b,'sms_plan_'+str(id),write)


@router.post('/{id}/slack-draft')
def signup_slack(id:int,b:Mutation,db=Depends(get_db),u=Depends(access)):
    def write():
        s.get(db,id)
        from app.notifications.slack_service import prepare
        r=prepare(db,'signup',str(id),u.id)
        s.audit(db,u,'slack_draft_prepared',id)
        return {'id':r.id,'fingerprint':r.fingerprint,'status':r.status,'external_delivery':False}
    return change(db,u,b,'slack_draft_'+str(id),write)


@router.get('/{id}/provisioning-preview')
def provisioning_preview(id:int,db=Depends(get_db),u=Depends(access)):
    from .acquisition import provision_preview
    return run(db,u,lambda:provision_preview(db,u,s.get(db,id)),True)


@router.post('/{id}/installation-asset')
def installation_asset(id:int,b:InstallAssetInput,db=Depends(get_db),u=Depends(access)):
    from .acquisition import install_asset
    def authorized():
        m=db.scalar(select(AppWork).where(AppWork.application_id==id,AppWork.work_order_id==b.work_order_id))
        if not m:raise PermissionError('Assigned installation context required')
        s.work_access(db,u,m)
    return change(db,u,b,'installation_asset_'+str(id),lambda:install_asset(db,u,s.get(db,id),b),authorize=authorized)
