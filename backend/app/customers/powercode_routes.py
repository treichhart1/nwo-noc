"""Private Powercode administration; every mutation is administrator-only."""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select, func
from app.api.deps import require_admin
from app.core.database import get_db
from app.customers.routes import customer_operator, transaction
from app.customers.models import (
    CustomerAccount,
    CustomerService,
    CustomerSyncRun,
    CustomerSyncState,
)
from app.customers.powercode_models import (
    PowercodePackage,
    PowercodePackageMapping,
    PowercodeProvision,
    PowercodeAttempt,
    PowercodeProvisionEvent,
    PowercodeServiceHistory,
)
from app.customers.powercode_contracts import (
    ConnectionInput,
    CredentialInput,
    MappingInput,
    ProvisionInput,
    DecisionInput,
    ActivationInput,
    STATES,
)
from app.customers import powercode_service as s
from app.equipment.service import view

router = APIRouter(
    prefix="/api/customers/powercode",
    tags=["Private Powercode integration"],
    dependencies=[Depends(customer_operator)],
)


def run(db, user, fn):
    def call():
        try:
            return fn()
        except (ValueError, PermissionError) as exc:
            from fastapi import HTTPException

            raise HTTPException(
                403 if isinstance(exc, PermissionError) else 409, str(exc)
            ) from None

    return transaction(db, call)


@router.get("")
def dashboard(
    response: Response,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(customer_operator),
):
    response.headers["Cache-Control"] = "no-store"

    def load():
        s.audit(db, u, "dashboard_viewed")
        packages = list(
            db.scalars(
                select(PowercodePackage)
                .order_by(PowercodePackage.external_id)
                .offset(offset)
                .limit(100)
            )
        )
        duplicate_contacts = (
            select(CustomerAccount.phone)
            .where(
                CustomerAccount.phone.is_not(None),
                CustomerAccount.source_deleted_at.is_(None),
            )
            .group_by(CustomerAccount.phone)
            .having(func.count() > 1)
        )
        groups = db.execute(
            select(
                CustomerService.status,
                CustomerService.service_type,
                CustomerService.currency,
                CustomerService.revenue_basis,
                func.count(),
                func.sum(CustomerService.monthly_recurring),
            )
            .where(CustomerService.source_deleted_at.is_(None))
            .group_by(
                CustomerService.status,
                CustomerService.service_type,
                CustomerService.currency,
                CustomerService.revenue_basis,
            )
        ).all()
        return {
            "configuration": s.config_view(db),
            "states": STATES,
            "offset": offset,
            "packages": [
                dict(s.package_view(p), digest=s.package_digest(p)) for p in packages
            ],
            "package_count": db.scalar(
                select(func.count()).select_from(PowercodePackage)
            ),
            "mappings": [
                view(m)
                for m in db.scalars(
                    select(PowercodePackageMapping)
                    .order_by(PowercodePackageMapping.plan_id)
                    .offset(offset)
                    .limit(100)
                )
            ],
            "provisions": [
                s.provision_view(p)
                for p in db.scalars(
                    select(PowercodeProvision)
                    .order_by(PowercodeProvision.id.desc())
                    .offset(offset)
                    .limit(100)
                )
            ],
            "provision_count": db.scalar(
                select(func.count()).select_from(PowercodeProvision)
            ),
            "synchronizations": [
                view(r)
                for r in db.scalars(
                    select(CustomerSyncRun)
                    .order_by(CustomerSyncRun.id.desc())
                    .offset(offset)
                    .limit(100)
                )
            ],
            "sync_state": view(db.get(CustomerSyncState, "powercode"))
            if db.get(CustomerSyncState, "powercode")
            else None,
            "customer_count": db.scalar(
                select(func.count()).select_from(CustomerAccount)
            ),
            "service_count": db.scalar(
                select(func.count()).select_from(CustomerService)
            ),
            "unmapped_services": db.scalar(
                select(func.count())
                .select_from(CustomerService)
                .where(
                    CustomerService.mapping_state != "verified",
                    CustomerService.source_deleted_at.is_(None),
                )
            ),
            "duplicate_contact_groups": db.scalar(
                select(func.count()).select_from(duplicate_contacts.subquery())
            ),
            "revenue_observations": [
                {
                    "status": r[0],
                    "technology": r[1],
                    "currency": r[2],
                    "basis": r[3],
                    "services": r[4],
                    "monthly_recurring": r[5] if r[0] == "active" else None,
                }
                for r in groups
            ],
            "financial_scope": "Source-reported contracted/estimated recurring charges only; not invoiced or collected revenue",
            "native_enabled": False,
            "provisioning_worker_registered": False,
        }

    return run(db, u, load)


@router.post("/configuration")
def configure(b: ConnectionInput, db=Depends(get_db), u=Depends(require_admin)):
    return run(db, u, lambda: s.configure(db, u, b))


@router.post("/credential")
def credential(b: CredentialInput, db=Depends(get_db), u=Depends(require_admin)):
    return run(db, u, lambda: s.credential(db, u, b))


@router.post("/package-mappings")
def mapping(b: MappingInput, db=Depends(get_db), u=Depends(require_admin)):
    return run(db, u, lambda: s.map_package(db, u, b))


@router.post("/provisions")
def prepare(
    b: ProvisionInput, response: Response, db=Depends(get_db), u=Depends(require_admin)
):
    response.headers["Cache-Control"] = "no-store"
    return run(db, u, lambda: s.prepare(db, u, b))


@router.get("/provisions/{ident}")
def preview(
    ident: int, response: Response, db=Depends(get_db), u=Depends(require_admin)
):
    response.headers["Cache-Control"] = "no-store"
    return run(db, u, lambda: s.preview(db, u, ident))


@router.post("/provisions/{ident}/decision/{action}")
def decision(
    ident: int,
    action: str,
    b: DecisionInput,
    db=Depends(get_db),
    u=Depends(require_admin),
):
    if action not in ("approve", "queue", "cancel"):
        from fastapi import HTTPException

        raise HTTPException(404, "Unsupported operation")
    return run(db, u, lambda: s.decide(db, u, ident, b, action))


@router.post("/provisions/{ident}/activation-authorization")
def activation(
    ident: int, b: ActivationInput, db=Depends(get_db), u=Depends(require_admin)
):
    return run(db, u, lambda: s.decide(db, u, ident, b, "approve_activation"))


@router.get("/provisions/{ident}/history")
def history(
    ident: int,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(require_admin),
):
    def load():
        if not db.get(PowercodeProvision, ident):
            raise LookupError()
        s.audit(db, u, "provision_history_viewed", ident)
        return {
            "events": [
                view(r)
                for r in db.scalars(
                    select(PowercodeProvisionEvent)
                    .where(PowercodeProvisionEvent.provision_id == ident)
                    .order_by(PowercodeProvisionEvent.id)
                    .offset(offset)
                    .limit(100)
                )
            ],
            "attempts": [
                view(r)
                for r in db.scalars(
                    select(PowercodeAttempt)
                    .where(PowercodeAttempt.provision_id == ident)
                    .order_by(PowercodeAttempt.id)
                    .offset(offset)
                    .limit(100)
                )
            ],
        }

    return run(db, u, load)


@router.get("/services/{ident}/status-history")
def service_history(
    ident: int,
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    u=Depends(customer_operator),
):
    def load():
        if not db.get(CustomerService, ident):
            raise LookupError()
        s.audit(db, u, "service_history_viewed", ident)
        return [
            view(r)
            for r in db.scalars(
                select(PowercodeServiceHistory)
                .where(PowercodeServiceHistory.service_id == ident)
                .order_by(PowercodeServiceHistory.id)
                .offset(offset)
                .limit(100)
            )
        ]

    return run(db, u, load)
