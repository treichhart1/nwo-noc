"""NOC-owned SYNTHETIC export contract, not a claim about native Powercode schema."""

import re
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class AccountRecord(SafeModel):
    external_id: str = Field(min_length=1, max_length=120)
    name: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=40)
    email: str | None = Field(None, max_length=255)
    updated_at: datetime
    deleted: bool = Field(False, strict=True)

    @field_validator("updated_at")
    @classmethod
    def aware(cls, v):
        if v.tzinfo is None or v > datetime.now(timezone.utc):
            raise ValueError("A non-future timezone-aware timestamp is required")
        return v


class ServiceRecord(SafeModel):
    external_id: str = Field(min_length=1, max_length=120)
    account_id: str = Field(min_length=1, max_length=120)
    status: Literal["active", "suspended", "disconnected", "unknown"]
    service_type: Literal["wireless", "fiber", "hybrid", "unknown"]
    address: str | None = Field(None, max_length=500)
    plan: str | None = Field(None, max_length=120)
    download_mbps: float | None = Field(None, ge=0, le=1000000, allow_inf_nan=False)
    upload_mbps: float | None = Field(None, ge=0, le=1000000, allow_inf_nan=False)
    equipment_id: str | None = Field(None, max_length=120)
    source_refs: dict[Literal["tower", "access_point", "sector", "fiber"], str] = Field(
        default_factory=dict
    )
    updated_at: datetime
    deleted: bool = Field(False, strict=True)

    _aware = field_validator("updated_at")(AccountRecord.aware.__func__)

    @field_validator("source_refs")
    @classmethod
    def refs(cls, v):
        if any(not x or len(x) > 120 for x in v.values()):
            raise ValueError("Bounded source references required")
        return v


class ExportBatch(SafeModel):
    schema_version: Literal["noc-powercode-export-v1"]
    snapshot_id: str = Field(min_length=1, max_length=120)
    mode: Literal["full", "incremental"]
    complete: bool = Field(strict=True)
    as_of: datetime
    previous_cursor: str | None = Field(None, max_length=200)
    cursor: str = Field(min_length=1, max_length=200)
    accounts: list[AccountRecord] = Field(max_length=10000)
    services: list[ServiceRecord] = Field(max_length=20000)
    _aware = field_validator("as_of")(AccountRecord.aware.__func__)

    @model_validator(mode="after")
    def timestamps(self):
        if any(r.updated_at > self.as_of for r in self.accounts + self.services):
            raise ValueError("Record newer than snapshot")
        return self


def e164(value):
    """No guessed country code or silently removed extension."""
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"\+[1-9][0-9]{7,14}", value)
        else None
    )


def masked_phone(value):
    return "***" + value[-4:] if e164(value) else ("invalid" if value else "missing")


def masked_email(value):
    return (
        "***@" + value.rsplit("@", 1)[1]
        if value and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)
        else ("invalid" if value else "missing")
    )
