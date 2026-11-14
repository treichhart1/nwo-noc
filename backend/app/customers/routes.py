"""Operator-only customer APIs. No live source activation or notification send route."""

from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body
from pydantic import Field
from sqlalchemy import select, func, text, or_
from sqlalchemy.exc import SQLAlchemyError
from app.api.deps import current_user, require_admin
from app.core.config import settings
from app.core.database import get_db
from app.models.entities import Site, Device, Sector, NetworkLink, AuditLog, Incident
from app.customers.models import (
    CustomerAccount,
    CustomerService,
    CustomerSyncState,
    IncidentCustomer,
)
from app.customers.contracts import (
    SafeModel,
    ExportBatch,
    masked_phone,
    masked_email,
    e164,
)
from app.customers.providers import ProviderFailure
from app.customers.sync import synchronize
from app.customers.impact import (
    Target,
    target_for,
    analyze,
    recipients,
    persist_incident,
)


def customer_operator(user=Depends(current_user), db=Depends(get_db)):
    if user.role not in {"admin", "operator"}:
        try:
            db.add(
                AuditLog(
                    user_id=user.id,
                    action="customer.access_denied",
                    entity_type="customer_data",
                    entity_id="powercode",
                    details={"required_role": "operator"},
                )
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(503, "Customer access audit unavailable") from None
        raise HTTPException(403, "Operator access required")
    return user


router = APIRouter(
    prefix="/api/customers",
    tags=["Private customers"],
    dependencies=[Depends(customer_operator)],
)


def audit(db, user, action, **details):
    # Never store search strings, names, source IDs, addresses or contact values.
    db.add(
        AuditLog(
            user_id=user.id,
            action="customer." + action,
            entity_type="customer_data",
            entity_id="powercode",
            details=details,
        )
    )


def transaction(db, fn):
    try:
        if db.get_bind().dialect.name != "postgresql":
            raise ProviderFailure("unavailable")
        db.execute(text("SET LOCAL statement_timeout='5000ms'"))
        result = fn()
        db.commit()
        return result
    except LookupError:
        db.rollback()
        raise HTTPException(404, "Customer or network record not found") from None
    except ProviderFailure as error:
        db.rollback()
        raise HTTPException(409, error.code) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            503,
            "Customer storage unavailable; retry safely, previous synchronization is retained",
        ) from None


def base_query(q="", site_id=None, device_id=None, incident_id=None):
    statement = select(CustomerService, CustomerAccount).join(CustomerAccount)
    if q:
        # Literal substring matching, not caller-supplied SQL wildcard syntax.
        needle = (
            "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        statement = statement.outerjoin(
            Site, CustomerService.site_id == Site.id
        ).outerjoin(Device, CustomerService.device_id == Device.id)
        statement = statement.where(
            or_(
                CustomerAccount.name.ilike(needle, escape="\\"),
                CustomerAccount.external_id.ilike(needle, escape="\\"),
                CustomerService.external_id.ilike(needle, escape="\\"),
                Site.name.ilike(needle, escape="\\"),
                Device.name.ilike(needle, escape="\\"),
            )
        )
    if site_id:
        statement = statement.where(CustomerService.site_id == site_id)
    if device_id:
        statement = statement.where(CustomerService.device_id == device_id)
    if incident_id:
        statement = statement.join(
            IncidentCustomer, IncidentCustomer.service_id == CustomerService.id
        ).where(IncidentCustomer.incident_id == incident_id)
    return statement


def view(s, a):
    return {
        "id": s.id,
        "external_id": s.external_id,
        "customer_id": a.id,
        "account_id": a.external_id,
        "name": a.name,
        "status": s.status,
        "service_type": s.service_type,
        "address": s.address,
        "plan": s.plan,
        "download_mbps": s.download_mbps,
        "upload_mbps": s.upload_mbps,
        "equipment_id": s.equipment_id,
        "site_id": s.site_id,
        "device_id": s.device_id,
        "sector_id": s.sector_id,
        "fiber_link_id": s.fiber_link_id,
        "mapping_state": s.mapping_state,
        "source_refs": s.source_refs,
        "phone": masked_phone(a.phone),
        "email": masked_email(a.email),
        "valid_sms_phone": bool(e164(a.phone)),
        "sms_consent": a.sms_consent,
        "consent_recorded_at": a.consent_recorded_at,
        "opt_out_at": a.opt_out_at,
        "preference_updated_at": a.preference_updated_at,
        "sms_opt_out": a.sms_opt_out,
        "communication_preference": a.communication_preference,
        "last_synced_at": s.last_synced_at,
        "source_deleted": s.source_deleted_at is not None,
    }


class Search(SafeModel):
    q: str = Field("", max_length=100)
    site_id: int | None = Field(None, ge=1)
    device_id: int | None = Field(None, ge=1)
    incident_id: int | None = Field(None, ge=1)
    offset: int = Field(0, ge=0, le=100000)
    limit: int = Field(50, ge=1, le=100)


@router.get("")
@router.post("/search")
def dashboard(
    body: Search = Body(default=Search()),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    q, site_id, device_id, incident_id, offset, limit = (
        body.q,
        body.site_id,
        body.device_id,
        body.incident_id,
        body.offset,
        body.limit,
    )

    def load():
        statement = base_query(q, site_id, device_id, incident_id)
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = db.execute(
            statement.order_by(CustomerService.id).offset(offset).limit(limit)
        ).all()
        counts = dict(
            db.execute(
                select(
                    CustomerService.service_type,
                    func.count(func.distinct(CustomerService.customer_id)),
                )
                .where(CustomerService.source_deleted_at.is_(None))
                .group_by(CustomerService.service_type)
            ).all()
        )
        by_site = [
            {"site_id": ident, "count": n}
            for ident, n in db.execute(
                select(
                    CustomerService.site_id,
                    func.count(func.distinct(CustomerService.customer_id)),
                )
                .where(CustomerService.source_deleted_at.is_(None))
                .group_by(CustomerService.site_id)
            )
        ]
        by_ap = [
            {"device_id": ident, "count": n}
            for ident, n in db.execute(
                select(
                    CustomerService.device_id,
                    func.count(func.distinct(CustomerService.customer_id)),
                )
                .where(CustomerService.source_deleted_at.is_(None))
                .group_by(CustomerService.device_id)
            )
        ]
        active_ids = select(CustomerService.customer_id).where(
            CustomerService.status == "active",
            CustomerService.source_deleted_at.is_(None),
        )
        duplicate_numbers = (
            select(CustomerAccount.phone)
            .group_by(CustomerAccount.phone)
            .having(func.count() > 1)
        )
        sms_eligible = db.scalar(
            select(func.count(CustomerAccount.id)).where(
                CustomerAccount.id.in_(active_ids),
                CustomerAccount.source_deleted_at.is_(None),
                CustomerAccount.phone.op("~")(r"^[+][1-9][0-9]{7,14}$"),
                CustomerAccount.sms_consent == "granted",
                CustomerAccount.consent_record.is_not(None),
                CustomerAccount.sms_opt_out.is_(False),
                CustomerAccount.communication_preference.in_(["sms", "both"]),
                CustomerAccount.phone.not_in(
                    duplicate_numbers.where(CustomerAccount.phone.is_not(None))
                ),
            )
        )
        state = db.get(CustomerSyncState, "powercode")
        audit(
            db,
            user,
            "accessed",
            operation="dashboard",
            returned=len(rows),
            filtered=bool(q or site_id or device_id or incident_id),
        )
        return {
            "total_customers": db.scalar(
                select(func.count(CustomerAccount.id)).where(
                    CustomerAccount.source_deleted_at.is_(None)
                )
            ),
            "active_customers": db.scalar(
                select(func.count(func.distinct(CustomerService.customer_id))).where(
                    CustomerService.status == "active",
                    CustomerService.source_deleted_at.is_(None),
                )
            ),
            "missing_tower_services": db.scalar(
                select(func.count(CustomerService.id)).where(
                    CustomerService.source_deleted_at.is_(None),
                    or_(
                        CustomerService.site_id.is_(None),
                        CustomerService.mapping_state != "verified",
                    ),
                )
            ),
            "sms_eligible_accounts_before_impact": sms_eligible,
            "by_service_type": counts,
            "by_tower": by_site,
            "by_access_point": by_ap,
            "total": total,
            "offset": offset,
            "items": [view(s, a) for s, a in rows],
            "integration": {
                "provider": "powercode",
                "live_enabled": False,
                "read_only": True,
                "fixture_import_allowed": settings.powercode_fixture_enabled
                and settings.offline_mode
                and settings.app_env == "staging",
                "status": state.status if state else "not_configured",
                "last_attempt_at": state.last_attempt_at if state else None,
                "last_success_at": state.last_success_at if state else None,
                "error_code": state.last_error if state else None,
                "received": state.last_count if state else 0,
                "stale": bool(
                    state
                    and state.last_success_at
                    and (
                        datetime.now(timezone.utc) - state.last_success_at
                    ).total_seconds()
                    > 86400
                ),
            },
            "notification_delivery_enabled": False,
        }

    return transaction(db, load)


class Mapping(SafeModel):
    site_id: int | None = Field(None, ge=1)
    device_id: int | None = Field(None, ge=1)
    sector_id: int | None = Field(None, ge=1)
    fiber_link_id: int | None = Field(None, ge=1)
    note: str = Field(min_length=1, max_length=255)


@router.patch("/services/{ident}/mapping")
def map_service(
    body: Mapping,
    ident: int = Path(ge=1),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    def save():
        row = db.get(CustomerService, ident)
        if row is None:
            raise LookupError()
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(950001)")):
            raise ProviderFailure("overlap_skipped")
        if row.source_deleted_at:
            raise ProviderFailure("invalid_reference")
        site = db.get(Site, body.site_id) if body.site_id else None
        device = db.get(Device, body.device_id) if body.device_id else None
        sector = db.get(Sector, body.sector_id) if body.sector_id else None
        link = db.get(NetworkLink, body.fiber_link_id) if body.fiber_link_id else None
        if (
            (body.site_id and not site)
            or (body.device_id and (not device or device.wisp_site_id != body.site_id))
            or (
                body.sector_id
                and (
                    not sector
                    or sector.site_id != body.site_id
                    or body.device_id
                    and sector.device_id != body.device_id
                )
            )
            or (
                body.fiber_link_id
                and (
                    not link
                    or link.link_type != "fiber"
                    or body.site_id not in (link.a_site_id, link.z_site_id)
                )
            )
            or (row.service_type == "fiber" and (body.sector_id or body.device_id))
        ):
            raise ProviderFailure("invalid_reference")
        for key in ("site_id", "device_id", "sector_id", "fiber_link_id"):
            setattr(row, key, getattr(body, key))
        row.mapping_state = "verified" if site else "unknown"
        row.mapping_note = body.note
        audit(
            db,
            user,
            "mapping_updated",
            service_id=ident,
            site_id=body.site_id,
            device_id=body.device_id,
            sector_id=body.sector_id,
            fiber_link_id=body.fiber_link_id,
        )
        return {
            "id": ident,
            "mapping_state": row.mapping_state,
            "network_configuration_changed": False,
        }

    return transaction(db, save)


class Preference(SafeModel):
    consent: Literal["unknown", "granted", "denied"]
    opt_out: bool
    preference: Literal["unknown", "none", "sms", "email", "both"]
    consent_record: str | None = Field(None, pattern=r"^[A-Za-z0-9_.:-]{1,120}$")


@router.patch("/accounts/{ident}/preferences")
def preference(
    body: Preference,
    ident: int = Path(ge=1),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    def save():
        a = db.get(CustomerAccount, ident)
        if a is None:
            raise LookupError()
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(950001)")):
            raise ProviderFailure("overlap_skipped")
        if a.source_deleted_at or body.consent == "granted" and not body.consent_record:
            raise ProviderFailure("invalid_reference")
        a.consent_recorded_at = (
            datetime.now(timezone.utc) if body.consent == "granted" else None
        )
        a.preference_updated_at = datetime.now(timezone.utc)
        if body.opt_out:
            a.opt_out_at = datetime.now(timezone.utc)
        a.sms_consent = body.consent
        a.sms_opt_out = body.opt_out
        a.communication_preference = body.preference
        a.consent_record = body.consent_record
        audit(
            db,
            user,
            "preferences_updated",
            customer_id=ident,
            consent=body.consent,
            opt_out=body.opt_out,
        )
        return {"id": ident, "delivery_enabled": False}

    return transaction(db, save)


class Preview(SafeModel):
    site_id: int | None = Field(None, ge=1)
    device_id: int | None = Field(None, ge=1)
    sector_id: int | None = Field(None, ge=1)
    link_id: int | None = Field(None, ge=1)
    incident_id: int | None = Field(None, ge=1)
    maintenance_id: int | None = Field(None, ge=1)
    offset: int = Field(0, ge=0, le=100000)
    limit: int = Field(100, ge=1, le=500)


@router.post("/impact-preview")
def preview(body: Preview, db=Depends(get_db), user=Depends(customer_operator)):
    def load():
        targets = [
            body.site_id,
            body.device_id,
            body.sector_id,
            body.link_id,
            body.incident_id,
            body.maintenance_id,
        ]
        if sum(x is not None for x in targets) != 1:
            raise ProviderFailure("invalid_reference")
        target = target_for(
            db,
            incident_id=body.incident_id,
            maintenance_id=body.maintenance_id,
            target=Target(body.site_id, body.device_id, body.sector_id, body.link_id),
        )
        services = list(
            db.scalars(
                select(CustomerService)
                .order_by(CustomerService.id)
                .offset(body.offset)
                .limit(body.limit)
            )
        )
        impacts = analyze(db, target, services)
        audit(
            db,
            user,
            "impact_previewed",
            returned=len(impacts),
            incident_id=body.incident_id,
            maintenance_id=body.maintenance_id,
        )
        return {
            "items": impacts,
            "recipient_preview": recipients(db, impacts),
            "total_services": db.scalar(select(func.count(CustomerService.id))),
            "offset": body.offset,
            "limit": body.limit,
            "preview_only": True,
            "incident_id": body.incident_id,
            "maintenance_id": body.maintenance_id,
            "delivery_enabled": False,
        }

    return transaction(db, load)


@router.post("/incidents/{ident}/assess")
def assess(
    ident: int = Path(ge=1), db=Depends(get_db), user=Depends(customer_operator)
):
    def save():
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(950002)")):
            raise ProviderFailure("overlap_skipped")
        target = target_for(db, incident_id=ident)
        services = list(
            db.scalars(
                select(CustomerService).order_by(CustomerService.id).limit(10001)
            )
        )
        if len(services) > 10000:
            raise ProviderFailure("unavailable")
        result = analyze(db, target, services)
        persist_incident(db, ident, result)
        audit(db, user, "incident_assessed", incident_id=ident, services=len(result))
        return {
            "incident_id": ident,
            "services": len(result),
            "delivery_enabled": False,
        }

    return transaction(db, save)


@router.post("/fixture-sync")
def fixture_sync(body: ExportBatch, db=Depends(get_db), user=Depends(require_admin)):
    if not (
        settings.powercode_fixture_enabled
        and settings.offline_mode
        and settings.app_env == "staging"
    ):
        raise HTTPException(
            409,
            "Synthetic fixture import disabled; no live Powercode connection is available",
        )

    class Provider:
        def fetch(self, **kwargs):
            return body

    return transaction(db, lambda: synchronize(db, Provider(), user_id=user.id))
