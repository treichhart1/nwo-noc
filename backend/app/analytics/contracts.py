from datetime import datetime, timezone, timedelta
from typing import Literal
from pydantic import Field, model_validator
from app.field.contracts import Mutation
from app.customers.contracts import SafeModel
from .catalog import METRICS, DASHBOARDS, DEFAULT_METRICS


class ConfigInput(Mutation):
    expected_revision: int = Field(ge=0)
    enabled: bool = False
    server_url: str = Field("", max_length=300)
    auth_mode: Literal["session", "auth_proxy"] = "session"
    embed_enabled: bool = False
    dashboards: list[str] = Field(
        default_factory=lambda: list(DASHBOARDS), max_length=10
    )
    export_device_ids: list[int] = Field(default_factory=list, max_length=100)
    export_metrics: list[str] = Field(
        default_factory=lambda: list(DEFAULT_METRICS), max_length=20
    )
    retention_days: int = Field(90, ge=1, le=90)
    stale_seconds: int = Field(900, ge=60, le=3600)

    @model_validator(mode="after")
    def allowlists(self):
        if (
            len(set(self.dashboards)) != len(self.dashboards)
            or set(self.dashboards) - DASHBOARDS.keys()
        ):
            raise ValueError("Approved dashboard IDs required")
        if (
            len(set(self.export_metrics)) != len(self.export_metrics)
            or set(self.export_metrics) - METRICS.keys()
        ):
            raise ValueError("Approved metric names required")
        if len(set(self.export_device_ids)) != len(self.export_device_ids) or any(
            id <= 0 for id in self.export_device_ids
        ):
            raise ValueError("Unique positive device identifiers required")
        if self.embed_enabled and self.auth_mode != "auth_proxy":
            raise ValueError("Embedding requires reviewed authentication proxy")
        return self


class RangeInput(SafeModel):
    preset: Literal["1h", "24h", "7d", "30d", "90d", "custom"] = "24h"
    start: datetime | None = None
    end: datetime | None = None
    site_id: int | None = Field(None, ge=1)
    device_id: int | None = Field(None, ge=1)
    metric: str = Field("rssi_dbm", max_length=50)
    group: Literal["site", "device", "network"] = "site"
    max_points: int = Field(60, ge=10, le=240)

    @model_validator(mode="after")
    def validate_range(self):
        if self.metric not in METRICS:
            raise ValueError("Unknown approved metric")
        if self.preset == "custom":
            if (
                not self.start
                or not self.end
                or not self.start.tzinfo
                or not self.end.tzinfo
            ):
                raise ValueError("Timezone-aware custom bounds required")
            if not timedelta(seconds=1) <= self.end - self.start <= timedelta(days=90):
                raise ValueError("Range must span 1 second to90 days")
            if self.end > datetime.now(timezone.utc) + timedelta(seconds=2):
                raise ValueError("Observed data cannot use future range")
        elif self.start or self.end:
            raise ValueError("Preset range cannot override bounds")
        return self


class ReportInput(Mutation):
    query: RangeInput


class LinkInput(Mutation):
    dashboard_uid: str = Field(max_length=50)
    query: RangeInput
    asset_id: int | None = Field(None, ge=1)
    embed: bool = False


class HealthInput(Mutation):
    mode: Literal["mock", "live"] = "mock"


class RollupInput(Mutation):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def bounds(self):
        if self.start.tzinfo:
            self.start = self.start.astimezone(timezone.utc)
        if self.end.tzinfo:
            self.end = self.end.astimezone(timezone.utc)
        if (
            not self.start.tzinfo
            or not self.end.tzinfo
            or self.start.minute
            or self.start.second
            or self.start.microsecond
            or self.end.minute
            or self.end.second
            or self.end.microsecond
            or not timedelta(hours=1) <= self.end - self.start <= timedelta(hours=24)
            or self.end
            > datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        ):
            raise ValueError("Closed UTC-aligned hours, at most24 per run, required")
        return self
