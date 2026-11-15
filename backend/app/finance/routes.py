from fastapi import APIRouter, Depends, Query, HTTPException, Response
from sqlalchemy import select, func
from app.core.database import get_db
from app.field.routes import access, run
from app.field import service as field
from . import service as s
from .models import *
from .contracts import *


def financial_access(u=Depends(access), db=Depends(get_db)):
    if u.role != "admin":
        s.audit(db, u, "access_denied", "private", {"financial_values": False})
        db.commit()
        raise HTTPException(403, "Financial administrator permission required")
    return u


router = APIRouter(
    prefix="/api/finance/v1",
    tags=["Private operational finance"],
    dependencies=[Depends(financial_access)],
)


def read(db, u, fn):
    return run(db, u, lambda: (s.admin(u), fn())[1])


def change(db, u, b, action, fn):
    return run(
        db,
        u,
        lambda: (s.admin(u), field.mutation(db, u, "finance:" + action, b, fn))[1],
        True,
    )


@router.get("/me")
def me(u=Depends(financial_access)):
    return {
        "role": u.role,
        "financial_access": True,
        "powercode_transport": "mock_only",
        "billing_writes": False,
        "fiber_construction_finance": False,
    }


@router.get("/periods")
def periods(
    offset: int = Query(0, ge=0, le=10000),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    return read(
        db,
        u,
        lambda: {
            "items": [
                s.view(x)
                for x in db.scalars(
                    select(Period)
                    .order_by(Period.starts_on.desc())
                    .offset(offset)
                    .limit(50)
                )
            ]
        },
    )


@router.post("/periods")
def period(b: PeriodInput, db=Depends(get_db), u=Depends(financial_access)):
    return change(db, u, b, "period", lambda: s.create_period(db, u, b))


@router.post("/periods/{id}/close")
def close(id: int, b: Mutation, db=Depends(get_db), u=Depends(financial_access)):
    def call():
        p = s.open_period(db, id)
        p.closed = True
        db.flush()
        return s.view(p)

    return change(db, u, b, f"close:{id}", call)


@router.get("/scopes")
def scopes(
    kind: str | None = Query(None, max_length=30),
    offset: int = Query(0, ge=0, le=10000),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    def call():
        q = select(Scope)
        if kind:
            q = q.where(Scope.kind == kind)
        return {
            "items": [
                s.view(x)
                for x in db.scalars(q.order_by(Scope.id).offset(offset).limit(50))
            ]
        }

    return read(db, u, call)


@router.post("/scopes")
def scope(b: ScopeInput, db=Depends(get_db), u=Depends(financial_access)):
    return change(db, u, b, "scope", lambda: s.create_scope(db, u, b))


@router.post("/periods/{id}/entries")
def entry(id: int, b: EntryInput, db=Depends(get_db), u=Depends(financial_access)):
    return change(db, u, b, f"entry:{id}", lambda: s.add_entry(db, u, id, b))


@router.post("/entries/{id}/reverse")
def reverse(id: int, b: ReversalInput, db=Depends(get_db), u=Depends(financial_access)):
    return change(db, u, b, f"reverse:{id}", lambda: s.reverse(db, u, id, b))


@router.post("/entries/{id}/allocation")
def allocation(
    id: int, b: AllocationInput, db=Depends(get_db), u=Depends(financial_access)
):
    return change(db, u, b, f"allocate:{id}", lambda: s.allocate(db, u, id, b))


@router.post("/periods/{id}/coverage")
def coverage(
    id: int, b: CoverageInput, db=Depends(get_db), u=Depends(financial_access)
):
    return change(db, u, b, f"coverage:{id}", lambda: s.attest(db, u, id, b))


@router.post("/recurring")
def recurring(b: RecurringInput, db=Depends(get_db), u=Depends(financial_access)):
    return change(db, u, b, "recurring", lambda: s.recurring_create(db, u, b))


@router.post("/periods/{id}/recurring")
def materialize(id: int, b: Mutation, db=Depends(get_db), u=Depends(financial_access)):
    return change(db, u, b, f"materialize:{id}", lambda: s.materialize(db, u, id, b))


@router.get("/recurring")
def templates(
    offset: int = Query(0, ge=0, le=10000),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    return read(
        db,
        u,
        lambda: {
            "items": [
                s.view(x)
                for x in db.scalars(
                    select(Recurring).order_by(Recurring.id).offset(offset).limit(50)
                )
            ]
        },
    )


@router.post("/periods/{id}/imports/preview")
def preview(id: int, b: ImportInput, db=Depends(get_db), u=Depends(financial_access)):
    return change(db, u, b, f"preview:{id}", lambda: s.preview_import(db, u, id, b))


@router.post("/imports/{id}/approve")
def approve(id: int, b: ApprovalInput, db=Depends(get_db), u=Depends(financial_access)):
    return change(db, u, b, f"import:{id}", lambda: s.approve_import(db, u, id, b))


@router.get("/imports")
def imports(
    offset: int = Query(0, ge=0, le=10000),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    return read(
        db,
        u,
        lambda: {
            "items": [
                s.view(x)
                for x in db.scalars(
                    select(ImportBatch)
                    .order_by(ImportBatch.id.desc())
                    .offset(offset)
                    .limit(50)
                )
            ]
        },
    )


@router.get("/periods/{id}/report")
def report(
    id: int,
    scope_id: int,
    revenue_basis: RevenueBasis = "contracted",
    expense_basis: ExpenseBasis = "actual",
    service_type: str | None = Query(
        None, pattern="^(wireless|fiber|hybrid|transport)$"
    ),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    return read(
        db,
        u,
        lambda: s.calculate(
            db, id, scope_id, revenue_basis, expense_basis, service_type
        ),
    )


@router.post("/periods/{id}/reports")
def save_report(
    id: int, b: CoverageInput, db=Depends(get_db), u=Depends(financial_access)
):
    return change(
        db,
        u,
        b,
        f"report:{id}",
        lambda: s.snapshot(db, u, id, b.scope_id, b.revenue_basis, b.expense_basis),
    )


@router.get("/reports/{id}")
def historical_report(id: int, db=Depends(get_db), u=Depends(financial_access)):
    return read(db, u, lambda: s.view(s.get(db, Report, id)))


@router.get("/periods/{id}/export")
def export(
    id: int,
    scope_id: int,
    revenue_basis: RevenueBasis = "contracted",
    expense_basis: ExpenseBasis = "actual",
    db=Depends(get_db),
    u=Depends(financial_access),
):
    def call():
        r = s.calculate(db, id, scope_id, revenue_basis, expense_basis)
        s.audit(db, u, "export", id, {"scope_id": scope_id})
        db.commit()
        return Response(
            s.csv_report(r),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="network-finance.csv"',
                "Cache-Control": "no-store",
            },
        )

    return read(db, u, call)


@router.get("/periods/{id}/dashboard")
def dashboard(
    id: int,
    kind: str = Query(
        "tower",
        pattern="^(tower|sector|backhaul|edge|circuit|plan|group|asset|network)$",
    ),
    revenue_basis: RevenueBasis = "contracted",
    expense_basis: ExpenseBasis = "actual",
    offset: int = Query(0, ge=0, le=10000),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    def call():
        scopes = list(
            db.scalars(
                select(Scope)
                .where(Scope.kind == kind)
                .order_by(Scope.id)
                .offset(offset)
                .limit(40)
            )
        )
        dataset = s.financial_data(db, s.get(db, Period, id))
        rows = [
            s.calculate(db, id, sc.id, revenue_basis, expense_basis, _dataset=dataset)
            for sc in scopes
        ]
        rows.sort(
            key=lambda r: (r["contribution"] is None, -s.D(r["contribution"] or 0))
        )
        return {
            "items": rows,
            "next_offset": offset + 40 if len(rows) == 40 else None,
            "ranking": "complete contribution first; incomplete rows remain unranked",
            "period_trends": [
                {
                    "period": s.view(p),
                    "scope_id": sc.id,
                    "revenue": s.calculate(
                        db, p.id, sc.id, revenue_basis, expense_basis
                    )["totals"]["revenue"],
                }
                for p in db.scalars(
                    select(Period)
                    .where(Period.currency == s.get(db, Period, id).currency)
                    .order_by(Period.starts_on.desc())
                    .limit(12)
                )
                for sc in scopes
                if sc.kind == "network"
            ],
        }

    return read(db, u, call)


@router.post("/periods/{id}/scenarios")
def scenario(
    id: int, b: ScenarioInput, db=Depends(get_db), u=Depends(financial_access)
):
    return change(db, u, b, f"scenario:{id}", lambda: s.scenario(db, u, id, b))


@router.get("/scenarios")
def scenarios(
    offset: int = Query(0, ge=0, le=10000),
    db=Depends(get_db),
    u=Depends(financial_access),
):
    return read(
        db,
        u,
        lambda: {
            "items": [
                s.view(x)
                for x in db.scalars(
                    select(Scenario)
                    .order_by(Scenario.id.desc())
                    .offset(offset)
                    .limit(50)
                )
            ]
        },
    )


@router.post("/periods/{id}/retirement-review")
def retirement(
    id: int, b: RetirementInput, db=Depends(get_db), u=Depends(financial_access)
):
    return change(db, u, b, f"retirement:{id}", lambda: s.retirement(db, u, id, b))


@router.post("/periods/{id}/contracts")
def contract(
    id: int, b: ContractInput, db=Depends(get_db), u=Depends(financial_access)
):
    def call():
        s.open_period(db, id)
        scope = s.get(db, Scope, b.scope_id)
        if scope.kind != "circuit":
            raise ValueError("Existing business transport circuit required")
        a = Assumption(
            period_id=id,
            scope_id=scope.id,
            name=b.name,
            evidence=b.evidence,
            inputs={
                "kind": "business_transport_contract",
                **b.model_dump(mode="json", exclude={"operation_key", "reason"}),
            },
            created_by=u.id,
        )
        db.add(a)
        db.flush()
        return s.view(a)

    return change(db, u, b, f"contract:{id}", call)


@router.post("/periods/{id}/policy")
def policy(id: int, b: PolicyInput, db=Depends(get_db), u=Depends(financial_access)):
    def call():
        s.open_period(db, id)
        s.get(db, Scope, b.scope_id)
        a = Assumption(
            period_id=id,
            scope_id=b.scope_id,
            name="Operational financial screening policy",
            evidence=b.evidence,
            inputs={
                "kind": "screening_policy",
                **b.model_dump(mode="json", exclude={"operation_key", "reason"}),
            },
            created_by=u.id,
        )
        db.add(a)
        db.flush()
        return s.view(a)

    return change(db, u, b, f"policy:{id}", call)
