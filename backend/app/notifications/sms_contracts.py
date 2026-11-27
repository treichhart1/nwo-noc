from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID
import re
from pydantic import Field, SecretStr, model_validator
from app.customers.contracts import SafeModel, e164

ProviderName = Literal["twilio", "telnyx"]
CATEGORIES = {
    "outage",
    "update",
    "extended",
    "maintenance",
    "rescheduled",
    "cancelled",
    "restoration",
    "signup_application",
    "installation_scheduling",
    "customer_cancellation",
}


class ProviderInput(SafeModel):
    sender: str = Field(max_length=20)
    approved_senders: list[str] = Field(min_length=1, max_length=20)
    account_sid: str = Field(min_length=1, max_length=80)
    service_id: str = Field(min_length=1, max_length=80)
    callback_url: str = Field(max_length=255)
    callbacks_enabled: bool = False
    registration_reference: str = Field(
        min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.:/ -]+$"
    )
    recipient_allowlist: list[str] = Field(default_factory=list, max_length=1000)
    categories: list[str] = Field(
        default_factory=lambda: sorted(CATEGORIES - {"customer_cancellation"}), max_length=10
    )
    currency: Literal["USD"] = "USD"
    price_per_segment_microunits: int | None = Field(None, ge=1, le=100000000)
    daily_limit_microunits: int = Field(0, ge=0, le=100000000000)
    monthly_limit_microunits: int = Field(0, ge=0, le=1000000000000)
    enabled: bool = False

    @model_validator(mode="after")
    def valid(self):
        if (
            not e164(self.sender)
            or self.sender not in self.approved_senders
            or any(
                not e164(x) for x in self.approved_senders + self.recipient_allowlist
            )
        ):
            raise ValueError("Validated sender and recipient numbers required")
        u = urlsplit(self.callback_url)
        if (
            u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
            or u.port not in {None, 443}
        ):
            raise ValueError("Exact HTTPS callback URL required")
        if (
            not self.categories
            or not set(self.categories) <= CATEGORIES
            or len(set(self.categories)) != len(self.categories)
        ):
            raise ValueError("Explicit supported message categories required")
        return self

    def provider_valid(self, name):
        if urlsplit(self.callback_url).path != "/callbacks/sms/" + name:
            raise ValueError("Use the isolated provider callback route")
        if name == "twilio":
            if not re.fullmatch(
                r"AC[a-fA-F0-9]{32}", self.account_sid
            ) or not re.fullmatch(r"MG[a-fA-F0-9]{32}", self.service_id):
                raise ValueError(
                    "Validated Twilio account and Messaging Service SIDs required"
                )
        else:
            try:
                if (
                    str(UUID(self.account_sid)) != self.account_sid
                    or str(UUID(self.service_id)) != self.service_id
                ):
                    raise ValueError()
            except ValueError:
                raise ValueError(
                    "Validated Telnyx organization/profile UUIDs required"
                ) from None
        return self


class RoutingInput(SafeModel):
    primary: Literal["mock", "twilio", "telnyx"] = "mock"
    secondary: ProviderName | None = None
    failover: bool = False

    @model_validator(mode="after")
    def valid(self):
        if self.secondary == self.primary or (
            self.failover and (not self.secondary or self.primary == "mock")
        ):
            raise ValueError("Failover requires two distinct native providers")
        return self


class CredentialsInput(SafeModel):
    # JSON with only the documented provider-specific keys. Always write-only.
    value: SecretStr = Field(max_length=4096)


class AuthorizationInput(SafeModel):
    confirm: bool
    reference: str = Field(
        min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.:/ -]+$"
    )
    fingerprint: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")


class ReconcileInput(AuthorizationInput):
    provider_message_id: str | None = Field(None, max_length=100)
