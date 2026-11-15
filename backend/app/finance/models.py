from sqlalchemy import (
    Column as C,
    Integer as I,
    String as S,
    Boolean as B,
    Date as Date,
    DateTime as DT,
    Numeric as N,
    JSON,
    ForeignKey as FK,
    UniqueConstraint as U,
    CheckConstraint as CK,
    Index,
)
from app.core.database import Base
from app.field.models import now


class Period(Base):
    __tablename__ = "finance_periods"
    id = C(I, primary_key=True)
    code = C(S(80), unique=True, nullable=False)
    starts_on = C(Date, nullable=False)
    ends_on = C(Date, nullable=False)
    currency = C(S(3), nullable=False)
    closed = C(B, nullable=False, default=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)
    __table_args__ = (CK("ends_on>starts_on", name="ck_finance_period_dates"),)


class Scope(Base):
    __tablename__ = "finance_scopes"
    id = C(I, primary_key=True)
    code = C(S(100), unique=True, nullable=False)
    name = C(S(160), nullable=False)
    kind = C(S(30), nullable=False, index=True)
    site_id = C(I, FK("sites.id"), index=True)
    sector_id = C(I, FK("sectors.id"), index=True)
    link_id = C(I, FK("network_links.id"))
    edge_id = C(I, FK("unified_edges.id"))
    circuit_id = C(I, FK("unified_circuits.id"))
    service_id = C(I, FK("customer_services.id"))
    plan_id = C(I, FK("signup_plans.id"))
    asset_id = C(I, FK("equipment_assets.id"))
    context = C(JSON, nullable=False, default=dict)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)
    __table_args__ = (
        CK(
            "kind IN ('network','tower','sector','backhaul','edge','circuit','service','plan','asset','group')",
            name="ck_finance_scope_kind",
        ),
        U(
            "kind",
            "site_id",
            "sector_id",
            "link_id",
            "edge_id",
            "circuit_id",
            "service_id",
            "plan_id",
            "asset_id",
            name="uq_finance_scope_context",
        ),
    )


class GroupMember(Base):
    __tablename__ = "finance_group_members"
    scope_id = C(I, FK("finance_scopes.id"), primary_key=True)
    service_id = C(I, FK("customer_services.id"), primary_key=True)


class Entry(Base):
    __tablename__ = "finance_entries"
    id = C(I, primary_key=True)
    period_id = C(I, FK("finance_periods.id"), nullable=False, index=True)
    scope_id = C(I, FK("finance_scopes.id"), nullable=False, index=True)
    kind = C(S(30), nullable=False)
    category = C(S(40), nullable=False)
    basis = C(S(30), nullable=False)
    amount = C(N(18, 2), nullable=False)
    verified_collection = C(B, nullable=False, default=False)
    source_type = C(S(30), nullable=False)
    external_key = C(S(160), nullable=False)
    evidence = C(S(1000), nullable=False)
    dimensions = C(JSON, nullable=False)
    reversal_of = C(I, FK("finance_entries.id"), unique=True)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)
    __table_args__ = (
        U(
            "period_id",
            "source_type",
            "external_key",
            "basis",
            name="uq_finance_source_fact",
        ),
        CK(
            "kind IN ('revenue','opex','capex','depreciation','replacement_estimate')",
            name="ck_finance_entry_kind",
        ),
        CK(
            "(reversal_of IS NULL AND amount>=0) OR (reversal_of IS NOT NULL AND amount<=0)",
            name="ck_finance_reversal_amount",
        ),
        Index("ix_finance_period_basis_kind", "period_id", "basis", "kind"),
    )


class Allocation(Base):
    __tablename__ = "finance_allocations"
    id = C(I, primary_key=True)
    entry_id = C(I, FK("finance_entries.id"), nullable=False, unique=True)
    method = C(S(30), nullable=False)
    evidence = C(JSON, nullable=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)


class Share(Base):
    __tablename__ = "finance_shares"
    allocation_id = C(I, FK("finance_allocations.id"), primary_key=True)
    scope_id = C(I, FK("finance_scopes.id"), primary_key=True)
    weight = C(N(24, 8), nullable=False)
    amount = C(N(18, 2), nullable=False)
    dimensions = C(JSON, nullable=False)
    __table_args__ = (CK("weight>0", name="ck_finance_share_weight"),)


class Attestation(Base):
    __tablename__ = "finance_attestations"
    id = C(I, primary_key=True)
    period_id = C(I, FK("finance_periods.id"), nullable=False, index=True)
    scope_id = C(I, FK("finance_scopes.id"), nullable=False, index=True)
    revenue_basis = C(S(30), nullable=False)
    expense_basis = C(S(30), nullable=False)
    categories = C(JSON, nullable=False)
    evidence = C(S(1000), nullable=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)


class Recurring(Base):
    __tablename__ = "finance_recurring"
    id = C(I, primary_key=True)
    scope_id = C(I, FK("finance_scopes.id"), nullable=False)
    code = C(S(100), unique=True, nullable=False)
    category = C(S(40), nullable=False)
    amount = C(N(18, 2), nullable=False)
    currency = C(S(3), nullable=False)
    basis = C(S(30), nullable=False)
    starts_on = C(Date, nullable=False)
    ends_on = C(Date)
    evidence = C(S(1000), nullable=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)


class Assumption(Base):
    __tablename__ = "finance_assumptions"
    id = C(I, primary_key=True)
    period_id = C(I, FK("finance_periods.id"), nullable=False, index=True)
    scope_id = C(I, FK("finance_scopes.id"), nullable=False)
    name = C(S(160), nullable=False)
    inputs = C(JSON, nullable=False)
    evidence = C(S(1000), nullable=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)


class Scenario(Base):
    __tablename__ = "finance_scenarios"
    id = C(I, primary_key=True)
    assumption_id = C(I, FK("finance_assumptions.id"), nullable=False)
    name = C(S(160), nullable=False)
    baseline_digest = C(S(64), nullable=False)
    baseline = C(JSON, nullable=False)
    result = C(JSON, nullable=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)


class ImportBatch(Base):
    __tablename__ = "finance_imports"
    id = C(I, primary_key=True)
    period_id = C(I, FK("finance_periods.id"), nullable=False, index=True)
    provider = C(S(30), nullable=False)
    digest = C(S(64), nullable=False)
    payload = C(JSON, nullable=False)
    report = C(JSON, nullable=False)
    approved = C(B, nullable=False, default=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)


class Report(Base):
    __tablename__ = "finance_reports"
    id = C(I, primary_key=True)
    period_id = C(I, FK("finance_periods.id"), nullable=False, index=True)
    scope_id = C(I, FK("finance_scopes.id"), nullable=False)
    digest = C(S(64), nullable=False, unique=True)
    result = C(JSON, nullable=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), default=now, nullable=False)
