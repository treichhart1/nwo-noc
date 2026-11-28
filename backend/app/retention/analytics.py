"""Bounded monthly cohort and observed-event analytics; unavailable is not zero."""

from datetime import date, datetime, timezone
from collections import Counter, defaultdict
from decimal import Decimal
from sqlalchemy import select, func
from app.customers.models import CustomerService
from app.customers.powercode_models import PowercodeServiceHistory
from app.signup.models import AppWork
from app.field.models import WorkOrder
from .models import Case, CaseService, CaseEvent, Cohort, Offer


def next_month(d):
    return date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)


def cohort_metrics(opening, closing, all_opening=None, all_closing=None):
    if not opening or not closing or not opening.complete or not closing.complete:
        return {
            "opening_accounts": None,
            "opening_services": None,
            "customer_cancellations": None,
            "cohort_service_losses": None,
            "new_customers": None,
            "new_services": None,
            "net_customer_growth": None,
            "net_service_growth": None,
            "customer_churn_rate": None,
            "service_churn_rate": None,
            "denominator_status": "unavailable: complete opening and closing source cohorts required",
        }
    os = {int(m["service_id"]) for m in opening.members}
    cs = {int(m["service_id"]) for m in closing.members}
    oa = {int(m["customer_id"]) for m in opening.members}
    ca = {int(m["customer_id"]) for m in closing.members}
    global_oa = {int(m["customer_id"]) for m in (all_opening or opening).members}
    global_ca = {int(m["customer_id"]) for m in (all_closing or closing).members}
    global_os = {int(m["service_id"]) for m in (all_opening or opening).members}
    global_cs = {int(m["service_id"]) for m in (all_closing or closing).members}
    account_losses = oa - global_ca
    service_losses = os - global_cs
    new_accounts = ca - global_oa
    new_services = cs - global_os
    return {
        "opening_accounts": len(oa),
        "opening_services": len(os),
        "customer_cancellations": len(account_losses),
        "cohort_service_losses": len(service_losses),
        "new_customers": len(new_accounts),
        "new_services": len(new_services),
        "net_customer_growth": len(new_accounts) - len(account_losses),
        "net_service_growth": len(new_services) - len(service_losses),
        "customer_churn_rate": len(account_losses) / len(oa) if oa else None,
        "service_churn_rate": len(service_losses) / len(os) if os else None,
        "denominator_status": "complete operator-attested source cohorts",
        "scope": "month-opening cohort attrition; not every intramonth disconnect episode",
    }


def summary(db, month, site_id=None):
    start = date.fromisoformat(month + "-01")
    end = next_month(start)
    lo = datetime.combine(start, datetime.min.time(), timezone.utc)
    hi = datetime.combine(end, datetime.min.time(), timezone.utc)
    history = list(
        db.scalars(
            select(PowercodeServiceHistory)
            .where(
                PowercodeServiceHistory.source_updated_at >= lo,
                PowercodeServiceHistory.source_updated_at < hi,
                PowercodeServiceHistory.status == "disconnected",
                PowercodeServiceHistory.previous_status.is_not(None),
                PowercodeServiceHistory.previous_status != "disconnected",
            )
            .order_by(PowercodeServiceHistory.id)
            .limit(10001)
        )
    )
    if len(history) > 10000:
        raise ValueError(
            "Monthly source history exceeds bounded report; narrow or use approved background reporting"
        )
    case_rows = list(
        db.execute(
            select(CaseService, Case)
            .join(Case)
            .where(
                CaseService.actual_disconnected_at >= lo,
                CaseService.actual_disconnected_at < hi,
            )
            .order_by(CaseService.id)
            .limit(10001)
        )
    )
    if len(case_rows) > 10000:
        raise ValueError("Monthly cancellation evidence exceeds report bound")
    services = {
        s.id: s
        for s in db.scalars(
            select(CustomerService).where(
                CustomerService.id.in_(
                    {h.service_id for h in history}
                    | {r.service_id for r, c in case_rows}
                )
            )
        )
    }
    lost = {h.service_id for h in history}
    lost.update(r.service_id for r, c in case_rows)
    contexts = {r.service_id: (r, c) for r, c in case_rows}
    if site_id:
        lost = {
            s
            for s in lost
            if (
                contexts[s][0].snapshot.get("site_id")
                if s in contexts
                else services[s].site_id
            )
            == site_id
        }
    opening = db.scalar(select(Cohort).where(Cohort.as_of == start))
    closing = db.scalar(select(Cohort).where(Cohort.as_of == end))
    all_opening, all_closing = opening, closing
    if site_id:
        from types import SimpleNamespace

        opening = (
            SimpleNamespace(
                complete=opening.complete,
                members=[m for m in opening.members if m.get("site_id") == site_id],
            )
            if opening
            else None
        )
        closing = (
            SimpleNamespace(
                complete=closing.complete,
                members=[m for m in closing.members if m.get("site_id") == site_id],
            )
            if closing
            else None
        )
    groups = {
        k: Counter()
        for k in (
            "package",
            "tower",
            "sector",
            "town",
            "county",
            "reported_reason",
            "operator_reason",
            "competitor",
        )
    }
    loss = defaultdict(Decimal)
    unknown = 0
    voluntary = Counter()
    for sid in sorted(lost):
        link, c = contexts.get(sid, (None, None))
        sv = services[sid]
        snap = link.snapshot if link else {}
        values = {
            "package": snap.get("package_id")
            or snap.get("plan")
            or sv.package_id
            or sv.plan
            or "unknown",
            "tower": str(snap.get("site_id") or sv.site_id or "unknown"),
            "sector": str(snap.get("sector_id") or sv.sector_id or "unknown"),
            "town": snap.get("town") or "unknown",
            "county": snap.get("county") or "unknown",
            "reported_reason": c.reported_reason if c else "unknown",
            "operator_reason": c.operator_reason or "unknown" if c else "unknown",
            "competitor": str(c.competitor_id) if c and c.competitor_id else "unknown",
        }
        for k, v in values.items():
            groups[k][v] += 1
        from .models import Reason

        reason = db.get(Reason, c.operator_reason or c.reported_reason) if c else None
        voluntary[reason.voluntary if reason else "unknown"] += 1
        amount = snap.get("monthly_recurring") if snap else sv.monthly_recurring
        currency = snap.get("currency") if snap else sv.currency
        basis = (
            (snap.get("revenue_basis") or "unavailable") if snap else sv.revenue_basis
        )
        if amount is None or not currency or basis not in {"contracted", "estimated"}:
            unknown += 1
        else:
            basis = basis + "_snapshot"
            for dimension in (
                "total",
                "tower",
                "package",
                "competitor",
                "reported_reason",
            ):
                loss[(currency, basis, dimension, values.get(dimension, "all"))] += (
                    Decimal(str(amount))
                )
    result = {
        **cohort_metrics(opening, closing, all_opening, all_closing),
        "month": month,
        "site_id": site_id,
        "observed_unique_service_cancellations": len(lost),
        "observed_affected_accounts": len({services[s].customer_id for s in lost}),
        "voluntary_service_classification": dict(voluntary),
        "breakdowns": {k: dict(v) for k, v in groups.items()},
        "revenue_loss": [
            {
                "currency": k[0],
                "basis": k[1],
                "dimension": k[2],
                "value": k[3],
                "monthly_recurring_snapshot": str(v),
                "annualized_recurring_snapshot": str(v * 12),
            }
            for k, v in loss.items()
        ],
        "services_without_pricing": unknown,
        "new_recurring_revenue": None,
        "net_recurring_revenue_change": None,
        "average_tenure_days": None,
        "tenure_status": "Verified service start dates are unavailable; first import is not subscription start",
        "invoiced_or_collected_revenue": False,
        "annualization_caution": "12 times the observed recurring snapshot; not guaranteed future cash loss",
        "new_installation_work_orders": db.scalar(
            select(func.count())
            .select_from(AppWork)
            .join(WorkOrder, WorkOrder.id == AppWork.work_order_id)
            .where(
                AppWork.kind == "installation",
                WorkOrder.status == "completed",
                WorkOrder.completed_at >= lo,
                WorkOrder.completed_at < hi,
                *([WorkOrder.site_id == site_id] if site_id else []),
            )
        ),
        "initial_disconnected_imports_excluded": True,
        "reason_scope": "Customer reports, operator classifications and unknown source reasons remain separate",
        "cohort_caution": "Account loss requires complete all-service membership; observed affected accounts are not account churn",
    }
    # Full opening/closing snapshots support revenue change only when every member has explicit pricing.
    result["historical_reports"] = historical_reports(db, start, end, site_id)
    result["retention"] = retained(db, lo, hi, site_id)
    money = cohort_revenue(opening, closing)
    result["cohort_revenue"] = money
    result["new_recurring_revenue"] = (
        [
            {"currency": m["currency"], "basis": m["basis"], "monthly": m["new"]}
            for m in money
        ]
        if money is not None
        else None
    )
    result["net_recurring_revenue_change"] = (
        [
            {"currency": m["currency"], "basis": m["basis"], "monthly": m["net_change"]}
            for m in money
        ]
        if money is not None
        else None
    )
    starts = (
        {
            m["service_id"]: date.fromisoformat(m["started_on"])
            for m in opening.members
            if m.get("started_on")
        }
        if opening
        else {}
    )
    dates = {h.service_id: h.source_updated_at.date() for h in history}
    dates.update({r.service_id: r.actual_disconnected_at.date() for r, c in case_rows})
    durations = [
        (dates[s] - starts[s]).days
        for s in lost
        if s in starts and s in dates and dates[s] >= starts[s]
    ]
    result["average_tenure_days"] = (
        sum(durations) / len(durations) if durations else None
    )
    result["tenure_service_count"] = len(durations)
    if durations:
        result["tenure_status"] = (
            "Verified operator-attested service start evidence only; unavailable members excluded and counted"
        )
    result["tenure_unavailable_services"] = len(lost) - len(durations)
    return result


def retained(db, lo, hi, site_id=None):
    rows = list(
        db.execute(
            select(CaseService, Case)
            .join(Case)
            .where(
                Case.retention_state == "customer_retained",
                Case.id.in_(
                    select(CaseEvent.case_id).where(
                        CaseEvent.action == "retention_review",
                        CaseEvent.evidence["retention_state"].as_string()
                        == "customer_retained",
                        CaseEvent.created_at >= lo,
                        CaseEvent.created_at < hi,
                    )
                ),
            )
            .order_by(CaseService.id)
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise ValueError("Retention report exceeds bounded scope")
    amounts = defaultdict(Decimal)
    seen = set()
    cases = set()
    unknown = 0
    for r, c in rows:
        if r.service_id in seen or site_id and r.snapshot.get("site_id") != site_id:
            continue
        seen.add(r.service_id)
        cases.add(c.id)
        v = r.snapshot.get("monthly_recurring")
        cur = r.snapshot.get("currency")
        if (
            v is None
            or not cur
            or r.snapshot.get("revenue_basis") not in {"contracted", "estimated"}
        ):
            unknown += 1
        else:
            amounts[(cur, r.snapshot.get("revenue_basis") or "unavailable")] += Decimal(
                str(v)
            )
    offers = (
        list(
            db.scalars(
                select(Offer)
                .where(Offer.case_id.in_(cases), Offer.status == "accepted")
                .order_by(Offer.id.desc())
            )
        )
        if cases
        else []
    )
    costs = defaultdict(lambda: {"monthly": Decimal(0), "one_time": Decimal(0)})
    used = set()
    complete = True
    for o in offers:
        if o.case_id in used:
            continue
        used.add(o.case_id)
        if not o.currency or o.monthly_cost is None or o.one_time_cost is None:
            complete = False
            continue
        costs[o.currency]["monthly"] += o.monthly_cost
        costs[o.currency]["one_time"] += o.one_time_cost
    return {
        "selected_retained_services": len(seen),
        "retained_monthly_snapshot": [
            {"currency": k[0], "basis": k[1] + "_snapshot", "monthly": str(v)}
            for k, v in amounts.items()
        ],
        "unknown_pricing": unknown,
        "estimated_offer_costs": {
            k: {kk: str(vv) for kk, vv in v.items()} for k, v in costs.items()
        },
        "offer_cost_data_complete": complete,
        "estimated_profitability": None,
        "profitability_limitation": "Cannot infer fully allocated operating expenses or accounting treatment from an offer; use existing finance scope calculation",
        "automatic_billing_changes": False,
    }


def cohort_revenue(opening, closing):
    if not opening or not closing or not opening.complete or not closing.complete:
        return None
    old = {m["service_id"]: m for m in opening.members}
    new = {m["service_id"]: m for m in closing.members}
    if any(
        m.get("monthly") is None or not m.get("currency")
        for m in [*old.values(), *new.values()]
    ):
        return None
    groups = defaultdict(
        lambda: {
            "opening": Decimal(0),
            "closing": Decimal(0),
            "new": Decimal(0),
            "lost": Decimal(0),
        }
    )
    for sid, m in old.items():
        k = (m["currency"], m.get("basis", "estimated"))
        groups[k]["opening"] += Decimal(m["monthly"])
        if sid not in new:
            groups[k]["lost"] += Decimal(m["monthly"])
    for sid, m in new.items():
        k = (m["currency"], m.get("basis", "estimated"))
        groups[k]["closing"] += Decimal(m["monthly"])
        if sid not in old:
            groups[k]["new"] += Decimal(m["monthly"])
    return [
        {
            "currency": k[0],
            "basis": k[1],
            **{kk: str(vv) for kk, vv in v.items()},
            "net_change": str(v["closing"] - v["opening"]),
        }
        for k, v in groups.items()
    ]


def historical_reports(db, start, end, site_id=None):
    filters = [
        Case.source == "historical_report",
        Case.reported_disconnected_date >= start,
        Case.reported_disconnected_date < end,
    ]
    if site_id:
        filters.append(
            Case.id.in_(
                select(CaseService.case_id).where(
                    CaseService.snapshot["site_id"].as_integer() == site_id
                )
            )
        )
    rows = db.execute(
        select(
            Case.reported_reason,
            Case.competitor_id,
            func.count(Case.id),
            func.count(Case.customer_id),
        )
        .where(*filters)
        .group_by(Case.reported_reason, Case.competitor_id)
        .limit(1001)
    ).all()
    if len(rows) > 1000:
        raise ValueError("Historical report grouping exceeds bound")
    return {
        "reported_record_count": sum(r[2] for r in rows),
        "identity_reviewed_records": sum(r[3] for r in rows),
        "groups": [
            {"reported_reason": r[0], "competitor_id": r[1], "records": r[2]}
            for r in rows
        ],
        "basis": "Imported historical reports only; not verified service/account cancellations or a churn denominator",
        "additive_to_verified_cancellations": False,
        "unmatched_scope": "Unmatched records appear in network-wide reports only; never invent tower or customer associations",
    }
