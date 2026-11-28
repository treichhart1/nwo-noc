from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import Field, EmailStr, field_validator, model_validator
from app.customers.contracts import SafeModel

REASONS = {
    "switching_fiber": "Switching to fiber",
    "switching_starlink": "Switching to Starlink",
    "switching_cellular": "Switching to cellular home internet",
    "switching_cable": "Switching to cable internet",
    "price": "Price",
    "speed": "Internet speed",
    "reliability": "Reliability",
    "outages": "Network outages",
    "customer_service": "Customer service",
    "moving": "Moving",
    "financial_hardship": "Financial hardship",
    "seasonal": "Seasonal service",
    "nonpayment": "Nonpayment",
    "no_longer_needed": "No longer needs service",
    "other": "Other",
    "unknown": "Unknown",
}
STATES = {
    "pending_verification": {"verified", "withdrawn"},
    "verified": {"retention_review", "cancellation_confirmed", "withdrawn"},
    "retention_review": {"cancellation_confirmed", "withdrawn"},
    "cancellation_confirmed": {"awaiting_disconnection", "withdrawn"},
    "awaiting_disconnection": {"disconnected", "withdrawn"},
    "disconnected": {"closed"},
    "withdrawn": {"closed"},
    "closed": set(),
}
RETENTION = [
    "pending_review",
    "contact_attempted",
    "contacted",
    "technical_investigation",
    "speed_upgrade_offer",
    "package_change_offer",
    "pricing_adjustment_offer",
    "equipment_replacement_offer",
    "customer_accepted",
    "customer_declined",
    "customer_retained",
    "cancellation_confirmed",
]


class Replacement(SafeModel):
    provider: str = Field("", max_length=120)
    technology: Literal[
        "fiber", "satellite", "cellular", "cable", "wireless", "unknown"
    ] = "unknown"
    package: str = Field("", max_length=160)
    download_mbps: Decimal | None = Field(None, ge=0, le=1000000, allow_inf_nan=False)
    upload_mbps: Decimal | None = Field(None, ge=0, le=1000000, allow_inf_nan=False)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    monthly: Decimal | None = Field(
        None, ge=0, le=1000000, decimal_places=2, allow_inf_nan=False
    )
    promotional: Decimal | None = Field(
        None, ge=0, le=1000000, decimal_places=2, allow_inf_nan=False
    )
    promo_months: int | None = Field(None, ge=1, le=120)
    promo_expires: date | None = None
    installation: Decimal | None = Field(
        None, ge=0, le=1000000, decimal_places=2, allow_inf_nan=False
    )
    equipment: Decimal | None = Field(
        None, ge=0, le=1000000, decimal_places=2, allow_inf_nan=False
    )
    contract: str = Field("", max_length=500)

    @model_validator(mode="after")
    def money(self):
        if (
            any(
                getattr(self, k) is not None
                for k in ("monthly", "promotional", "installation", "equipment")
            )
            and not self.currency
        ):
            raise ValueError("Currency required")
        return self


class PublicInput(SafeModel):
    submission_key: UUID
    customer_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    phone: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    account_reference: str = Field("", max_length=120)
    service_address: str = Field(min_length=5, max_length=500)
    town: str = Field("", max_length=120)
    county: str = Field("", max_length=120)
    requested_date: date
    reason: str = Field(pattern=r"^[a-z0-9_]{1,50}$")
    comments: str = Field("", max_length=2000)
    replacement: Replacement = Field(default_factory=Replacement)
    equipment_ack: bool = Field(strict=True)
    policy_ack: bool = Field(strict=True)
    privacy_ack: bool = Field(strict=True)
    policy_id: int = Field(ge=1)
    consent_sms: bool = Field(False, strict=True)
    turnstile_token: str = Field(min_length=1, max_length=2048)
    website: str = Field("", max_length=100)

    @model_validator(mode="after")
    def valid(self):
        if not (self.equipment_ack and self.policy_ack and self.privacy_ack):
            raise ValueError("Acknowledgements required; no debt is established")
        if (
            not date.today()
            <= self.requested_date
            <= date.today() + timedelta(days=366)
        ):
            raise ValueError("Current or future bounded date required")
        return self


class Change(SafeModel):
    expected_revision: int = Field(ge=1)
    notes: str = Field(min_length=3, max_length=2000)


class Verification(Change):
    customer_id: int = Field(ge=1)
    service_ids: list[int] = Field(min_length=1, max_length=20)
    identity_verified: bool = Field(strict=True)
    address_verified: bool = Field(strict=True)
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,120}$")

    @field_validator("service_ids")
    @classmethod
    def unique(cls, v):
        if len(set(v)) != len(v) or any(n < 1 for n in v):
            raise ValueError("Distinct service IDs required")
        return v


class StatusChange(Change):
    status: str = Field(max_length=40)
    operator_reason: str | None = Field(None, max_length=50)
    billing_disposition: (
        Literal[
            "unreviewed",
            "refer_powercode",
            "reviewed_no_change",
            "refund_review",
            "charge_review",
        ]
        | None
    ) = None


class RetentionChange(Change):
    state: str = Field(max_length=40)
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,120}$")


class ReturnInput(Change):
    service_id: int = Field(ge=1)
    required: bool = Field(strict=True)
    due: date | None = None
    returned: bool | None = Field(None, strict=True)


class PolicyInput(SafeModel):
    text: str = Field(min_length=30, max_length=4000)
    retention_days: int = Field(365, ge=30, le=3650)
    unresolved_days: int = Field(7, ge=1, le=90)
    tower_churn_threshold: int = Field(3, ge=1, le=10000)


class ReasonInput(SafeModel):
    code: str = Field(pattern=r"^[a-z0-9_]{1,50}$")
    label: str = Field(min_length=2, max_length=120)
    voluntary: Literal["voluntary", "involuntary", "unknown"] = "unknown"
    active: bool = True


class CompetitorInput(SafeModel):
    name: str = Field(min_length=2, max_length=120)
    technology: Literal[
        "fiber", "satellite", "cellular", "cable", "wireless", "mixed", "unknown"
    ] = "unknown"
    service_areas: list[str] = Field(default_factory=list, max_length=100)
    active: bool = True

    @field_validator("service_areas")
    @classmethod
    def bounded(cls, v):
        if any(not x or len(x) > 120 for x in v):
            raise ValueError("Bounded approved public area labels required")
        return v


class ObservationInput(SafeModel):
    competitor_id: int = Field(ge=1)
    offer: Replacement
    source_kind: Literal["customer_reported", "operator_observed", "vendor_published"]
    source_reference: str = Field(pattern=r"^[A-Za-z0-9_.:/ -]{3,200}$")
    observed_on: date
    verified: bool = False

    @field_validator("observed_on")
    @classmethod
    def past(cls, v):
        if v > date.today():
            raise ValueError("Observation cannot be future")
        return v

    @model_validator(mode="after")
    def proof(self):
        if self.verified and self.source_kind == "customer_reported":
            raise ValueError("Customer reports are not independent verification")
        return self


class OfferInput(Change):
    kind: Literal[
        "technical_investigation",
        "speed_upgrade_offer",
        "package_change_offer",
        "pricing_adjustment_offer",
        "equipment_replacement_offer",
    ]
    terms: str = Field(min_length=5, max_length=2000)
    expires_at: datetime
    eligible: bool = Field(strict=True)
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,120}$")
    monthly_cost: Decimal | None = Field(
        None, ge=0, le=1000000, decimal_places=2, allow_inf_nan=False
    )
    one_time_cost: Decimal | None = Field(
        None, ge=0, le=1000000, decimal_places=2, allow_inf_nan=False
    )
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")

    @field_validator("expires_at")
    @classmethod
    def expiry(cls, v):
        if v.tzinfo is None or not datetime.now(timezone.utc) < v <= datetime.now(
            timezone.utc
        ) + timedelta(days=366):
            raise ValueError("Aware future bounded expiry required")
        return v

    @model_validator(mode="after")
    def money(self):
        if (
            self.monthly_cost is not None or self.one_time_cost is not None
        ) and not self.currency:
            raise ValueError("Currency required")
        return self


class OfferDecision(Change):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: Literal["approve", "accepted", "declined"]
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,120}$")


class CohortEvidence(SafeModel):
    monthly: Decimal | None = Field(
        None, ge=0, le=1000000, decimal_places=2, allow_inf_nan=False
    )
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    basis: Literal["contracted", "estimated"] = "estimated"
    started_on: date | None = None
    town: str | None = Field(None, max_length=120)
    county: str | None = Field(None, max_length=120)

    @model_validator(mode="after")
    def valid(self):
        if self.monthly is not None and not self.currency:
            raise ValueError("Explicit currency required")
        return self


class CohortInput(SafeModel):
    as_of: date
    complete: bool = Field(strict=True)
    service_ids: list[int] = Field(max_length=10000)
    evidence: dict[int, CohortEvidence] = Field(default_factory=dict, max_length=10000)
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,120}$")

    @model_validator(mode="after")
    def valid(self):
        if (
            self.as_of.day != 1
            or self.as_of > date.today()
            or len(set(self.service_ids)) != len(self.service_ids)
        ):
            raise ValueError("Distinct documented month-opening cohort required")
        if not set(self.evidence) <= set(self.service_ids) or any(
            v.started_on and v.started_on > self.as_of for v in self.evidence.values()
        ):
            raise ValueError(
                "Historical evidence must match selected services and cohort date"
            )
        return self


class CSVInput(SafeModel):
    csv_text: str = Field(min_length=1, max_length=500000)
    columns: dict[str, str] = Field(max_length=14)
    dry_run: bool = Field(True, strict=True)
    expected_digest: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")


class Communication(Change):
    template: Literal[
        "request_confirmation",
        "retention_contact",
        "equipment_return",
        "processing_update",
        "completion",
    ]


class Review(Change):
    competitor_id: int | None = Field(None, ge=1)
    legal_hold: bool | None = Field(None, strict=True)


class PurposeConsent(Change):
    granted: bool = Field(strict=True)
    phone: str | None = Field(None, pattern=r"^\+[1-9][0-9]{7,14}$")
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,120}$")
