"""Decimal, evidence-based operational analytics. No network, billing or construction writes."""

import hashlib, json, csv, io
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from datetime import date
from sqlalchemy import select, func, or_
from app.field import service as field
from app.field.models import now
from app.models.entities import Site, Sector, NetworkLink, User
from app.customers.models import CustomerService
from app.equipment.models import Asset, Financial, CostEntry, Location
from app.signup.models import Plan
from app.unified.models import Edge, Circuit, Node, Path, Hop
from .models import *
from .contracts import *

D = Decimal
CENT = D(".01")
REF = {
    "tower": ("site_id", Site),
    "sector": ("sector_id", Sector),
    "backhaul": ("link_id", NetworkLink),
    "edge": ("edge_id", Edge),
    "circuit": ("circuit_id", Circuit),
    "service": ("service_id", CustomerService),
    "plan": ("plan_id", Plan),
    "asset": ("asset_id", Asset),
}
REQUIRED = OPEX | {"revenue"}


def admin(u):
    field.admin(u)


def get(db, cls, id):
    return field.get(db, cls, id)


def digest(x):
    return hashlib.sha256(
        json.dumps(x, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def view(x):
    return {
        c.name: (
            str(v)
            if isinstance(v, D)
            else v.isoformat() if hasattr(v, "isoformat") else v
        )
        for c in x.__table__.columns
        for v in [getattr(x, c.name)]
    }


def audit(db, u, action, id, details=None):
    field.audit(db, u.id, "finance." + action, "operational_finance", id, details or {})


def open_period(db, id):
    p = db.scalar(select(Period).where(Period.id == id).with_for_update())
    if not p:
        raise LookupError()
    if p.closed:
        raise ValueError(
            "Financial period closed; preserve history and open an adjustment period"
        )
    return p


def create_period(db, u, b):
    admin(u)
    if db.scalar(
        select(Period.id).where(
            Period.currency == b.currency,
            Period.starts_on < b.ends_on,
            Period.ends_on > b.starts_on,
        )
    ):
        raise ValueError(
            "Ledger periods cannot overlap within a currency; use period trends for historical comparisons"
        )
    p = Period(**b.model_dump(exclude={"operation_key", "reason"}), created_by=u.id)
    db.add(p)
    db.flush()
    return view(p)


def create_scope(db, u, b):
    admin(u)
    kw = {}
    ctx = {}
    if b.kind in REF:
        if b.reference_id is None or b.service_ids:
            raise ValueError("One existing canonical reference required")
        key, cls = REF[b.kind]
        r = get(db, cls, b.reference_id)
        kw[key] = r.id
        if db.scalar(
            select(Scope.id).where(getattr(Scope, key) == r.id, Scope.kind == b.kind)
        ):
            raise ValueError("Financial scope already mapped; reuse it")
        if b.kind == "sector":
            kw["site_id"] = r.site_id
        if b.kind == "service":
            if r.mapping_state != "verified" or r.source_deleted_at:
                raise ValueError("Reviewed existing customer mapping required")
            kw.update(site_id=r.site_id, sector_id=r.sector_id)
        if b.kind == "asset":
            loc = db.get(Location, r.location_id) if r.location_id else None
            if loc:
                kw["site_id"] = loc.site_id
        ctx = {"canonical_id": r.id, "kind": b.kind}
        if b.kind == "plan":
            ctx["plan_ref"] = r.powercode_plan_ref
    elif b.reference_id is not None:
        raise ValueError("Financial-only group/network cannot invent infrastructure")
    elif b.kind == "group":
        if not b.service_ids or len(set(b.service_ids)) != len(b.service_ids):
            raise ValueError("Unique existing customer services required")
        for id in b.service_ids:
            get(db, CustomerService, id)
    elif b.service_ids:
        raise ValueError("Only financial groups have membership lists")
    r = Scope(code=b.code, name=b.name, kind=b.kind, context=ctx, created_by=u.id, **kw)
    db.add(r)
    db.flush()
    for id in b.service_ids:
        db.add(GroupMember(scope_id=r.id, service_id=id))
    return view(r)


def dimensions(db, scope):
    d = {
        k: getattr(scope, k)
        for k in [
            "site_id",
            "sector_id",
            "service_id",
            "asset_id",
            "circuit_id",
            "link_id",
            "edge_id",
            "plan_id",
        ]
    }
    if scope.service_id:
        s = get(db, CustomerService, scope.service_id)
        if s.mapping_state != "verified" or s.source_deleted_at:
            raise ValueError("Customer mapping needs review")
        if s.sector_id and get(db, Sector, s.sector_id).site_id != s.site_id:
            raise ValueError("Conflicting tower/sector customer mapping")
        d.update(
            site_id=s.site_id,
            sector_id=s.sector_id,
            customer_id=s.customer_id,
            plan_ref=s.plan,
            service_type=s.service_type,
            service_status=s.status,
        )
    if scope.circuit_id:
        c = get(db, Circuit, scope.circuit_id)
        d.update(service_id=c.service_id, contracted_bandwidth_mbps=c.bandwidth_mbps)
        if c.service_id:
            s = get(db, CustomerService, c.service_id)
            d.update(
                site_id=s.site_id,
                sector_id=s.sector_id,
                customer_id=s.customer_id,
                plan_ref=s.plan,
                service_type="transport",
                service_status=s.status,
            )
    if scope.plan_id:
        d["plan_ref"] = get(db, Plan, scope.plan_id).powercode_plan_ref
    if scope.asset_id:
        a = get(db, Asset, scope.asset_id)
        loc = db.get(Location, a.location_id) if a.location_id else None
        d.update(asset_state=a.state, site_id=loc.site_id if loc else None)
    return d


def add_entry(db, u, period_id, b, source_type="manual", source_key=None):
    admin(u)
    p = open_period(db, period_id)
    sc = get(db, Scope, b.scope_id)
    if b.kind == "revenue" and sc.circuit_id and b.category == "subscription":
        raise ValueError("Transport circuits require transport or installation revenue")
    r = Entry(
        period_id=p.id,
        scope_id=sc.id,
        **b.model_dump(exclude={"scope_id", "operation_key", "reason", "external_key"}),
        source_type=source_type,
        external_key=source_key or b.external_key,
        dimensions=dimensions(db, sc),
        created_by=u.id,
    )
    db.add(r)
    db.flush()
    return view(r)


def reverse(db, u, id, b):
    r = get(db, Entry, id)
    target = getattr(b, "adjustment_period_id", None) or r.period_id
    period = open_period(db, target)
    original_period = get(db, Period, r.period_id)
    if period.currency != original_period.currency or (
        target != r.period_id and period.starts_on < original_period.ends_on
    ):
        raise ValueError("Adjustments require same currency and a later period")
    if r.reversal_of or db.scalar(select(Entry.id).where(Entry.reversal_of == id)):
        raise ValueError("Already reversed or reversal entry")
    x = Entry(
        period_id=target,
        scope_id=r.scope_id,
        kind=r.kind,
        category=r.category,
        basis=r.basis,
        amount=-r.amount,
        source_type="reversal",
        external_key=str(id),
        evidence=b.reason,
        verified_collection=r.verified_collection,
        dimensions=r.dimensions,
        reversal_of=r.id,
        created_by=u.id,
    )
    db.add(x)
    db.flush()
    return view(x)


def matches(db, scope, d, scope_id):
    if scope.kind == "network":
        return True
    if scope.kind == "group":
        return d.get("service_id") in set(
            db.scalars(
                select(GroupMember.service_id).where(GroupMember.scope_id == scope.id)
            )
        )
    if scope.kind == "plan":
        return d.get("plan_ref") is not None and d.get("plan_ref") == scope.context.get(
            "plan_ref", get(db, Plan, scope.plan_id).powercode_plan_ref
        )
    return d.get(REF[scope.kind][0]) == getattr(scope, REF[scope.kind][0])


def active_services(db, scope):
    q = select(CustomerService).where(
        CustomerService.status == "active",
        CustomerService.mapping_state == "verified",
        CustomerService.source_deleted_at.is_(None),
    )
    if scope.kind == "tower":
        q = q.where(CustomerService.site_id == scope.site_id)
    elif scope.kind == "sector":
        q = q.where(CustomerService.sector_id == scope.sector_id)
    elif scope.kind == "service":
        q = q.where(CustomerService.id == scope.service_id)
    elif scope.kind == "group":
        q = q.where(
            CustomerService.id.in_(
                select(GroupMember.service_id).where(GroupMember.scope_id == scope.id)
            )
        )
    elif scope.kind == "circuit":
        q = q.where(CustomerService.id == get(db, Circuit, scope.circuit_id).service_id)
    elif scope.kind == "plan":
        q = q.where(
            CustomerService.plan == get(db, Plan, scope.plan_id).powercode_plan_ref
        )
    elif scope.kind != "network":
        return []
    return list(db.scalars(q.limit(10001)))


def allocate(db, u, id, b):
    admin(u)
    e = get(db, Entry, id)
    open_period(db, e.period_id)
    if (
        e.kind not in {"opex", "capex", "depreciation", "replacement_estimate"}
        or e.reversal_of
        or db.scalar(select(Entry.id).where(Entry.reversal_of == e.id))
    ):
        raise ValueError("Unreversed cost entry required")
    if db.scalar(select(Allocation.id).where(Allocation.entry_id == e.id)):
        raise ValueError(
            "Immutable allocation already exists; reverse expense and document corrected cost"
        )
    weights = []
    evidence = []
    for t in sorted(b.targets, key=lambda x: x.scope_id):
        sc = get(db, Scope, t.scope_id)
        w = t.weight
        if b.method == "equal":
            w = D(1)
        elif b.method == "customer_count":
            services = active_services(db, sc)
            w = D(len({s.customer_id for s in services}))
            evidence.append(
                {
                    "scope_id": sc.id,
                    "reviewed_active_service_ids": [s.id for s in services],
                }
            )
        elif b.method == "revenue_weighted":
            report = calculate(db, e.period_id, sc.id, b.revenue_basis, "actual")
            w = D(report["recurring_revenue"])
            evidence.append(
                {"scope_id": sc.id, "basis": b.revenue_basis, "revenue": str(w)}
            )
        if w is None or w <= 0:
            raise ValueError(
                "All allocation weights need positive supported evidence; exclude zero-weight destinations"
            )
        weights.append((sc, D(w)))
    total = sum(w for _, w in weights)
    cents = int(e.amount * 100)
    shares = [
        (sc, w, int((D(cents) * w / total).to_integral_value(rounding=ROUND_FLOOR)))
        for sc, w in weights
    ]
    leftover = cents - sum(c for _, _, c in shares)
    # Largest remainder, deterministic ID tiebreaker; exact cent conservation.
    ranked = sorted(
        range(len(shares)),
        key=lambda i: (
            -(D(cents) * shares[i][1] / total - shares[i][2]),
            shares[i][0].id,
        ),
    )
    bonuses = set(ranked[:leftover])
    a = Allocation(
        entry_id=e.id,
        method=b.method,
        evidence={
            "reason": b.reason,
            "captured_at": now().isoformat(),
            "weights": evidence,
            "capacity_weights_are_operator_documented": b.method == "capacity_weighted",
            "revenue_basis": b.revenue_basis,
        },
        created_by=u.id,
    )
    db.add(a)
    db.flush()
    for i, (sc, w, c) in enumerate(shares):
        db.add(
            Share(
                allocation_id=a.id,
                scope_id=sc.id,
                weight=w,
                amount=D(c + (i in bonuses)) / 100,
                dimensions=dimensions(db, sc),
            )
        )
    db.flush()
    return {
        "id": a.id,
        "method": a.method,
        "total": str(e.amount),
        "shares": [
            view(x)
            for x in db.scalars(select(Share).where(Share.allocation_id == a.id))
        ],
        "evidence": a.evidence,
    }


def attest(db, u, id, b):
    open_period(db, id)
    get(db, Scope, b.scope_id)
    a = Attestation(
        period_id=id,
        **b.model_dump(exclude={"operation_key", "reason"}),
        created_by=u.id,
    )
    db.add(a)
    db.flush()
    return view(a)


def financial_data(db, p):
    entries = list(
        db.scalars(
            select(Entry)
            .join(Period, Entry.period_id == Period.id)
            .where(
                or_(
                    Entry.period_id == p.id,
                    (Entry.kind == "capex")
                    & (Period.ends_on <= p.ends_on)
                    & (Period.currency == p.currency),
                )
            )
            .order_by(Entry.id)
            .limit(20001)
        )
    )
    if len(entries) > 20000:
        raise ValueError("Report period exceeds bounded ledger size; partition periods")
    selected_ids = sorted(
        {e.id for e in entries} | {e.reversal_of for e in entries if e.reversal_of}
    )
    allocs = list(
        db.scalars(select(Allocation).where(Allocation.entry_id.in_(selected_ids)))
    )
    by_entry = {a.entry_id: a for a in allocs}
    shares = {}
    for x in db.scalars(
        select(Share)
        .join(Allocation, Share.allocation_id == Allocation.id)
        .join(Entry, Entry.id == Allocation.entry_id)
        .where(Entry.id.in_(selected_ids))
    ):
        shares.setdefault(x.allocation_id, []).append(x)
    return entries, by_entry, shares


def calculate(
    db,
    period_id,
    scope_id,
    revenue_basis="contracted",
    expense_basis="actual",
    service_type=None,
    _dataset=None,
):
    p = get(db, Period, period_id)
    sc = get(db, Scope, scope_id)
    entries, by_entry, shares = (
        _dataset if _dataset is not None else financial_data(db, p)
    )
    totals = {
        k: D(0)
        for k in [
            "revenue",
            "recurring_revenue",
            "installation_revenue",
            "opex",
            "capex",
            "depreciation",
            "replacement_estimate",
        ]
    }
    lines = []
    customers = set()
    services = set()
    categories = {}
    group_ids = (
        set(
            db.scalars(
                select(GroupMember.service_id).where(GroupMember.scope_id == sc.id)
            )
        )
        if sc.kind == "group"
        else set()
    )
    plan_ref = sc.context.get("plan_ref") if sc.plan_id else None

    def selected(d, sid):
        if sid == sc.id:
            return True
        if sc.kind == "network":
            return True
        if sc.kind == "group":
            return d.get("service_id") in group_ids
        if sc.kind == "plan":
            return plan_ref is not None and d.get("plan_ref") == plan_ref
        return d.get(REF[sc.kind][0]) == getattr(sc, REF[sc.kind][0])

    for e in entries:
        if e.basis != (
            revenue_basis
            if e.kind == "revenue"
            else (
                "assumption"
                if e.kind == "depreciation"
                else "estimated" if e.kind == "replacement_estimate" else expense_basis
            )
        ):
            continue
        original = get(db, Entry, e.reversal_of) if e.reversal_of else e
        a = by_entry.get(original.id)
        portions = (
            [
                (x.scope_id, x.dimensions, x.amount * (-1 if e.reversal_of else 1))
                for x in shares.get(a.id, [])
            ]
            if a
            else [(e.scope_id, e.dimensions, e.amount)]
        )
        for sid, d, amount in portions:
            if not selected(d, sid) or (
                service_type and d.get("service_type") != service_type
            ):
                continue
            totals[e.kind] += amount
            categories[e.category] = categories.get(e.category, D(0)) + amount
            if e.kind == "revenue":
                totals[
                    (
                        "installation_revenue"
                        if e.category == "installation"
                        else "recurring_revenue"
                    )
                ] += amount
                if d.get("customer_id"):
                    customers.add(d["customer_id"])
                if d.get("service_id"):
                    services.add(d["service_id"])
            lines.append(
                {
                    "entry_id": e.id,
                    "scope_id": sid,
                    "kind": e.kind,
                    "category": e.category,
                    "basis": e.basis,
                    "amount": str(amount),
                    "allocation_method": a.method if a else "direct",
                    "allocation_id": a.id if a else None,
                    "source_type": e.source_type,
                    "external_key": e.external_key,
                    "dimensions": d,
                    "evidence": e.evidence,
                }
            )
    att = db.scalar(
        select(Attestation)
        .where(
            Attestation.period_id == p.id,
            Attestation.scope_id == sc.id,
            Attestation.revenue_basis == revenue_basis,
            Attestation.expense_basis == expense_basis,
        )
        .order_by(Attestation.id.desc())
        .limit(1)
    )
    covered = set(att.categories) if att else set()
    missing = sorted(REQUIRED - covered)
    if service_type:
        missing.append("filtered_slice_completeness_not_attested")
    complete = not missing
    contribution = totals["revenue"] - totals["opex"]
    recurring = totals["recurring_revenue"] - totals["opex"]
    months = D((p.ends_on - p.starts_on).days) * 12 / D("365.2425")
    monthly = recurring / months
    cashlabel = (
        "verified collected receipts"
        if revenue_basis == "collected"
        else revenue_basis + " revenue; not collected cash"
    )
    result = {
        "period": view(p),
        "scope": view(sc),
        "revenue_basis": revenue_basis,
        "expense_basis": expense_basis,
        "basis_label": cashlabel,
        "service_type_filter": service_type,
        "totals": {k: str(v.quantize(CENT)) for k, v in totals.items()},
        "recurring_revenue": str(totals["recurring_revenue"]),
        "known_contribution_subtotal": str(contribution),
        "complete": complete,
        "missing_inputs": missing,
        "coverage_attestation": view(att) if att else None,
        "contribution": str(contribution) if complete else None,
        "operating_margin_percent": (
            str((contribution / totals["revenue"] * 100).quantize(CENT))
            if complete and totals["revenue"] > 0
            else None
        ),
        "operating_contribution_after_depreciation": (
            str(contribution - totals["depreciation"])
            if complete and "depreciation" in covered
            else None
        ),
        "capital_complete": "capital" in covered,
        "payback_months": (
            str((totals["capex"] / monthly).quantize(CENT))
            if complete and "capital" in covered and totals["capex"] > 0 and monthly > 0
            else None
        ),
        "operational_roi_percent": (
            str((contribution / totals["capex"] * 100).quantize(CENT))
            if complete and "capital" in covered and totals["capex"] > 0
            else None
        ),
        "documented_revenue_customer_count": len(customers),
        "documented_revenue_service_count": len(services),
        "revenue_per_customer": (
            str((totals["revenue"] / len(customers)).quantize(CENT))
            if customers
            else None
        ),
        "cost_per_customer": (
            str((totals["opex"] / len(customers)).quantize(CENT))
            if complete and customers
            else None
        ),
        "monthly_arpu": (
            str((totals["recurring_revenue"] / months / len(customers)).quantize(CENT))
            if customers
            else None
        ),
        "categories": {k: str(v) for k, v in categories.items()},
        "lines": lines,
        "recommendations": [],
        "as_of": now().isoformat(),
        "automatic_actions": False,
        "financial_scope": "operational network only; fiber engineering/construction budgets excluded",
        "capital_basis": "Cumulative documented investment through period end; acquisition-location snapshots, not a balance sheet or cash-flow statement",
    }
    if not complete:
        result["recommendations"].append(
            {
                "priority": "review",
                "action": "Complete expense and revenue evidence before judging profitability",
                "evidence": missing,
                "uncertainty": "Inputs incomplete",
            }
        )
    elif contribution < 0:
        result["recommendations"].append(
            {
                "priority": "high",
                "action": "Review operational costs, allocation and service revenue; no retirement decision",
                "evidence": {
                    "revenue": str(totals["revenue"]),
                    "opex": str(totals["opex"]),
                    "attestation_id": att.id,
                },
                "uncertainty": "Financial margin alone does not establish service alternatives or contractual feasibility",
            }
        )
    policy_row = db.scalar(
        select(Assumption)
        .where(
            Assumption.period_id == p.id,
            Assumption.scope_id == sc.id,
            Assumption.inputs["kind"].as_string() == "screening_policy",
        )
        .order_by(Assumption.id.desc())
        .limit(1)
    )
    policy = {
        "maintenance_share_percent": 25,
        "backhaul_revenue_percent": 30,
        "low_margin_percent": 20,
        "low_utilization_percent": 10,
        "high_utilization_percent": 85,
    }
    if policy_row:
        policy.update({k: policy_row.inputs[k] for k in policy})
    result["screening_policy"] = {
        "thresholds": policy,
        "assumption_id": policy_row.id if policy_row else None,
        "basis": (
            "Operator configuration"
            if policy_row
            else "Documented advisory defaults; not financial facts"
        ),
    }

    def recommend(action, evidence, uncertainty, priority="review"):
        result["recommendations"].append(
            {
                "priority": priority,
                "action": action,
                "evidence": evidence,
                "uncertainty": uncertainty,
                "screening_policy_id": policy_row.id if policy_row else None,
            }
        )

    if (
        totals["opex"] > 0
        and categories.get("maintenance", D(0)) / totals["opex"] * 100
        > policy["maintenance_share_percent"]
    ):
        recommend(
            "Review maintenance history and replacement options",
            {
                "maintenance": str(categories["maintenance"]),
                "known_opex": str(totals["opex"]),
            },
            "Cost-share screening only; no equipment failure prediction",
        )
    transport_cost = sum(
        categories.get(k, D(0)) for k in ["backhaul", "upstream", "license"]
    )
    if (
        complete
        and totals["recurring_revenue"] > 0
        and transport_cost / totals["recurring_revenue"] * 100
        > policy["backhaul_revenue_percent"]
    ):
        recommend(
            "Review backhaul cost allocation and documented transport alternatives",
            {
                "documented_transport_cost": str(transport_cost),
                "recurring_revenue": str(totals["recurring_revenue"]),
            },
            "Shared allocation and contractual obligations require review; transport-only services need not consume upstream internet",
        )
    if (
        complete
        and totals["revenue"] > 0
        and contribution / totals["revenue"] * 100 < policy["low_margin_percent"]
    ):
        recommend(
            (
                "Review business transport pricing evidence"
                if sc.circuit_id
                else "Review low-margin infrastructure and operational alternatives"
            ),
            {
                "period_contribution": str(contribution),
                "revenue": str(totals["revenue"]),
            },
            "No price change or tower retirement authorized; confirm service dependencies and contract terms",
        )
    if sc.sector_id:
        from datetime import datetime, timezone
        from app.tower_health.models import TowerSample

        sec = get(db, Sector, sc.sector_id)
        end = datetime.combine(p.ends_on, datetime.min.time(), tzinfo=timezone.utc)
        samples = list(
            db.scalars(
                select(TowerSample)
                .where(
                    TowerSample.sector_id == sc.sector_id,
                    TowerSample.observed_at >= p.starts_on,
                    TowerSample.observed_at < end,
                )
                .order_by(TowerSample.observed_at.desc())
                .limit(3)
            )
        )
        measurements = []
        for sample in samples:
            value = sample.data.get("metrics", {}).get("utilization_percent")
            if (
                sample.data.get("metrics", {}).get("collector_ok") is not True
                or "utilization_percent" in sample.data.get("unavailable", {})
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 100
            ):
                continue
            measurements.append(
                {
                    "sample_id": sample.id,
                    "value": value,
                    "observed_at": sample.observed_at.isoformat(),
                }
            )
        reference = min(now(), end)
        fresh = (
            bool(samples)
            and 0 <= (reference - samples[0].observed_at).total_seconds() <= 900
        )
        consecutive = (
            len(measurements) == 3
            and len({r.observed_at for r in samples}) == 3
            and (samples[0].observed_at - samples[-1].observed_at).total_seconds()
            <= 3600
        )
        result["capacity_context"] = {
            "configured_mbps": sec.capacity_mbps,
            "utilization_samples": measurements,
            "fresh_at_period_boundary": fresh,
            "source": "Existing NOC-98 recorded telemetry; configured capacity is not measured demand",
            "uncertainty": "Three recent samples cannot establish month-long peak demand or RF capacity",
        }
        if (
            fresh
            and consecutive
            and all(
                m["value"] >= policy["high_utilization_percent"] for m in measurements
            )
        ):
            recommend(
                "Investigate capacity-constrained sector upgrade options",
                measurements,
                "Recent recorded utilization only; confirm RF, backhaul capacity and economics before any investment",
            )
        elif (
            fresh
            and consecutive
            and all(
                m["value"] <= policy["low_utilization_percent"] for m in measurements
            )
        ):
            recommend(
                "Review potentially underutilized sector and growth opportunities",
                measurements,
                "Recent recorded utilization only; does not establish persistent underutilization or justify retirement",
            )
    current = active_services(db, sc)
    if len(current) > 10000:
        raise ValueError("Customer context exceeds bounded report size")
    result["current_assignment_context"] = {
        "active_services": len(current),
        "active_customers": len({x.customer_id for x in current}),
        "as_of": now().isoformat(),
        "historical_snapshot": False,
        "known": sc.kind
        in {"network", "tower", "sector", "service", "plan", "group", "circuit"},
    }
    result["contract_assumptions"] = [
        view(a)
        for a in db.scalars(
            select(Assumption)
            .where(Assumption.period_id == p.id, Assumption.scope_id == sc.id)
            .order_by(Assumption.id.desc())
            .limit(20)
        )
        if a.inputs.get("kind") == "business_transport_contract"
    ]
    if sc.asset_id:
        asset = get(db, Asset, sc.asset_id)
        fin = db.get(Financial, sc.asset_id)
        result["asset_context"] = {
            "state": asset.state,
            "purchase_date": (
                asset.purchase_date.isoformat() if asset.purchase_date else None
            ),
            "configured_useful_life_months": fin.useful_life_months if fin else None,
            "accounting_treatment_assumed": False,
        }
    return result


def snapshot(db, u, id, scope_id, revenue_basis, expense_basis, service_type=None):
    result = calculate(db, id, scope_id, revenue_basis, expense_basis, service_type)
    stable = {
        k: v
        for k, v in result.items()
        if k not in {"as_of", "current_assignment_context"}
    }
    stable["current_assignment_context"] = {
        k: v for k, v in result["current_assignment_context"].items() if k != "as_of"
    }
    sha = digest(stable)
    old = db.scalar(select(Report).where(Report.digest == sha))
    if old:
        return {"id": old.id, "digest": sha, **old.result}
    r = Report(
        period_id=id, scope_id=scope_id, digest=sha, result=result, created_by=u.id
    )
    db.add(r)
    db.flush()
    audit(db, u, "report", r.id, {"digest": sha})
    return {"id": r.id, "digest": sha, **result}


def recurring_create(db, u, b):
    get(db, Scope, b.scope_id)
    r = Recurring(**b.model_dump(exclude={"reason", "operation_key"}), created_by=u.id)
    db.add(r)
    db.flush()
    return view(r)


def materialize(db, u, pid, b):
    p = open_period(db, pid)
    ids = []
    # Never prorate or turn a monthly estimate into an actual invoice implicitly.
    expected = date(
        p.starts_on.year + (p.starts_on.month == 12),
        1 if p.starts_on.month == 12 else p.starts_on.month + 1,
        1,
    )
    if p.starts_on.day != 1 or p.ends_on != expected:
        raise ValueError(
            "Monthly recurring templates require an exact calendar month; document other periods manually"
        )
    for r in db.scalars(
        select(Recurring)
        .where(
            Recurring.starts_on <= p.starts_on,
            or_(Recurring.ends_on.is_(None), Recurring.ends_on >= p.ends_on),
        )
        .limit(1001)
    ):
        if r.currency != p.currency:
            raise ValueError("Currency conversion unavailable")
        key = f"recurring:{r.id}"
        old = db.scalar(
            select(Entry).where(
                Entry.period_id == pid,
                Entry.source_type == "recurring",
                Entry.external_key == key,
            )
        )
        if old:
            ids.append(old.id)
            continue
        e = EntryInput(
            operation_key=b.operation_key,
            reason=b.reason,
            scope_id=r.scope_id,
            kind="opex",
            category=r.category,
            basis=r.basis,
            amount=r.amount,
            external_key=key,
            evidence=r.evidence,
        )
        ids.append(add_entry(db, u, pid, e, "recurring")["id"])
    return {"id": pid, "entry_ids": ids, "external_delivery": False}


class MockRevenueAdapter:
    """Closed synthetic charge DTOs; no native Powercode endpoint or write method assumed."""

    def read(self, charges):
        return [MockCharge.model_validate(c) for c in charges]


def import_payload(db, u, pid, b):
    p = get(db, Period, pid)
    out = []
    if b.provider == "powercode_mock":
        for c in MockRevenueAdapter().read(b.charges):
            service = db.scalar(
                select(CustomerService).where(
                    CustomerService.external_id == c.service_external_id
                )
            )
            if (
                not service
                or service.status != "active"
                or service.mapping_state != "verified"
                or service.source_deleted_at
            ):
                raise ValueError("Active reviewed existing Powercode service required")
            sc = db.scalar(
                select(Scope).where(
                    Scope.kind == "service", Scope.service_id == service.id
                )
            )
            if not sc:
                raise ValueError("Explicit canonical financial service scope required")
            if c.basis == "collected" and not c.verified_collection:
                raise ValueError(
                    "Verified collection evidence required; billed is not cash"
                )
            if c.currency != p.currency:
                raise ValueError("Currency mismatch; no assumed FX")
            out.append(
                dict(
                    scope_id=sc.id,
                    kind="revenue",
                    category=c.category,
                    basis=c.basis,
                    amount=str(c.amount),
                    verified_collection=c.verified_collection,
                    external_key=c.external_key,
                    evidence=c.evidence,
                )
            )
    else:
        for aid in sorted(set(b.asset_ids)):
            a = get(db, Asset, aid)
            sc = db.scalar(
                select(Scope).where(Scope.kind == "asset", Scope.asset_id == aid)
            )
            if not sc:
                raise ValueError("Explicit canonical financial asset scope required")
            fin = db.get(Financial, aid)
            if fin:
                if fin.currency != p.currency:
                    raise ValueError("Asset currency mismatch")
                # Original purchase is imported only into the acquisition period, not every month.
                if (
                    fin.purchase_cost is not None
                    and a.purchase_date
                    and p.starts_on <= a.purchase_date < p.ends_on
                ):
                    out.append(
                        dict(
                            scope_id=sc.id,
                            kind="capex",
                            category="equipment",
                            basis="actual",
                            amount=str(fin.purchase_cost),
                            external_key=f"asset:{aid}:purchase",
                            evidence="Documented NOC-100 purchase cost; not proof of payment",
                        )
                    )
                if fin.replacement_estimate is not None:
                    out.append(
                        dict(
                            scope_id=sc.id,
                            kind="replacement_estimate",
                            category="replacement",
                            basis="estimated",
                            amount=str(fin.replacement_estimate),
                            external_key=f"asset:{aid}:replacement",
                            evidence="NOC-100 replacement planning estimate, excluded from actual cash expenses",
                        )
                    )
                if (
                    fin.depreciation_method == "straight_line_monthly"
                    and fin.useful_life_months
                    and fin.purchase_cost is not None
                    and fin.salvage_value is not None
                    and a.purchase_date
                    and a.purchase_date < p.ends_on
                ):
                    from app.equipment.service import financial_view

                    before = financial_view(db, a, max(p.starts_on, a.purchase_date))
                    after = financial_view(db, a, p.ends_on)
                    if (
                        before["book_value"] is not None
                        and after["book_value"] is not None
                    ):
                        amount = D(before["book_value"]) - D(after["book_value"])
                        out.append(
                            dict(
                                scope_id=sc.id,
                                kind="depreciation",
                                category="depreciation",
                                basis="assumption",
                                amount=str(amount),
                                external_key=f"asset:{aid}:depreciation",
                                evidence="Configured NOC-100 straight-line whole-calendar-month book-value difference; separate noncash assumption",
                            )
                        )
            for cost in db.scalars(
                select(CostEntry).where(
                    CostEntry.asset_id == aid,
                    CostEntry.at >= p.starts_on,
                    CostEntry.at < p.ends_on,
                    CostEntry.maintenance_id.is_not(None),
                )
            ):
                if cost.currency != p.currency:
                    raise ValueError("Maintenance currency mismatch")
                out.append(
                    dict(
                        scope_id=sc.id,
                        kind="opex",
                        category="maintenance",
                        basis="actual",
                        amount=str(cost.amount),
                        external_key=f"maintenance-cost:{cost.id}",
                        evidence=f"NOC-100 cost {cost.id}, maintenance {cost.maintenance_id}; NOC-102 service record links retained",
                    )
                )
    if len({(x["external_key"], x["basis"]) for x in out}) != len(out):
        raise ValueError("Duplicate incoming financial facts")
    if len(out) > 2000:
        raise ValueError(
            "Import exceeds 2000 financial facts; partition asset selections"
        )
    for row in out:
        row["dimensions_snapshot"] = dimensions(db, get(db, Scope, row["scope_id"]))
    if len(json.dumps(out, default=str).encode()) > 1048576:
        raise ValueError("Import preview exceeds 1 MiB; partition records")
    return out


def preview_import(db, u, pid, b):
    open_period(db, pid)
    payload = import_payload(db, u, pid, b)
    sha = digest({"provider": b.provider, "period": pid, "records": payload})
    items = []
    for x in payload:
        existing = db.scalar(
            select(Entry).where(
                Entry.period_id == pid,
                Entry.source_type == b.provider,
                Entry.external_key == x["external_key"],
                Entry.basis == x["basis"],
            )
        )
        if existing:
            if existing.dimensions != x["dimensions_snapshot"] or any(
                (
                    D(getattr(existing, k)) != D(x[k])
                    if k == "amount"
                    else str(getattr(existing, k)) != str(x[k])
                )
                for k in ["scope_id", "kind", "category", "basis", "amount", "evidence"]
            ):
                raise ValueError(
                    "Source fact conflict; reverse and reconcile explicitly"
                )
        items.append({"action": "already_imported" if existing else "create", **x})
    r = ImportBatch(
        period_id=pid,
        provider=b.provider,
        digest=sha,
        payload={"request": b.model_dump(mode="json"), "records": payload},
        report={"items": items, "dry_run": True, "external_calls": 0},
        created_by=u.id,
    )
    db.add(r)
    db.flush()
    return view(r)


def approve_import(db, u, id, b):
    r = get(db, ImportBatch, id)
    open_period(db, r.period_id)
    if r.digest != b.digest:
        raise ValueError("Wrong preview digest")
    if r.approved:
        return view(r)
    req = ImportInput.model_validate(r.payload["request"])
    fresh = import_payload(db, u, r.period_id, req)
    if (
        digest({"provider": r.provider, "period": r.period_id, "records": fresh})
        != r.digest
    ):
        raise ValueError("Source financial facts changed; repreview")
    for x in fresh:
        existing = db.scalar(
            select(Entry).where(
                Entry.period_id == r.period_id,
                Entry.source_type == r.provider,
                Entry.external_key == x["external_key"],
                Entry.basis == x["basis"],
            )
        )
        if existing:
            if existing.dimensions != x["dimensions_snapshot"] or any(
                (
                    D(getattr(existing, k)) != D(x[k])
                    if k == "amount"
                    else str(getattr(existing, k)) != str(x[k])
                )
                for k in ["scope_id", "kind", "category", "basis", "amount", "evidence"]
            ):
                raise ValueError("Existing financial fact conflicts; no partial import")
            continue
        add_entry(
            db,
            u,
            r.period_id,
            EntryInput(
                operation_key=b.operation_key,
                reason=b.reason,
                **{k: v for k, v in x.items() if k != "dimensions_snapshot"},
            ),
            r.provider,
        )
    r.approved = True
    db.flush()
    return view(r)


def scenario(db, u, pid, b):
    open_period(db, pid)
    baseline = calculate(db, pid, b.scope_id, b.revenue_basis, b.expense_basis)
    inputs = b.model_dump(mode="json", exclude={"operation_key", "reason"})
    a = Assumption(
        period_id=pid,
        scope_id=b.scope_id,
        name=b.name,
        inputs=inputs,
        evidence=b.evidence,
        created_by=u.id,
    )
    db.add(a)
    db.flush()
    delta = b.monthly_revenue_delta - b.monthly_opex_delta
    days = (get(db, Period, pid).ends_on - get(db, Period, pid).starts_on).days
    months = D(days) * 12 / D("365.2425")
    base = (
        (D(baseline["totals"]["recurring_revenue"]) - D(baseline["totals"]["opex"]))
        / months
        if baseline["complete"]
        else None
    )
    result = {
        "advisory_only": True,
        "baseline_complete": baseline["complete"],
        "monthly_baseline_contribution": (
            str(base.quantize(CENT)) if base is not None else None
        ),
        "monthly_proposed_contribution": (
            str((base + delta).quantize(CENT)) if base is not None else None
        ),
        "incremental_monthly_contribution": str(delta),
        "incremental_payback_months": (
            str((b.capex / delta).quantize(CENT)) if delta > 0 and b.capex > 0 else None
        ),
        "sensitivity": [],
        "uncertainty": "Operator assumptions; no customer growth, prices, purchases or retirements executed",
    }
    for percent in [-b.sensitivity_percent, 0, b.sensitivity_percent]:
        d = b.monthly_revenue_delta * (1 + D(percent) / 100) - b.monthly_opex_delta
        result["sensitivity"].append(
            {
                "revenue_delta_change_percent": percent,
                "monthly_increment": str(d.quantize(CENT)),
                "payback_months": (
                    str((b.capex / d).quantize(CENT)) if b.capex > 0 and d > 0 else None
                ),
            }
        )
    x = Scenario(
        assumption_id=a.id,
        name=b.name,
        baseline_digest=digest(baseline),
        baseline=baseline,
        result=result,
        created_by=u.id,
    )
    db.add(x)
    db.flush()
    return view(x)


def retirement(db, u, pid, b):
    sc = get(db, Scope, b.scope_id)
    if sc.kind != "tower":
        raise ValueError("Existing tower scope required")
    report = calculate(db, pid, sc.id, b.revenue_basis, b.expense_basis)
    services = active_services(db, sc)
    links = list(
        db.scalars(
            select(NetworkLink).where(
                or_(
                    NetworkLink.a_site_id == sc.site_id,
                    NetworkLink.z_site_id == sc.site_id,
                )
            )
        )
    )
    from app.unified import service as unified

    nodes = list(db.scalars(select(Node).where(Node.site_id == sc.site_id)))
    operational_impact = (
        unified.impact(db, failed_nodes=[n.id for n in nodes])
        if nodes
        else {"uncertainty": "No canonical unified tower node documented"}
    )
    return {
        "id": sc.id,
        "financial_report": report,
        "active_service_ids": [x.id for x in services],
        "active_customer_count": len({x.customer_id for x in services}),
        "documented_backhaul_dependencies": [
            {
                "id": l.id,
                "type": l.link_type,
                "a_site_id": l.a_site_id,
                "z_site_id": l.z_site_id,
            }
            for l in links
        ],
        "removal_cost": str(b.removal_cost) if b.removal_cost is not None else None,
        "contractual_obligations": (
            str(b.contractual_obligations)
            if b.contractual_obligations is not None
            else None
        ),
        "alternate_service_evidence": b.alternate_service_evidence,
        "unified_topology_impact_preview": operational_impact,
        "decision": "qualified_operator_review_required",
        "automatic_retirement": False,
        "uncertainty": "Current native customer and link records; alternate paths, shared savings, contracts and engineering must be verified; no topology changed",
    }


def csv_report(report):
    f = io.StringIO()
    w = csv.writer(f)
    w.writerow(
        [
            "period",
            "scope",
            "category",
            "kind",
            "basis",
            "amount",
            "allocation",
            "source",
            "evidence",
        ]
    )

    def safe(v):
        s = str(v)
        return (
            "'" + s
            if s.lstrip().startswith(("=", "+", "-", "@"))
            or any(c in s for c in ["\t", "\r", "\n"])
            else s
        )

    for l in report["lines"]:
        w.writerow(
            [
                safe(x)
                for x in [
                    report["period"]["code"],
                    report["scope"]["code"],
                    l["category"],
                    l["kind"],
                    l["basis"],
                    l["amount"],
                    l["allocation_method"],
                    l["source_type"],
                    l["evidence"],
                ]
            ]
        )
    return f.getvalue()
