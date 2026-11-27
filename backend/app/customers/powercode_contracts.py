"""Provider-independent NOC130 contracts. No native Powercode URL/field claims."""

import ipaddress
import re
from decimal import Decimal
from urllib.parse import urlsplit
from typing import Literal
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic import BaseModel, ConfigDict
from datetime import datetime, timezone


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


STATES = [
    "pending_review",
    "approved",
    "queued",
    "submitted",
    "account_created",
    "service_assigned",
    "activation_pending",
    "completed",
    "failed",
    "reconciliation_required",
    "cancelled",
]
NATIVE_BLOCKER = "Native Powercode version, operation schemas, permissions and acceptance/idempotency semantics require verification"


def validate_origin(value):
    if value is None:
        return None
    try:
        p = urlsplit(value)
        if (
            p.scheme != "https"
            or not p.hostname
            or p.username
            or p.password
            or p.query
            or p.fragment
            or p.path not in ("", "/")
            or p.port not in (None, 443, 444)
            or any(c.isspace() for c in value)
            or "\\" in value
            or "%" in value
        ):
            raise ValueError()
        # Private routed deployments are legitimate, but loopback/link-local are not.
        if p.hostname.lower() in ("localhost", "metadata.google.internal"):
            raise ValueError()
        try:
            ip = ipaddress.ip_address(p.hostname)
        except ValueError:
            if not re.fullmatch(
                r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", p.hostname
            ):
                raise ValueError()
        else:
            if (
                ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise ValueError()
        return value.rstrip("/")
    except (ValueError, TypeError):
        raise ValueError(
            "Approved HTTPS origin without credentials, paths or redirects required"
        ) from None


class ConnectionInput(SafeModel):
    endpoint: str | None = Field(None, max_length=500)
    allowed_origin: str | None = Field(None, max_length=500)
    auth_method: Literal[
        "unverified", "documented_api_key_pending", "documented_basic_pending"
    ] = "unverified"
    emergency_disabled: bool = Field(True, strict=True)
    expected_revision: int = Field(0, ge=0)
    _endpoint = field_validator("endpoint", "allowed_origin")(validate_origin)

    @model_validator(mode="after")
    def same_origin(self):
        if self.endpoint != self.allowed_origin:
            raise ValueError("Exact independently approved origin required")
        return self


class CredentialInput(SafeModel):
    value: SecretStr = Field(min_length=1, max_length=3000)


class PackageRecord(SafeModel):
    external_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    technology: Literal["wireless", "fiber", "hybrid", "lte", "5g"]
    audience: Literal["residential", "business", "both"]
    available: bool = Field(strict=True)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    monthly: Decimal | None = Field(
        None, ge=0, le=9999999999, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    installation: Decimal | None = Field(
        None, ge=0, le=9999999999, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    download_mbps: Decimal | None = Field(
        None, ge=0, le=1000000, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    upload_mbps: Decimal | None = Field(
        None, ge=0, le=1000000, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value > datetime.now(timezone.utc):
            raise ValueError("Non-future timezone-aware timestamp required")
        return value

    @model_validator(mode="after")
    def prices(self):
        if (
            self.monthly is not None or self.installation is not None
        ) and not self.currency:
            raise ValueError("Currency required for monetary observations")
        return self


class MappingInput(SafeModel):
    plan_id: int = Field(ge=1)
    package_id: str = Field(min_length=1, max_length=120)
    expected_plan_revision: int = Field(ge=1)
    package_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_differences: bool = Field(strict=True)


class ProvisionInput(SafeModel):
    application_id: int = Field(ge=1)
    expected_application_revision: int = Field(ge=1)
    identity_verified: bool = Field(strict=True)
    address_reviewed: bool = Field(strict=True)
    qualification_reviewed: bool = Field(strict=True)
    duplicate_reviewed: bool = Field(strict=True)
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,120}$")


class DecisionInput(SafeModel):
    expected_revision: int = Field(ge=1)
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    authorize: bool = Field(strict=True)


class ActivationInput(DecisionInput):
    independently_authorized: bool = Field(strict=True)
    evidence_reference: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,120}$")


class ProvisionResult(SafeModel):
    outcome: Literal["accepted", "not_accepted", "ambiguous"]
    external_id: str | None = Field(None, min_length=1, max_length=120)
    error_code: (
        Literal[
            "authentication_failed", "rate_limited", "unavailable", "validation_failed"
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def accepted(self):
        if (self.outcome == "accepted") != (self.external_id is not None):
            raise ValueError(
                "Verified acceptance must include one stable external identifier"
            )
        return self
