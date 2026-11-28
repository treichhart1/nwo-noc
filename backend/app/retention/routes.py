"""Private administrator cancellation, financial and competitive-intelligence review."""

from datetime import date
from fastapi import APIRouter, Depends, Query, Response, HTTPException
from sqlalchemy import select, func, or_
from app.core.database import get_db
from app.finance.routes import financial_access
from app.customers.routes import transaction
from app.customers.models import CustomerService
from . import service as s, analytics
from .models import Case, Reason, Policy, Competitor, Observation, CaseService
from .contracts import (
    PolicyInput,
    PurposeConsent,
    ReasonInput,
    CompetitorInput,
    ObservationInput,
    Verification,
    StatusChange,
    RetentionChange,
    ReturnInput,
    OfferInput,
    OfferDecision,
    CohortInput,
    CSVInput,
    Communication,
    Change,
    Review,
)


def run(db, user, fn):
    try:
        return transaction(db, fn)
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except HTTPException:
        db.rollback()
        raise


router = APIRouter(
    prefix="/api/retention",
    tags=["Private cancellation and retention"],
    dependencies=[Depends(financial_access)],
)


@router.get("")
def dashboard(
    response: Response,
    offset: int = Query(0, ge=0, le=100000),
    status: str | None = Query(None, max_length=40),
    reason: str | None = Query(None, max_length=50),
    customer_id: int | None = Query(None, ge=1),
    service_id: int | None = Query(None, ge=1),
    q: str = Query("", max_length=45),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    response.headers["Cache-Control"] = "no-store"

    def load():
        filters = []
        if status:
            filters.append(Case.status == status)
        if reason:
            filters.append(
                or_(Case.reported_reason == reason, Case.operator_reason == reason)
            )
        if customer_id:
            filters.append(Case.customer_id == customer_id)
        if service_id:
            filters.append(
                Case.id.in_(
                    select(CaseService.case_id).where(
                        CaseService.service_id == service_id
                    )
                )
            )
        if q:
            filters.append(
                Case.reference.ilike("%" + q.replace("%", "").replace("_", "") + "%")
            )
        rows = list(
            db.scalars(
                select(Case)
                .where(*filters)
                .order_by(Case.created_at.desc(), Case.id.desc())
                .offset(offset)
                .limit(50)
            )
        )
        s.audit(db, u, "dashboard_viewed")
        return {
            "items": [s.case_view(db, c) for c in rows],
            "total": db.scalar(select(func.count()).select_from(Case).where(*filters)),
            "offset": offset,
            "reasons": [
                {
                    "code": r.code,
                    "label": r.label,
                    "voluntary": r.voluntary,
                    "active": r.active,
                }
                for r in db.scalars(select(Reason).order_by(Reason.code))
            ],
            "policies": [
                {
                    "id": p.id,
                    "version": p.version,
                    "text": p.text,
                    "active": p.active,
                    "retention_days": p.retention_days,
                    "unresolved_days": p.unresolved_days,
                    "tower_churn_threshold": p.tower_churn_threshold,
                }
                for p in db.scalars(
                    select(Policy).order_by(Policy.version.desc()).limit(20)
                )
            ],
            "competitors": [
                {
                    "id": r.id,
                    "name": r.name,
                    "technology": r.technology,
                    "service_areas": r.service_areas,
                    "active": r.active,
                }
                for r in db.scalars(
                    select(Competitor).order_by(Competitor.name).limit(500)
                )
            ],
            "external_changes_enabled": False,
        }

    return run(db, u, load)


@router.get("/analytics")
def report(
    month: str = Query(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$"),
    site_id: int | None = Query(None, ge=1),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    return run(db, u, lambda: audited_summary(db, u, month, site_id))


@router.get("/towers/{site_id}")
def tower(
    site_id: int,
    month: str = Query(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$"),
    finance_period_id: int | None = Query(None, ge=1),
    finance_scope_id: int | None = Query(None, ge=1),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    def load():
        from app.finance.models import Scope, Period
        from app.finance.service import calculate

        result = analytics.summary(db, month, site_id)
        result["profitability"] = None
        if finance_period_id is not None or finance_scope_id is not None:
            scope = db.get(Scope, finance_scope_id)
            period = db.get(Period, finance_period_id)
            start = date.fromisoformat(month + "-01")
            if (
                not scope
                or scope.kind != "tower"
                or scope.site_id != site_id
                or not period
                or period.starts_on != start
                or period.ends_on != analytics.next_month(start)
            ):
                raise ValueError(
                    "Matching canonical tower finance scope and reporting month required"
                )
            result["profitability"] = calculate(db, period.id, scope.id)
        result["active_services"] = db.scalar(
            select(func.count())
            .select_from(CustomerService)
            .where(
                CustomerService.site_id == site_id,
                CustomerService.status == "active",
                CustomerService.source_deleted_at.is_(None),
            )
        )
        result["active_customer_count"] = db.scalar(
            select(func.count(func.distinct(CustomerService.customer_id))).where(
                CustomerService.site_id == site_id,
                CustomerService.status == "active",
                CustomerService.source_deleted_at.is_(None),
            )
        )
        return result

    return run(db, u, load)


@router.get("/cases/{id}")
def detail(id: int, db=Depends(get_db), u=Depends(financial_access)):
    def load():
        c = db.get(Case, id)
        if not c:
            raise HTTPException(404, "Request not found")
        s.audit(db, u, "request_viewed", id)
        if c.redacted_at:
            return s.case_view(db, c)
        return {**s.case_view(db, c, True), "risk": s.risk(db, c)}

    return run(db, u, load)


@router.post("/policies")
def policy(b: PolicyInput, db=Depends(get_db), u=Depends(financial_access)):
    return run(db, u, lambda: s.configure(db, u, b))


@router.post("/reasons")
def reason(b: ReasonInput, db=Depends(get_db), u=Depends(financial_access)):
    def write():
        s.lock(db)
        r = db.get(Reason, b.code)
        if r:
            # Classification changes are versioned as audit evidence, never rewrite case reports.
            s.audit(
                db,
                u,
                "reason_configuration",
                details={
                    "code": r.code,
                    "previous_classification": r.voluntary,
                    "classification": b.voluntary,
                },
            )
            for k, v in b.model_dump().items():
                setattr(r, k, v)
        else:
            db.add(Reason(**b.model_dump()))
            s.audit(db, u, "reason_created")
        return {"code": b.code}

    return run(db, u, write)


@router.post("/competitors")
def competitor(b: CompetitorInput, db=Depends(get_db), u=Depends(financial_access)):
    def write():
        s.lock(db)
        r = db.scalar(
            select(Competitor).where(func.lower(Competitor.name) == b.name.lower())
        )
        if r:
            for k, v in b.model_dump().items():
                setattr(r, k, v)
        else:
            r = Competitor(**b.model_dump())
            db.add(r)
        s.audit(db, u, "competitor_reviewed")
        db.flush()
        return {"id": r.id}

    return run(db, u, write)


@router.get("/observations")
def observations(
    competitor_id: int = Query(ge=1),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    def load():
        s.audit(db, u, "competitor_observations_viewed")
        return [
            {
                "id": r.id,
                "competitor_id": r.competitor_id,
                "offer": r.offer,
                "source_kind": r.source_kind,
                "source_reference": r.source_reference,
                "observed_on": r.observed_on,
                "verified": r.verified,
            }
            for r in db.scalars(
                select(Observation)
                .where(Observation.competitor_id == competitor_id)
                .order_by(Observation.observed_on.desc(), Observation.id.desc())
                .offset(offset)
                .limit(100)
            )
        ]

    return run(db, u, load)


@router.post("/observations")
def observation(b: ObservationInput, db=Depends(get_db), u=Depends(financial_access)):
    def write():
        s.lock(db)
        if not db.get(Competitor, b.competitor_id):
            raise ValueError("Known competitor required")
        r = Observation(
            **b.model_dump(exclude={"offer"}),
            offer=b.offer.model_dump(mode="json"),
            verified_by=u.id if b.verified else None,
        )
        db.add(r)
        s.audit(db, u, "competitor_observation_created")
        db.flush()
        return {"id": r.id}

    return run(db, u, write)


@router.post("/cohorts")
def cohort(b: CohortInput, db=Depends(get_db), u=Depends(financial_access)):
    return run(db, u, lambda: s.cohort(db, u, b))


@router.post("/historical-import")
def historical(b: CSVInput, db=Depends(get_db), u=Depends(financial_access)):
    return run(db, u, lambda: s.historical(db, u, b))


@router.post("/cases/{id}/verify")
def verify(id: int, b: Verification, db=Depends(get_db), u=Depends(financial_access)):
    return run(db, u, lambda: s.verify(db, u, id, b))


@router.post("/cases/{id}/status")
def status(id: int, b: StatusChange, db=Depends(get_db), u=Depends(financial_access)):
    return run(db, u, lambda: s.change(db, u, id, b))


@router.post("/cases/{id}/retention")
def retention(
    id: int, b: RetentionChange, db=Depends(get_db), u=Depends(financial_access)
):
    return run(db, u, lambda: s.retention(db, u, id, b))


@router.post("/cases/{id}/equipment")
def equipment(id: int, b: ReturnInput, db=Depends(get_db), u=Depends(financial_access)):
    return run(db, u, lambda: s.returns(db, u, id, b))


@router.post("/cases/{id}/offers")
def offer(id: int, b: OfferInput, db=Depends(get_db), u=Depends(financial_access)):
    return run(db, u, lambda: s.offer(db, u, id, b))


@router.post("/cases/{id}/offers/{offer_id}/decision")
def decision(
    id: int,
    offer_id: int,
    b: OfferDecision,
    db=Depends(get_db),
    u=Depends(financial_access),
):
    return run(db, u, lambda: s.offer_decide(db, u, id, offer_id, b))


@router.post("/cases/{id}/redact")
def redact(id: int, b: Change, db=Depends(get_db), u=Depends(financial_access)):
    return run(db, u, lambda: s.redact(db, u, id, b))


@router.post("/cases/{id}/sms-draft")
def communication(
    id: int, b: Communication, db=Depends(get_db), u=Depends(financial_access)
):
    from .communications import prepare

    return run(db, u, lambda: prepare(db, u, id, b))


@router.post("/cases/{id}/review")
def review(id: int, b: Review, db=Depends(get_db), u=Depends(financial_access)):
    return run(db, u, lambda: s.review(db, u, id, b))


@router.get("/trends")
def trends(
    start: str = Query(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$"),
    months: int = Query(6, ge=1, le=12),
    site_id: int | None = Query(None, ge=1),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    def load():
        d = date.fromisoformat(start + "-01")
        reports = []
        for _ in range(months):
            reports.append(analytics.summary(db, d.strftime("%Y-%m"), site_id))
            d = analytics.next_month(d)
        return reports

    return run(db, u, load)


def audited_summary(db, u, month, site_id):
    s.audit(db, u, "financial_report_viewed")
    return analytics.summary(db, month, site_id)


@router.get("/geography")
def geography(
    month: str = Query(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$"),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    def load():
        from app.models.entities import Site

        s.audit(db, u, "private_tower_geography_viewed")
        report = analytics.summary(db, month)
        groups = report["breakdowns"]["tower"]
        return {
            "month": month,
            "private": True,
            "points": [
                {
                    "id": r.id,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                    "observed_service_cancellations": groups.get(str(r.id), 0),
                }
                for r in db.scalars(select(Site).order_by(Site.id).limit(1000))
                if r.latitude is not None and r.longitude is not None
            ],
            "coordinate_source": "Existing operator-maintained site inventory; never published by the public gateway",
            "missing_coordinates": "Omitted sites remain unavailable, not invented",
        }

    return run(db, u, load)


@router.get("/cases/{id}/profitability")
def retention_profitability(
    id: int,
    period_id: int = Query(ge=1),
    scope_id: int = Query(ge=1),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    def load():
        from app.finance.models import Scope, GroupMember
        from app.finance.service import calculate

        case = s.get(db, id)
        selected = set(
            db.scalars(
                select(CaseService.service_id).where(CaseService.case_id == case.id)
            )
        )
        scope = db.get(Scope, scope_id)
        covered = (
            {scope.service_id}
            if scope and scope.kind == "service"
            else set(
                db.scalars(
                    select(GroupMember.service_id).where(
                        GroupMember.scope_id == scope_id
                    )
                )
            )
            if scope and scope.kind == "group"
            else set()
        )
        if not selected or covered != selected:
            raise ValueError(
                "Canonical finance service/group scope must match exactly the selected services"
            )
        finance = calculate(db, period_id, scope_id)
        s.audit(db, u, "retention_profitability_viewed", case.id)
        return {
            "case_id": case.id,
            "financial_scope": finance,
            "estimated_retention_profitability": finance["contribution"]
            if finance["complete"]
            else None,
            "currency": finance["period"]["currency"],
            "basis": "Canonical contracted revenue and documented operating expenses; not collected cash",
            "offer_cost_caution": "Offer estimates are not automatically posted; finance completeness must explicitly cover actual offer/labor/shared costs. Do not subtract costs already allocated in this report twice.",
            "automatic_financial_changes": False,
        }

    return run(db, u, load)


@router.post("/cases/{id}/sms-consent")
def purpose_consent(
    id: int, b: PurposeConsent, db=Depends(get_db), u=Depends(financial_access)
):
    return run(db, u, lambda: s.purpose_consent(db, u, id, b))
