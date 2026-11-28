"""Bounded email configuration; native delivery requires independent host approval."""

import re
from string import Formatter
from typing import Literal
from pydantic import Field, SecretStr, field_validator, model_validator
from app.customers.contracts import SafeModel

PURPOSES = {
    "operational",
    "maintenance",
    "signup",
    "installation",
    "cancellation",
    "retention",
}
PROVIDERS = {"mock", "sendgrid", "smtp"}
TEMPLATE_PURPOSE = {
    **{
        k: "signup"
        for k in [
            "signup_received",
            "signup_information",
            "survey_scheduling",
            "qualification_result",
            "quote_available",
        ]
    },
    **{
        k: "installation"
        for k in [
            "installation_scheduling",
            "installation_reminder",
            "appointment_rescheduling",
            "installation_completion",
            "activation_update",
        ]
    },
    **{
        k: "operational"
        for k in ["network_outage", "extended_outage", "verified_restoration"]
    },
    **{
        k: "maintenance"
        for k in [
            "planned_maintenance",
            "maintenance_rescheduling",
            "maintenance_restoration",
        ]
    },
    **{
        k: "cancellation"
        for k in [
            "cancellation_received",
            "verification_required",
            "equipment_return",
            "cancellation_update",
            "cancellation_completion",
        ]
    },
    **{k: "retention" for k in ["retention_review", "retention_offer"]},
}


def address(value):
    if (
        not isinstance(value, str)
        or len(value) > 254
        or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,63}",
            value,
        )
    ):
        raise ValueError("A single ASCII mailbox without display name required")
    local, domain = value.rsplit("@", 1)
    if (
        len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or ".." in domain
    ):
        raise ValueError("Invalid mailbox")
    return local + "@" + domain.lower()


class EmailConfig(SafeModel):
    enabled: bool = False
    sender: str
    sender_name: str = Field(
        "Northwest Ohio Broadband", max_length=80, pattern=r"^[^\r\n<>]+$"
    )
    reply_to: str
    approved_domains: list[str] = Field(min_length=1, max_length=10)
    verification_reference: str = Field(min_length=10, max_length=120)
    recipient_hashes: list[str] = Field(default_factory=list, max_length=1000)
    purposes: list[
        Literal[
            "operational",
            "maintenance",
            "signup",
            "installation",
            "cancellation",
            "retention",
        ]
    ] = Field(min_length=1, max_length=6)
    smtp_host: str | None = Field(
        None, max_length=253, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]+$"
    )
    smtp_port: Literal[465, 587] = 587
    smtp_tls: Literal["starttls", "implicit"] = "starttls"
    daily_messages: int = Field(100, ge=1, le=10000)
    monthly_messages: int = Field(1000, ge=1, le=100000)
    per_minute: int = Field(10, ge=1, le=100)
    estimated_cost_microunits: int | None = Field(None, ge=0, le=10000000)
    daily_budget_microunits: int = Field(1000000, ge=1, le=1000000000)
    monthly_budget_microunits: int = Field(10000000, ge=1, le=1000000000)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    callbacks_enabled: bool = False

    @field_validator("sender", "reply_to")
    @classmethod
    def mailbox(cls, v):
        return address(v)

    @field_validator("recipient_hashes")
    @classmethod
    def hashes(cls, v):
        if any(not re.fullmatch("[a-f0-9]{64}", x) for x in v):
            raise ValueError("Recipient identity hashes required")
        return sorted(set(v))

    @model_validator(mode="after")
    def approved(self):
        if any(
            not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,63}", d)
            for d in self.approved_domains
        ):
            raise ValueError("Approved DNS domains required")
        if any(
            a.rsplit("@", 1)[1] not in self.approved_domains
            for a in (self.sender, self.reply_to)
        ):
            raise ValueError("Sender/reply-to domain not approved")
        if (self.smtp_port == 465) != (self.smtp_tls == "implicit"):
            raise ValueError("465 requires implicit TLS; 587 requires STARTTLS")
        return self


class EmailRouting(SafeModel):
    primary: Literal["mock", "sendgrid", "smtp"] = "mock"
    secondary: Literal["sendgrid", "smtp"] | None = None
    failover: bool = False

    @model_validator(mode="after")
    def distinct(self):
        if self.primary == self.secondary:
            raise ValueError("Providers must differ")
        if self.failover:
            raise ValueError(
                "Automatic failover unavailable; independently approve proven nonacceptance"
            )
        return self


class EmailSecrets(SafeModel):
    value: SecretStr = Field(max_length=4096)


class EmailApproval(SafeModel):
    confirm: bool = False
    reference: str = Field(
        min_length=10, max_length=120, pattern=r"^[A-Za-z0-9_.: -]+$"
    )
    fingerprint: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")


class EmailPermission(SafeModel):
    customer_id: int | None = Field(None, ge=1)
    application_id: int | None = Field(None, ge=1)
    purpose: Literal[
        "operational",
        "maintenance",
        "signup",
        "installation",
        "cancellation",
        "retention",
    ]
    granted: bool
    verified_address: str
    reference: str = Field(
        min_length=10, max_length=120, pattern=r"^[A-Za-z0-9_.: -]+$"
    )

    @field_validator("verified_address")
    @classmethod
    def mailbox(cls, v):
        return address(v)

    @model_validator(mode="after")
    def subject(self):
        if (self.customer_id is None) == (self.application_id is None):
            raise ValueError("One canonical subject required")
        return self


class EmailTemplateRequest(SafeModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,50}$")
    purpose: Literal[
        "operational",
        "maintenance",
        "signup",
        "installation",
        "cancellation",
        "retention",
    ]
    subject: str = Field(min_length=3, max_length=160, pattern=r"^[^\r\n<>]+$")
    text: str = Field(min_length=10, max_length=4000)
    brand: str = Field(
        "Northwest Ohio Broadband", min_length=1, max_length=80, pattern=r"^[^<>\r\n]+$"
    )
    support: str = Field(
        "Contact your usual support channel.", max_length=160, pattern=r"^[^<>\r\n]+$"
    )

    @model_validator(mode="after")
    def safe(self):
        if TEMPLATE_PURPOSE.get(self.key) != self.purpose:
            raise ValueError("Select a supported source/purpose template category")
        for s in (self.subject, self.text):
            if (
                "<" in s
                or ">" in s
                or re.search(r"https?://|www\.|javascript:|data:", s, re.I)
            ):
                raise ValueError(
                    "Templates use safe text; external resources/links require separate reviewed contract"
                )
            for _, name, spec, conv in Formatter().parse(s):
                if name is not None and (
                    name not in {"reference", "status"} or spec or conv
                ):
                    raise ValueError("Only reference/status placeholders supported")
        return self


class EmailDraftRequest(SafeModel):
    source_kind: Literal["signup", "cancellation", "network"]
    source_id: int = Field(ge=1)
    customer_id: int | None = Field(None, ge=1)
    application_id: int | None = Field(None, ge=1)
    template_id: int = Field(ge=1)
