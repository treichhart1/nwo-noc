from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID
from typing import Literal, Annotated
import re, unicodedata
from pydantic import (
    Field,
    EmailStr,
    field_validator,
    model_validator,
    StringConstraints,
)
from app.customers.contracts import SafeModel, e164
from app.field.contracts import Mutation
from app.equipment.contracts import AttachmentInput
from app.core.operator_text import clean_operator_text

Money = Annotated[
    Decimal, Field(ge=0, le=Decimal("10000000"), decimal_places=2, allow_inf_nan=False)
]
Short = Annotated[str, StringConstraints(min_length=1, max_length=200)]
STATES = [
    "submitted",
    "under_review",
    "qualification_pending",
    "site_survey_required",
    "survey_scheduled",
    "qualified",
    "quote_pending",
    "quote_sent",
    "customer_accepted",
    "installation_pending",
    "installation_scheduled",
    "installed",
    "activation_pending",
    "completed",
    "rejected",
    "cancelled",
]


class Address(SafeModel):
    street: str = Field(min_length=3, max_length=200)
    unit: str = Field("", max_length=80)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(pattern=r"^[A-Za-z]{2}$")
    postal_code: str = Field(pattern=r"^\d{5}(-\d{4})?$")
    country: Literal["US"] = "US"

    @field_validator("street", "unit", "city", "state", "postal_code", mode="before")
    @classmethod
    def clean(cls, v):
        if not isinstance(v, str):
            return v
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", v).strip())


class SignupInput(SafeModel):
    submission_key: UUID
    audience: Literal["residential", "business"]
    request_kind: Literal["new", "upgrade", "additional_location"] = "new"
    customer_name: str = Field(min_length=2, max_length=200)
    first_name: str = Field("", max_length=100)
    last_name: str = Field("", max_length=100)
    business_name: str | None = Field(None, max_length=200)
    service_address: Address
    billing_address: Address | None = None
    email: EmailStr
    phone: str = Field(max_length=40)
    preferred_contact: Literal["email", "phone", "sms", "none"]
    consent_email: bool = False
    consent_phone: bool = False
    consent_sms: bool = False
    privacy_consent: bool
    technology: Literal["wireless", "fiber", "hybrid", "lte", "5g"]
    plan_id: int = Field(ge=1)
    installation_preferences: str = Field("", max_length=1000)
    notes: str = Field("", max_length=2000)
    website: str = Field("", max_length=200)
    turnstile_token: str = Field(min_length=1, max_length=2048)

    @field_validator("customer_name", "business_name", "first_name", "last_name", mode="before")
    @classmethod
    def applicant_name(cls, v):
        return (
            re.sub(r"\s+", " ", unicodedata.normalize("NFKC", v).strip())
            if isinstance(v, str)
            else v
        )

    @field_validator("phone")
    @classmethod
    def phone_number(cls, v):
        result = e164(v)
        if not result:
            raise ValueError("International E.164 phone required")
        return result

    @model_validator(mode="after")
    def validate_request(self):
        if bool(self.first_name) != bool(self.last_name):
            raise ValueError("Both first and last names required when provided")
        if self.first_name and self.customer_name != (self.first_name + " " + self.last_name).strip():
            raise ValueError("Combined name must match first and last name")
        if self.audience == "business" and not self.business_name:
            raise ValueError("Business name required")
        if not self.privacy_consent:
            raise ValueError("Privacy consent required")
        if self.preferred_contact != "none" and not getattr(
            self, "consent_" + self.preferred_contact
        ):
            raise ValueError("Explicit selected-channel consent required")
        if re.search(r"\bP\.?O\.?\s+BOX\b", self.service_address.street, re.I):
            raise ValueError("Physical service location required")
        for v in [
            self.customer_name,
            self.business_name,
            self.notes,
            self.installation_preferences,
        ]:
            clean_operator_text(v)
            if v and (
                re.search(r"\b\d{3}-\d{2}-\d{4}\b", v)
                or re.search(
                    r"(?i)(ssn|social security|passport|cvv|card number|driver.?s? license)\s*[:=]",
                    v,
                )
                or re.search(r"\b(?:\d[ -]?){13,19}\b", v)
            ):
                raise ValueError("Do not provide identity/payment information")
        return self


class LookupInput(SafeModel):
    token: str = Field(min_length=32, max_length=200)
    turnstile_token: str = Field(min_length=1, max_length=2048)


class PlanInput(Mutation):
    name: str = Field(min_length=3, max_length=160)
    audience: Literal["residential", "business", "both"]
    technology: Literal["wireless", "fiber", "hybrid", "lte", "5g"]
    active: bool = True
    public: bool = False
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    monthly: Money | None = None
    installation: Money | None = None
    download_mbps: (
        Annotated[Decimal, Field(gt=0, le=1000000, allow_inf_nan=False)] | None
    ) = None
    upload_mbps: (
        Annotated[Decimal, Field(gt=0, le=1000000, allow_inf_nan=False)] | None
    ) = None
    promotion: str | None = Field(None, max_length=500)
    eligibility: str = Field(
        "Subject to approved qualification", min_length=3, max_length=1000
    )
    equipment: list[Short] = Field(default_factory=list, max_length=30)
    powercode_plan_ref: str | None = Field(None, max_length=120)

    @model_validator(mode="after")
    def price(self):
        if (
            self.monthly is not None or self.installation is not None
        ) and not self.currency:
            raise ValueError("Explicit currency required")
        return self


class PlanUpdate(PlanInput):
    expected_revision: int = Field(ge=1)


class AreaInput(Mutation):
    name: str = Field(min_length=3, max_length=160)
    technology: Literal["wireless", "fiber", "hybrid", "lte", "5g", "service_boundary"]
    status: Literal["active", "planned"]
    polygon: list[list[float]] = Field(min_length=4, max_length=500)
    site_id: int | None = Field(None, ge=1)
    sector_id: int | None = Field(None, ge=1)
    fiber_link_id: int | None = Field(None, ge=1)
    evidence: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def valid_polygon(self):
        if self.polygon[0] != self.polygon[-1] or any(
            len(p) != 2 or not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90)
            for p in self.polygon
        ):
            raise ValueError("Closed finite WGS84 lon/lat polygon required")
        points = self.polygon
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:]))
        if abs(area) < 1e-12:
            raise ValueError("Nonzero polygon area required")

        def cross(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

        for i, (a, b) in enumerate(zip(points, points[1:])):
            if a == b:
                raise ValueError("Repeated polygon edge vertex")
            for j in range(i + 2, len(points) - 1):
                if i == 0 and j == len(points) - 2:
                    continue
                c, d = points[j], points[j + 1]
                if (
                    cross(a, b, c) * cross(a, b, d) <= 0
                    and cross(c, d, a) * cross(c, d, b) <= 0
                    and max(min(a[0], b[0]), min(c[0], d[0]))
                    <= min(max(a[0], b[0]), max(c[0], d[0]))
                    and max(min(a[1], b[1]), min(c[1], d[1]))
                    <= min(max(a[1], b[1]), max(c[1], d[1]))
                ):
                    raise ValueError("Simple nonintersecting polygon required")
        return self


class Update(Mutation):
    expected_revision: int = Field(ge=1)
    state: Literal[
        "submitted",
        "under_review",
        "qualification_pending",
        "site_survey_required",
        "survey_scheduled",
        "qualified",
        "quote_pending",
        "quote_sent",
        "customer_accepted",
        "installation_pending",
        "installation_scheduled",
        "installed",
        "activation_pending",
        "completed",
        "rejected",
        "cancelled",
    ]
    assigned_to: int | None = Field(None, ge=1)
    proposed_site_id: int | None = Field(None, ge=1)
    evidence: str = Field(min_length=10, max_length=2000)
    approved_qualification: bool = False
    acknowledged_no_provisioning: bool = False


class QualifyInput(Mutation):
    expected_revision: int = Field(ge=1)
    latitude: float | None = Field(None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(None, ge=-180, le=180, allow_inf_nan=False)
    coordinate_source: str = Field("Unavailable", max_length=300)
    use_provider: bool = False
    consent_address_transfer: bool = False

    @model_validator(mode="after")
    def coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Paired coordinates required")
        return self


class WorkInput(Mutation):
    expected_revision: int = Field(ge=1)
    kind: Literal["survey", "installation"]
    site_id: int = Field(ge=1)
    sector_id: int | None = Field(None, ge=1)
    fiber_link_id: int | None = Field(None, ge=1)
    technician_id: int = Field(ge=1)
    scheduled_at: datetime
    equipment: list[Short] = Field(default_factory=list, max_length=30)

    @field_validator("scheduled_at")
    @classmethod
    def aware(cls, v):
        if v.tzinfo is None:
            raise ValueError("Aware schedule required")
        return v


class SurveyInput(Mutation):
    expected_revision: int = Field(ge=1)
    app_work_id: int = Field(ge=1)
    surveyed_at: datetime
    latitude: float | None = Field(None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(None, ge=-180, le=180, allow_inf_nan=False)
    proposed_site_id: int = Field(ge=1)
    proposed_sector_id: int | None = Field(None, ge=1)
    technology: Literal["wireless", "fiber", "hybrid", "lte", "5g"]
    line_of_sight: str = Field(min_length=3, max_length=1000)
    signal_observation: str = Field(min_length=3, max_length=1000)
    equipment: list[Short] = Field(default_factory=list, max_length=30)
    mounting: str = Field(min_length=3, max_length=1000)
    cable_route: str = Field(min_length=3, max_length=1000)
    complexity: str = Field(min_length=3, max_length=1000)
    outcome: Literal["qualified_preliminary", "not_qualified", "inconclusive"]
    photo_ids: list[int] = Field(default_factory=list, max_length=20)

    @field_validator("surveyed_at")
    @classmethod
    def time(cls, v):
        if v.tzinfo is None or v > datetime.now(timezone.utc):
            raise ValueError("Nonfuture aware survey time required")
        return v


class QuoteInput(Mutation):
    expected_revision: int = Field(ge=1)
    plan_id: int = Field(ge=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    monthly: Money | None = None
    installation: Money | None = None
    equipment: Money | None = None
    construction: Money | None = None
    discount: Money = Decimal("0.00")
    optional_services: list[dict] = Field(default_factory=list, max_length=20)
    expires_at: datetime

    @model_validator(mode="after")
    def valid(self):
        if self.expires_at.tzinfo is None or self.expires_at <= datetime.now(
            timezone.utc
        ):
            raise ValueError("Future aware expiration required")
        for item in self.optional_services:
            if (
                set(item) != {"description", "amount"}
                or not isinstance(item["description"], str)
                or not 1 <= len(item["description"]) <= 200
            ):
                raise ValueError("Bounded optional charge required")
            try:
                amount = Decimal(str(item["amount"]))
            except (InvalidOperation, ValueError, TypeError):
                raise ValueError("Valid optional charge required") from None
            if (
                not amount.is_finite()
                or amount < 0
                or amount > 10000000
                or amount.as_tuple().exponent < -2
            ):
                raise ValueError("Valid exact optional charge required")
            item["amount"] = str(amount)
        return self


class Control(Mutation):
    expected_revision: int = Field(ge=1)
    action: Literal[
        "approve_quote",
        "issue_quote",
        "record_acceptance",
        "record_opt_out",
        "approve_match",
        "prepare_provisioning",
        "hold",
        "release_hold",
        "redact",
    ]
    quote_id: int | None = Field(None, ge=1)
    customer_id: int | None = Field(None, ge=1)
    evidence: str = Field(min_length=10, max_length=2000)


class NotifyInput(Mutation):
    expected_revision: int = Field(ge=1)
    template: Literal[
        "application_received",
        "information_required",
        "survey_scheduling",
        "quote_available",
        "installation_scheduling",
        "status_update",
        "installation_reminder",
        "appointment_rescheduled",
        "installation_completed",
    ]
    channel: Literal["email", "sms", "phone"]


class PhotoInput(AttachmentInput):
    operation_key: UUID
    app_work_id: int = Field(ge=1)


class QuoteAccessInput(Mutation):
    expected_revision: int = Field(ge=1)
    quote_id: int = Field(ge=1)


class PublicQuoteInput(SafeModel):
    token: str = Field(min_length=48, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    request_key: UUID
    turnstile_token: str = Field(min_length=1, max_length=2048)


class PublicQuoteResponse(PublicQuoteInput):
    decision: Literal["accepted", "rejected"]
    acknowledge: Literal[True]


class InstallAssetInput(Mutation):
    expected_revision: int = Field(ge=1)
    work_order_id: int = Field(ge=1)
    asset_id: int = Field(ge=1)
    asset_revision: int = Field(ge=1)
    customer_location_id: int = Field(ge=1)
