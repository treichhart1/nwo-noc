"""Offline one-device plan validation only; never contacts or activates equipment."""

import ipaddress, json, sys
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PilotPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    approved: Literal[True]
    approval_record: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{7,119}$")
    candidate_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    device_id: int = Field(ge=1)
    vendor: Literal["ubiquiti", "cambium", "racom", "aviat", "mikrotik"]
    address: str
    protocol: Literal["snmpv3_get", "https_get"]
    port: int
    interval_seconds: int = Field(300, ge=60, le=86400)
    operator_token_ttl_minutes: int = Field(15, ge=1, le=30)
    concurrency: Literal[1] = 1
    batch_size: Literal[1] = 1
    tls_verified: Literal[True] = True
    minimum_privilege_reviewed: Literal[True]
    private_secret_mount_reviewed: Literal[True]
    deny_other_egress_verified: Literal[True]
    firmware_mapping_validated: Literal[True]
    backup_restore_verified: Literal[True]
    simulation_only: Literal[True] = True

    @model_validator(mode="after")
    def single_endpoint(self):
        ip = ipaddress.ip_address(self.address)
        if ip.version != 4 or not any(
            ip in ipaddress.ip_network(n)
            for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        ):
            raise ValueError("Exactly one approved RFC1918 IPv4 endpoint required")
        expected = (
            ("https_get", 443) if self.vendor == "mikrotik" else ("snmpv3_get", 161)
        )
        if (self.protocol, self.port) != expected:
            raise ValueError("Vendor read-only protocol/port mismatch")
        return self


def review(value):
    p = PilotPlan.model_validate(value)
    return {
        "device_id": p.device_id,
        "vendor": p.vendor,
        "endpoint_count": 1,
        "interval_seconds": p.interval_seconds,
        "read_only": True,
        "execution_enabled": False,
        "network_calls": 0,
        "scope": "offline consistency only; approval assertions require independent operator evidence and do not enable a pilot",
    }


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as f:
            value = json.load(f)
        print(json.dumps(review(value)))
    except (ValueError, OSError, IndexError):
        raise SystemExit(
            "Pilot plan rejected: verify one approved endpoint, immutable candidate, read-only credentials, TLS/SNMPv3, egress, firmware and recovery evidence; never place credentials in this plan"
        )
