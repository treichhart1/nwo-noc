"""Bounded operator acquisition extensions; canonical applications and work orders."""

import secrets
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select, func
from app.field import service as field
from app.field.models import WorkOrder
from app.signup import service as s, crypto
from app.signup.models import Application, Quote, Plan, AppWork, QuoteResponse, Event
from app.equipment.service import view
from app.field.models import now


def prepare_quote_access(db, u, a, b):
    field.admin(u)
    s.check(a, b.expected_revision)
    q = field.get(db, Quote, b.quote_id)
    latest = db.scalar(
        select(func.max(Quote.number)).where(Quote.application_id == a.id)
    )
    if (
        q.application_id != a.id
        or q.number != latest
        or q.status != "issued"
        or q.expires_at <= now()
        or a.state != "quote_sent"
    ):
        raise ValueError("Current issued quote required")
    token = secrets.token_urlsafe(48)
    q.access_hash = crypto.digest("quote_access", token)
    q.access_ciphertext = crypto.seal(a, {"token": token})
    s.event(
        db,
        a,
        u,
        "quote_access_prepared",
        b.reason,
        {"quote_id": q.id, "expires_at": q.expires_at.isoformat()},
    )
    return {"id": q.id, "token_prepared": True, "expires_at": q.expires_at.isoformat()}


def quote_token(db, u, application_id, quote_id):
    field.admin(u)
    a = s.get(db, application_id)
    q = field.get(db, Quote, quote_id)
    if (
        q.application_id != a.id
        or not q.access_ciphertext
        or a.redacted_at
        or q.expires_at <= now()
    ):
        raise ValueError("Quote access unavailable")
    s.audit(db, u, "quote_access_viewed", a.id)
    return {
        "token": crypto.unseal(a, q.access_ciphertext)["token"],
        "expires_at": q.expires_at.isoformat(),
        "transport": "Provide privately; never place token in URL, logs or Slack",
    }


def provision_preview(db, u, a):
    field.admin(u)
    from app.signup.models import ProvisionDraft

    approved = db.scalar(
        select(ProvisionDraft).where(ProvisionDraft.application_id == a.id)
    )
    if not approved:
        raise ValueError("Approved documentation-only provisioning draft required")
    p = s.pii(a)
    q = db.scalar(
        select(Quote)
        .where(Quote.application_id == a.id, Quote.status == "accepted")
        .order_by(Quote.number.desc())
        .limit(1)
    )
    return {
        "application_id": a.id,
        "approved_by": approved.approved_by,
        "applicant": {
            k: p.get(k)
            for k in [
                "customer_name",
                "business_name",
                "service_address",
                "billing_address",
                "email",
                "phone",
            ]
        },
        "service_plan": a.plan_snapshot,
        "powercode_plan_reference": field.get(db, Plan, a.plan_id).powercode_plan_ref,
        "matched_customer_id": a.matched_customer_id,
        "duplicate_review": s.matches(db, a),
        "quote": q.snapshot if q else None,
        "work_orders": [
            view(w)
            for w in db.scalars(select(AppWork).where(AppWork.application_id == a.id))
        ],
        "production_writes": 0,
        "activation_authorized": False,
        "next_action": "Separately approved NOC-130 provisioning; no Powercode writes exist here",
    }


def analytics(db, u, since, until):
    s.staff(u)
    rows = list(
        db.scalars(
            select(Application)
            .where(Application.created_at >= since, Application.created_at < until)
            .order_by(Application.id)
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise ValueError("Narrow analytics range; bounded to10000 applications")
    states = {}
    technologies = {}
    dates = {}
    towns = {}
    reasons = []
    processing = []
    leads = []
    mrr = {}
    ids = [a.id for a in rows]
    recent = list(
        db.scalars(
            select(Event)
            .where(Event.application_id.in_(ids), Event.action == "state_changed")
            .order_by(Event.id.desc())
            .limit(100)
        )
    )
    reasons_by_app = {}
    for item in recent:
        reasons_by_app.setdefault(item.application_id, item)
    completions = dict(
        db.execute(
            select(AppWork.application_id, WorkOrder.completed_at)
            .join(WorkOrder, WorkOrder.id == AppWork.work_order_id)
            .where(
                AppWork.application_id.in_(ids),
                AppWork.kind == "installation",
                WorkOrder.completed_at.is_not(None),
            )
        ).all()
    )
    for a in rows:
        states[a.state] = states.get(a.state, 0) + 1
        technologies[a.technology] = technologies.get(a.technology, 0) + 1
        day = a.created_at.date().isoformat()
        dates[day] = dates.get(day, 0) + 1
        if a.wrapped_key and not a.redacted_at:
            city = s.pii(a).get("service_address", {}).get("city", "Unknown")
            towns[city] = towns.get(city, 0) + 1
        if a.closed_at:
            processing.append((a.closed_at - a.created_at).total_seconds() / 86400)
        if (
            a.state not in {"rejected", "cancelled"}
            and a.plan_snapshot.get("monthly") is not None
        ):
            currency = a.plan_snapshot.get("currency")
            if currency:
                mrr[currency] = mrr.get(currency, Decimal(0)) + Decimal(
                    str(a.plan_snapshot["monthly"])
                )
        if a.state in {"rejected", "cancelled"}:
            e = reasons_by_app.get(a.id)
            if e and a.wrapped_key:
                reasons.append(
                    {
                        "application_id": a.id,
                        "state": a.state,
                        "reason": crypto.unseal(a, e.reason_ciphertext),
                    }
                )
        completed = completions.get(a.id)
        if completed:
            leads.append((completed - a.created_at).total_seconds() / 86400)
    installed = sum(
        states.get(k, 0) for k in ["installed", "activation_pending", "completed"]
    )
    qualified = sum(
        states.get(k, 0)
        for k in [
            "qualified",
            "quote_pending",
            "quote_sent",
            "customer_accepted",
            "installation_pending",
            "installation_scheduled",
            "installed",
            "activation_pending",
            "completed",
        ]
    )
    return {
        "total": len(rows),
        "by_state": states,
        "by_date": dates,
        "by_town": towns,
        "by_technology": technologies,
        "qualification_rate": qualified / len(rows) if rows else None,
        "installation_conversion_rate": installed / len(rows) if rows else None,
        "average_closed_processing_days": sum(processing) / len(processing)
        if processing
        else None,
        "average_completed_installation_lead_days": sum(leads) / len(leads)
        if leads
        else None,
        "closed_reasons": reasons[:100],
        "prospective_monthly_revenue": {k: str(v) for k, v in mrr.items()},
        "revenue_basis": "Configured plan snapshots of non-rejected/non-cancelled prospects; not billed, contracted or collected revenue",
        "rate_basis": "Current lifecycle state among applications submitted in the selected UTC period",
    }


def install_asset(db, u, a, b):
    s.check(a, b.expected_revision)
    m = db.scalar(
        select(AppWork).where(
            AppWork.application_id == a.id,
            AppWork.kind == "installation",
            AppWork.work_order_id == b.work_order_id,
        )
    )
    if not m:
        raise ValueError("Canonical installation work order required")
    w = s.work_access(db, u, m)
    if (
        u.role not in {"admin", "technician"}
        or w.status != "in_progress"
        or a.state != "installation_scheduled"
    ):
        raise PermissionError("Assigned in-progress installation required")
    from app.equipment.models import Asset, Location
    from app.equipment.contracts import AssetMove
    from app.equipment.service import move_asset

    asset = field.get(db, Asset, b.asset_id)
    location = field.get(db, Location, b.customer_location_id)
    if location.application_id != a.id:
        from app.customers.models import CustomerService

        service = (
            db.get(CustomerService, location.service_id)
            if location.service_id
            else None
        )
        if (
            not service
            or not a.matched_customer_id
            or service.customer_id != a.matched_customer_id
        ):
            raise ValueError(
                "Customer premises must reference this application or its administrator-matched customer"
            )
    if (
        location.kind != "customer_premises"
        or asset.state not in {"available", "reserved"}
        or w.asset_id
    ):
        raise ValueError(
            "Available spare, customer premises and unassigned work asset required"
        )
    move_asset(
        db,
        asset,
        AssetMove(
            reason="Documented approved customer installation",
            expected_revision=b.asset_revision,
            to_state="installed",
            to_location_id=location.id,
            technician_id=w.technician_id,
        ),
        u.id,
    )
    w.asset_id = asset.id
    w.revision += 1
    field.event(
        db,
        w,
        u,
        "application_asset_installed",
        "Protected customer installation documentation",
        {"application_id": a.id, "asset_id": asset.id},
    )
    s.event(
        db,
        a,
        u,
        "installation_asset_assigned",
        b.reason,
        {"work_order_id": w.id, "asset_id": asset.id},
    )
    return {
        "id": a.id,
        "asset_id": asset.id,
        "work_order_id": w.id,
        "revision": a.revision,
        "network_changes": 0,
    }
