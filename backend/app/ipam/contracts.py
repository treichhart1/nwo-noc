from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from typing import Literal
import re
from pydantic import Field, field_validator, model_validator, StrictBool
from app.customers.contracts import SafeModel
from app.core.operator_text import clean_operator_text


def bare_address(value):
    address = ip_address(value)
    if getattr(address, "scope_id", None) is not None:
        raise ValueError(
            "Document the interface separately; zone-scoped addresses are not global IPAM host keys"
        )
    return str(address)


class Reason(SafeModel):
    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean(cls, v):
        v = clean_operator_text(v)
        if not v or len(v) < 10:
            raise ValueError("Meaningful credential-free reason required")
        return v


class VRFInput(Reason):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_. :/-]+$")
    rd: str | None = Field(None, max_length=100, pattern=r"^[0-9.:]+$")
    purpose: str = Field("", max_length=255)


class VLANInput(Reason):
    vrf_id: int = Field(ge=1)
    site_id: int = Field(ge=1)
    vid: int = Field(ge=1, le=4094, strict=True)
    name: str = Field(min_length=1, max_length=100)
    purpose: str = Field("", max_length=255)
    status: Literal["planned", "documented", "retired"] = "documented"


class InterfaceInput(Reason):
    device_id: int = Field(ge=1)
    vrf_id: int = Field(ge=1)
    external_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["physical", "vlan", "bridge", "loopback", "tunnel", "unknown"] = (
        "physical"
    )
    vlan_id: int | None = Field(None, ge=1)
    parent_id: int | None = Field(None, ge=1)


class PrefixInput(Reason):
    vrf_id: int = Field(ge=1)
    network: str = Field(max_length=60)
    parent_id: int | None = Field(None, ge=1)
    site_id: int | None = Field(None, ge=1)
    vlan_id: int | None = Field(None, ge=1)
    link_id: int | None = Field(None, ge=1)
    kind: Literal["block", "subnet"] = "subnet"
    scope: Literal["public", "private", "shared", "documentation", "other"] = "private"
    purpose: str = Field("", max_length=255)
    owner: str = Field("documented NOC plan", min_length=1, max_length=120)
    gateway: str | None = None

    @field_validator("network")
    @classmethod
    def net(cls, v):
        return str(ip_network(v, strict=True))

    @field_validator("gateway")
    @classmethod
    def gw(cls, v):
        return bare_address(v) if v else None


class PoolInput(Reason):
    prefix_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    first: str
    last: str
    kind: Literal["dhcp", "nat", "infrastructure", "customer"] = "dhcp"
    nat_reference: str | None = Field(None, max_length=120)

    @field_validator("first", "last")
    @classmethod
    def addr(cls, v):
        return bare_address(v)


class AssignmentInput(Reason):
    prefix_id: int = Field(ge=1)
    address: str | None = None
    state: Literal["assigned", "reserved"] = "reserved"
    kind: Literal[
        "static",
        "dhcp_reservation",
        "gateway",
        "management",
        "loopback",
        "point_to_point",
        "customer",
    ] = "static"
    owner: str = Field(min_length=1, max_length=120)
    device_id: int | None = Field(None, ge=1)
    interface_id: int | None = Field(None, ge=1)
    service_id: int | None = Field(None, ge=1)
    pool_id: int | None = Field(None, ge=1)
    dns_name: str | None = Field(None, max_length=253)

    @field_validator("address")
    @classmethod
    def addr(cls, v):
        return bare_address(v) if v else None

    @field_validator("dns_name")
    @classmethod
    def dns(cls, v):
        if v is None:
            return None
        v = v.rstrip(".").lower()
        if not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
            v,
        ):
            raise ValueError("Valid DNS name required")
        return v


class Allocation(Reason):
    state: Literal["assigned", "released"]


class Suggestion(SafeModel):
    prefix_id: int = Field(ge=1)
    length: int = Field(ge=0, le=128)
    count: int = Field(1, ge=1, le=16)


class DiscoveryInput(Reason):
    batch_key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    vrf_id: int = Field(ge=1)
    device_id: int = Field(ge=1)
    observed_at: datetime
    routeros_version: str = Field(pattern=r"^[67]\.\d+(?:[^\r\n]{0,80})$")
    protocol: Literal["rest7", "api6_recorded", "api7_recorded"]
    responses: dict = Field(max_length=16)

    @model_validator(mode="after")
    def compatible(self):
        if self.observed_at.tzinfo is None or self.observed_at > datetime.now(
            timezone.utc
        ):
            raise ValueError("Nonfuture aware collection time required")
        self.observed_at = self.observed_at.astimezone(timezone.utc)
        if self.protocol == "rest7" and not self.routeros_version.startswith("7."):
            raise ValueError("RouterOS6 has no REST API")
        if self.protocol == "api6_recorded" and not self.routeros_version.startswith(
            "6."
        ):
            raise ValueError("Protocol/version mismatch")
        if self.protocol == "api7_recorded" and not self.routeros_version.startswith(
            "7."
        ):
            raise ValueError("Protocol/version mismatch")
        return self


class ImportInput(Reason):
    csv_text: str = Field(min_length=1, max_length=256000)


class Approval(Reason):
    approved: StrictBool
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
