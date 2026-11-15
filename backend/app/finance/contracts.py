from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field, model_validator
from app.field.contracts import Mutation
from app.customers.contracts import SafeModel

Money = __import__("typing").Annotated[
    Decimal,
    Field(ge=0, le=Decimal("999999999999.99"), decimal_places=2, allow_inf_nan=False),
]
Weight = __import__("typing").Annotated[
    Decimal,
    Field(gt=0, le=Decimal("1000000000000"), decimal_places=8, allow_inf_nan=False),
]
RevenueBasis = Literal["contracted", "invoiced", "collected", "estimated"]
ExpenseBasis = Literal["actual", "estimated"]
OPEX = {
    "lease",
    "power",
    "backhaul",
    "upstream",
    "license",
    "equipment_support",
    "maintenance",
    "insurance",
    "software",
    "other",
}


class PeriodInput(Mutation):
    code: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    starts_on: date
    ends_on: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def dates(self):
        if not 1 <= (self.ends_on - self.starts_on).days <= 366:
            raise ValueError("Period must span 1–366 days; end exclusive")
        return self


class ScopeInput(Mutation):
    code: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    name: str = Field(min_length=1, max_length=160)
    kind: Literal[
        "network",
        "tower",
        "sector",
        "backhaul",
        "edge",
        "circuit",
        "service",
        "plan",
        "asset",
        "group",
    ]
    reference_id: int | None = Field(None, ge=1)
    service_ids: list[int] = Field(default_factory=list, max_length=1000)


class EntryInput(Mutation):
    scope_id: int = Field(ge=1)
    kind: Literal["revenue", "opex", "capex", "depreciation", "replacement_estimate"]
    category: str = Field(min_length=1, max_length=40)
    basis: Literal[
        "contracted", "invoiced", "collected", "estimated", "actual", "assumption"
    ]
    amount: Money
    verified_collection: bool = False
    external_key: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:/-]+$"
    )
    evidence: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def kinds(self):
        cats = {
            "revenue": {"subscription", "transport", "installation"},
            "opex": OPEX,
            "capex": {"equipment", "installation"},
            "depreciation": {"depreciation"},
            "replacement_estimate": {"replacement"},
        }
        bases = {
            "revenue": {"contracted", "invoiced", "collected", "estimated"},
            "opex": {"actual", "estimated"},
            "capex": {"actual", "estimated"},
            "depreciation": {"assumption"},
            "replacement_estimate": {"estimated"},
        }
        if self.basis == "collected" and not self.verified_collection:
            raise ValueError(
                "Verified collection evidence and acknowledgement required"
            )
        if self.category not in cats[self.kind] or self.basis not in bases[self.kind]:
            raise ValueError(
                "Unsupported operational financial category/basis; construction budgets excluded"
            )
        return self


class Target(SafeModel):
    scope_id: int = Field(ge=1)
    weight: Weight | None = None


class AllocationInput(Mutation):
    method: Literal[
        "equal", "customer_count", "revenue_weighted", "capacity_weighted", "manual"
    ]
    targets: list[Target] = Field(min_length=1, max_length=100)
    revenue_basis: RevenueBasis = "contracted"

    @model_validator(mode="after")
    def unique(self):
        if len({x.scope_id for x in self.targets}) != len(self.targets):
            raise ValueError("Duplicate allocation destination")
        if self.method in {"manual", "capacity_weighted"} and any(
            x.weight is None for x in self.targets
        ):
            raise ValueError("Documented explicit weights required")
        if self.method not in {"manual", "capacity_weighted"} and any(
            x.weight is not None for x in self.targets
        ):
            raise ValueError("Method calculates its own weights")
        return self


class CoverageInput(Mutation):
    scope_id: int = Field(ge=1)
    revenue_basis: RevenueBasis
    expense_basis: ExpenseBasis
    categories: list[
        Literal[
            "revenue",
            "capital",
            "depreciation",
            "lease",
            "power",
            "backhaul",
            "upstream",
            "license",
            "equipment_support",
            "maintenance",
            "insurance",
            "software",
            "other",
        ]
    ] = Field(max_length=13)
    evidence: str = Field(min_length=10, max_length=1000)


class RecurringInput(Mutation):
    scope_id: int = Field(ge=1)
    code: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    category: Literal[
        "lease",
        "power",
        "backhaul",
        "upstream",
        "license",
        "equipment_support",
        "maintenance",
        "insurance",
        "software",
        "other",
    ]
    amount: Money
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    basis: ExpenseBasis
    starts_on: date
    ends_on: date | None = None
    evidence: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def dates(self):
        if self.ends_on and self.ends_on <= self.starts_on:
            raise ValueError("Invalid recurring interval")
        return self


class MockCharge(SafeModel):
    external_key: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,160}$")
    service_external_id: str = Field(min_length=1, max_length=120)
    amount: Money
    basis: RevenueBasis
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    category: Literal["subscription", "transport", "installation"] = "subscription"
    evidence: str = Field(min_length=10, max_length=1000)
    verified_collection: bool = False


class ImportInput(Mutation):
    provider: Literal["powercode_mock", "inventory"]
    charges: list[MockCharge] = Field(default_factory=list, max_length=1000)
    asset_ids: list[int] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def one(self):
        if self.provider == "powercode_mock" and (not self.charges or self.asset_ids):
            raise ValueError("Mock charge records required")
        if self.provider == "inventory" and (not self.asset_ids or self.charges):
            raise ValueError("Explicit existing asset selection required")
        return self


class ApprovalInput(Mutation):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ScenarioInput(Mutation):
    scope_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    revenue_basis: RevenueBasis = "contracted"
    expense_basis: ExpenseBasis = "actual"
    monthly_revenue_delta: Decimal = Field(
        ge=-1000000000, le=1000000000, decimal_places=2, allow_inf_nan=False
    )
    monthly_opex_delta: Decimal = Field(
        ge=-1000000000, le=1000000000, decimal_places=2, allow_inf_nan=False
    )
    capex: Money
    additional_customers: int = Field(ge=-100000, le=100000, default=0)
    sensitivity_percent: int = Field(ge=0, le=100, default=10)
    evidence: str = Field(min_length=10, max_length=1000)


class RetirementInput(Mutation):
    scope_id: int = Field(ge=1)
    revenue_basis: RevenueBasis = "contracted"
    expense_basis: ExpenseBasis = "actual"
    removal_cost: Money | None = None
    contractual_obligations: Money | None = None
    alternate_service_evidence: str | None = Field(None, min_length=10, max_length=1000)


class ReversalInput(Mutation):
    adjustment_period_id: int | None = Field(None, ge=1)


class ContractInput(Mutation):
    scope_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    starts_on: date
    ends_on: date
    evidence: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def term(self):
        if self.ends_on <= self.starts_on:
            raise ValueError("Contract end must follow start")
        return self


class PolicyInput(Mutation):
    scope_id: int = Field(ge=1)
    maintenance_share_percent: int = Field(25, ge=0, le=100)
    backhaul_revenue_percent: int = Field(30, ge=0, le=100)
    low_margin_percent: int = Field(20, ge=0, le=100)
    low_utilization_percent: int = Field(10, ge=0, le=100)
    high_utilization_percent: int = Field(85, ge=0, le=100)
    evidence: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def thresholds(self):
        if self.low_utilization_percent >= self.high_utilization_percent:
            raise ValueError("Utilization thresholds must be ordered")
        return self
