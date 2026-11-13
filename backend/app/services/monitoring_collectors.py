"""Closed read-only collectors. Scheduling never grants transport authorization."""

import asyncio, hashlib, re, time
from datetime import datetime, timedelta, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from app.core.config import settings
from app.models.entities import Device, NetworkLink, TelemetrySample
from app.integrations.read_only import (
    IntegrationFailure,
    ReadClient,
    ReadPolicy,
    MIKROTIK_PATHS,
    secret,
)
from app.services.radio_observation import RadioObservation
from app.services.microwave_model import (
    MicrowaveObservation,
    CarrierObservation,
    CarrierMetrics,
    LinkMetrics,
    ALARMS,
    MODULATIONS,
)
from app.services.rf_observation import NormalizedRFTelemetry, RFMeasurements
from app.services.radio_history import stage_radio
from app.services.microwave_history import stage_microwave, validate_association
from app.services.vendor_rf_observations import stage_observation
from app.services.monitoring_policy import Fixture


class Interface(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(pattern=r"^\*[0-9A-Fa-f]{1,10}$")
    running: bool | None = None
    disabled: bool | None = None
    rx_bytes: int | None = Field(None, ge=0, le=2**64 - 1)
    tx_bytes: int | None = Field(None, ge=0, le=2**64 - 1)
    rx_errors: int | None = Field(None, ge=0, le=2**64 - 1)
    tx_errors: int | None = Field(None, ge=0, le=2**64 - 1)
    rx_drops: int | None = Field(None, ge=0, le=2**64 - 1)
    tx_drops: int | None = Field(None, ge=0, le=2**64 - 1)


class RouterSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    cpu_percent: float | None = Field(None, ge=0, le=100)
    ram_percent: float | None = Field(None, ge=0, le=100)
    uptime_seconds: float | None = Field(None, ge=0, le=315360000)
    temperature_c: float | None = Field(None, ge=-100, le=200)
    interfaces: list[Interface] = Field(default_factory=list, max_length=512)
    ospf_states: list[
        Literal[
            "full",
            "down",
            "init",
            "exchange",
            "loading",
            "2-way",
            "exstart",
            "attempt",
            "unknown",
        ]
    ] = Field(default_factory=list, max_length=512)


class Envelope(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )
    device_id: int = Field(ge=1)
    link_id: int | None = Field(None, ge=1)
    format: Literal["radio", "microwave", "foundation", "mikrotik"]
    sampled_at: datetime
    payload: dict

    @model_validator(mode="after")
    def typed(self):
        if self.sampled_at.tzinfo is None:
            raise ValueError("Aware observation time required")
        model = {
            "radio": RadioObservation,
            "microwave": MicrowaveObservation,
            "foundation": NormalizedRFTelemetry,
            "mikrotik": RouterSnapshot,
        }[self.format]
        value = model.model_validate(self.payload)
        if self.format != "mikrotik" and value.observed_at != self.sampled_at:
            raise ValueError("Observation timestamp mismatch")
        if self.format == "microwave" and self.link_id is None:
            raise ValueError("Microwave link required")
        return self


def synthetic(db, job, now):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise IntegrationFailure("disabled")
    d = db.get(Device, job.device_id)
    if not d or d.source != "synthetic":
        raise IntegrationFailure("endpoint_denied")
    f = Fixture(**job.fixture)
    time.sleep(f.latency_ms / 1000)
    if f.mode in {"timeout", "disconnected"}:
        raise IntegrationFailure("timeout" if f.mode == "timeout" else "unavailable")
    if f.mode == "invalid":
        raise IntegrationFailure("invalid_response")
    at = now - timedelta(seconds=1800) if f.mode == "stale" else now
    vendor = (d.vendor or "").casefold()
    if vendor == "mikrotik":
        value = RouterSnapshot(
            cpu_percent=30,
            ram_percent=40,
            uptime_seconds=100000,
            temperature_c=95 if f.mode == "overheat" else 40,
            interfaces=[
                Interface(
                    id="*1",
                    running=f.mode != "ethernet",
                    disabled=False,
                    rx_errors=0,
                    tx_errors=0,
                )
            ],
            ospf_states=["full"],
        )
        return Envelope(
            device_id=d.id,
            format="mikrotik",
            sampled_at=at,
            payload=value.model_dump(mode="json"),
        )
    if vendor in {"ubiquiti", "cambium"}:
        from app.rf_analytics_rehearsal import synthetic_observation

        scenario = (
            "interference"
            if f.mode == "interference"
            else "congestion" if f.mode == "utilization" else "healthy"
        )
        obs = synthetic_observation(at, now=at, sequence=1, scenario=scenario)
        data = obs.model_dump()
        data["vendor"] = vendor
        data["profile"] = (
            "airmax-ac-mib-v1" if vendor == "ubiquiti" else "epmp-4600-mib-v1"
        )
        changes = {}
        if f.mode == "weak":
            changes["rssi_dbm"] = -85.0
        if f.mode == "low_snr":
            changes["snr_db"] = 10.0
        if f.mode == "capacity":
            changes["link_capacity_mbps"] = 30.0
        if f.mode == "overheat":
            changes["temperature_c"] = 95.0
        if f.mode == "missing":
            changes = {k: None for k in data["measurements"]}
        for k, v in changes.items():
            data["measurements"][k] = v
            if v is None:
                data["unavailable"][k] = "missing"
                data["original_values"].pop(k, None)
                data["original_units"].pop(k, None)
            else:
                data["original_values"][k] = v
        if f.mode == "outage":
            data["radio_status"] = "down"
            data["original_values"]["radio_status"] = 2
        if f.mode == "fallback":
            data["modulation"] = "QPSK"
            data["original_values"]["modulation"] = 2
        if f.mode == "missing":
            data["modulation"] = None
            data["unavailable"]["modulation"] = "missing"
            data["original_values"].pop("modulation", None)
            data["original_units"].pop("modulation", None)
            data["radio_status"] = None
            data["unavailable"]["radio_status"] = "missing"
            data["original_values"].pop("radio_status", None)
            data["original_units"].pop("radio_status", None)
        # Synthetic uptime is unspecified; never fabricate counter continuity.
        data["measurements"]["uptime_seconds"] = None
        data["unavailable"]["uptime_seconds"] = "missing"
        data["original_values"].pop("uptime_seconds", None)
        data["original_units"].pop("uptime_seconds", None)
        obs = RadioObservation.model_validate(data)
        return Envelope(
            device_id=d.id,
            format="radio",
            sampled_at=at,
            payload=obs.model_dump(mode="json"),
        )
    link = db.scalar(
        select(NetworkLink)
        .where(NetworkLink.a_device_id == d.id)
        .order_by(NetworkLink.id)
        .limit(1)
    )
    if vendor not in {"racom", "aviat"}:
        raise IntegrationFailure("unsupported")
    if link is None:
        obs = NormalizedRFTelemetry(
            vendor=vendor,
            profile=vendor + "-unit-export-v1",
            source_kind="synthetic_export",
            observed_at=at,
            received_at=at,
            measurements=RFMeasurements(
                rssi_dbm=-85.0 if f.mode == "weak" else -60.0,
                snr_db=10.0 if f.mode == "low_snr" else 30.0,
            ),
            radio_status="down" if f.mode == "outage" else "up",
        )
        return Envelope(
            device_id=d.id,
            format="foundation",
            sampled_at=at,
            payload=obs.model_dump(mode="json"),
        )
    carriers = {}
    original = {}
    units = {}
    for key in ("c1", "c2"):
        down = f.mode == "outage" or f.mode == "carrier_failure" and key == "c1"
        metrics = CarrierMetrics(
            local_rsl_dbm=-85.0 if f.mode == "weak" else -60.0,
            snr_db=10.0 if f.mode == "low_snr" else 30.0,
            capacity_mbps=20.0 if f.mode == "capacity" or down else 500.0,
            frequency_mhz=18000.0,
            bandwidth_mhz=80.0,
            temperature_c=95.0 if f.mode == "overheat" else 40.0,
            utilization_percent=95.0 if f.mode == "utilization" else 30.0,
        )
        known = metrics.model_dump(exclude_none=True)
        unavailable = {
            k: "missing" for k, v in metrics.model_dump().items() if v is None
        }
        unavailable["errors"] = "unsupported"
        unavailable.update({"alarm_" + k: "unsupported" for k in ALARMS})
        carrier = CarrierObservation(
            measurements=metrics,
            radio_status="down" if down else "up",
            modulation="QPSK" if f.mode == "fallback" else "256QAM",
            adaptive_modulation="enabled",
            unavailable=unavailable,
        )
        carriers[key] = carrier
        for k, v in known.items():
            original[key + "." + k] = v
            units[key + "." + k] = (
                "dbm"
                if k.endswith("dbm")
                else (
                    "db"
                    if k.endswith("_db")
                    else (
                        "mhz"
                        if k.endswith("mhz")
                        else (
                            "mbps"
                            if k.endswith("mbps")
                            else "percent" if k.endswith("percent") else "celsius"
                        )
                    )
                )
            )
        for k in ("radio_status", "modulation", "adaptive_modulation"):
            original[key + "." + k] = {
                "up": 1,
                "down": 2,
                "enabled": 1,
                "disabled": 2,
                "QPSK": 2,
                "256QAM": 8,
            }[getattr(carrier, k)]
            units[key + "." + k] = "enum"
    lm = LinkMetrics()
    unavailable = {k: "missing" for k in LinkMetrics.model_fields}
    unavailable.update(firmware_version="unsupported", hardware_id="unsupported")
    original["link.ethernet_status"] = 2 if f.mode == "ethernet" else 1
    units["link.ethernet_status"] = "enum"
    obs = MicrowaveObservation(
        profile=(
            "racom-ray3-mib-v1" if vendor == "racom" else "aviat-multicarrier-mib-v1"
        ),
        vendor=vendor,
        mapping_digest=hashlib.sha256(b"noc92-synthetic-carriers").hexdigest(),
        source_kind="synthetic_export",
        timestamp_basis="source_export",
        observed_at=at,
        received_at=at,
        carriers=carriers,
        measurements=lm,
        ethernet_status="down" if f.mode == "ethernet" else "up",
        unavailable=unavailable,
        original_values=original,
        original_units=units,
    )
    return Envelope(
        device_id=d.id,
        link_id=link.id,
        format="microwave",
        sampled_at=at,
        payload=obs.model_dump(mode="json"),
    )


def collect(db, job, now):
    if job.kind == "synthetic":
        return synthetic(db, job, now)
    if settings.offline_mode or not settings.integrations_admin_approved:
        raise IntegrationFailure("disabled")
    d = db.get(Device, job.device_id)
    if not d or not d.enabled or not d.authorized:
        raise IntegrationFailure("endpoint_denied")
    if job.kind in {"radio", "microwave"}:
        from app.radio_poll import private_json, PollConfig
        from app.microwave_poll import MicrowavePollConfig
        from app.integrations.radio_snmp import SNMPCredentials, RadioCollector
        from app.integrations.microwave_snmp import MicrowaveCollector

        entries = (
            private_json(settings.rf_scheduler_manifest_file, max_bytes=262144)
            if settings.rf_scheduler_manifest_file
            else []
        )
        if not isinstance(entries, list) or len(entries) > 1000:
            raise IntegrationFailure("configuration_invalid")
        entry = next(
            (
                e
                for e in entries
                if isinstance(e, dict)
                and e.get("kind") == job.kind
                and e.get("device_id") == d.id
            ),
            None,
        )
        if not entry:
            raise IntegrationFailure("configuration_invalid")
        enabled = (
            settings.rf_snmp_enabled
            if job.kind == "radio"
            else settings.rf_microwave_enabled
        )
        if not enabled:
            raise IntegrationFailure("disabled")
        cls = PollConfig if job.kind == "radio" else MicrowavePollConfig
        cfg = cls.model_validate(private_json(entry["config_file"]))
        cfg.policy.validate_connection()
        if (
            cfg.device_id != d.id
            or cfg.policy.address != d.ip_address
            or job.interval_seconds < cfg.policy.min_interval_seconds
        ):
            raise IntegrationFailure("endpoint_denied")
        if job.kind == "microwave":
            validate_association(
                d, db.get(NetworkLink, cfg.network_link_id), cfg.mapping.profile
            )
        else:
            from app.services.radio_observation import PROFILES

            if (d.vendor or "").casefold() not in (
                {"ubiquiti", "ubiquiti networks", "ubnt"}
                if PROFILES[cfg.mapping.profile] == "ubiquiti"
                else {"cambium", "cambium networks"}
            ):
                raise IntegrationFailure("configuration_invalid")
        credentials = SNMPCredentials.model_validate(private_json(cfg.credentials_file))
        client = (
            RadioCollector(cfg.mapping, cfg.policy)
            if job.kind == "radio"
            else MicrowaveCollector(cfg.mapping, cfg.policy)
        )

        async def bounded():
            return await asyncio.wait_for(
                client.collect(credentials), timeout=job.timeout_seconds
            )

        try:
            obs = asyncio.run(bounded())
        except TimeoutError:
            raise IntegrationFailure("timeout") from None
        return Envelope(
            device_id=d.id,
            link_id=cfg.network_link_id if job.kind == "microwave" else None,
            format=job.kind,
            sampled_at=obs.observed_at,
            payload=obs.model_dump(mode="json"),
        )
    if job.kind == "mikrotik":
        if not settings.mikrotik_enabled or settings.mikrotik_device_id != d.id:
            raise IntegrationFailure("disabled")
        from app.integrations.mikrotik import normalize, require_routeros7

        policy = ReadPolicy(
            settings.mikrotik_base_url,
            tuple(settings.mikrotik_allowed_origins.split(",")),
            MIKROTIK_PATHS,
            True,
            True,
        )
        with ReadClient(
            policy,
            auth=(settings.mikrotik_username, secret(settings.mikrotik_password)),
        ) as client:
            raw = {}
            for k, path in [
                ("identity", "/rest/system/identity"),
                ("resource", "/rest/system/resource"),
                ("interfaces", "/rest/interface"),
                ("health", "/rest/system/health"),
                ("neighbors", "/rest/routing/ospf/neighbor"),
            ]:
                try:
                    raw[k] = client.request(path)[0]
                except IntegrationFailure as exc:
                    if k in {"health", "neighbors"} and exc.code == "unsupported":
                        raw[k] = []
                    else:
                        raise
        require_routeros7(raw["resource"])
        values = normalize(raw, (settings.mikrotik_password,))
        fields = {
            k: values[k]
            for k in ("cpu_percent", "ram_percent", "uptime_seconds", "temperature_c")
        }
        fields["interfaces"] = [
            {k: v for k, v in i.items() if k in Interface.model_fields}
            for i in values["interfaces"]
            if re.fullmatch(r"\*[0-9a-fA-F]{1,10}", i["id"])
        ]
        fields["ospf_states"] = [
            (
                n["state"].lower()
                if n["state"].lower()
                in {
                    "full",
                    "down",
                    "init",
                    "exchange",
                    "loading",
                    "2-way",
                    "exstart",
                    "attempt",
                }
                else "unknown"
            )
            for n in values["ospf_neighbors"]
        ]
        snap = RouterSnapshot(**fields)
        return Envelope(
            device_id=d.id,
            format="mikrotik",
            sampled_at=now,
            payload=snap.model_dump(mode="json"),
        )
    raise IntegrationFailure("unsupported")


def stage(db, device, envelope):
    if envelope.device_id != device.id:
        raise IntegrationFailure("endpoint_denied")
    if envelope.format == "radio":
        return stage_radio(
            db, device, RadioObservation.model_validate(envelope.payload)
        )
    if envelope.format == "microwave":
        return stage_microwave(
            db,
            device,
            db.get(NetworkLink, envelope.link_id),
            MicrowaveObservation.model_validate(envelope.payload),
        )
    if envelope.format == "foundation":
        return stage_observation(
            db, device, NormalizedRFTelemetry.model_validate(envelope.payload)
        )
    existing = db.scalar(
        select(TelemetrySample)
        .where(
            TelemetrySample.device_id == device.id,
            TelemetrySample.sampled_at == envelope.sampled_at,
            TelemetrySample.raw_data["mikrotik_v1"].is_not(None),
        )
        .limit(1)
    )
    if existing:
        return existing
    snap = RouterSnapshot(**envelope.payload)
    values = snap.model_dump()
    values["ospf_neighbors"] = [{"state": s} for s in values.pop("ospf_states")]
    before = db.scalar(
        select(TelemetrySample)
        .where(
            TelemetrySample.device_id == device.id,
            TelemetrySample.raw_data["mikrotik_v1"].is_not(None),
        )
        .order_by(TelemetrySample.sampled_at.desc(), TelemetrySample.id.desc())
        .limit(1)
    )
    old = (before.raw_data or {}).get("mikrotik_v1", {}) if before else {}
    old_interfaces = {
        i["id"]: i
        for i in old.get("interfaces", [])
        if isinstance(i, dict) and "id" in i
    }
    continuity = (
        before
        and before.sampled_at < envelope.sampled_at
        and old.get("uptime_seconds") is not None
        and values["uptime_seconds"] is not None
        and values["uptime_seconds"] >= old["uptime_seconds"]
    )
    values["counter_reset"] = bool(
        before
        and old.get("uptime_seconds") is not None
        and values["uptime_seconds"] is not None
        and values["uptime_seconds"] < old["uptime_seconds"]
    )
    for i in values["interfaces"]:
        prev = old_interfaces.get(i["id"], {})
        for k in (
            "rx_bytes",
            "tx_bytes",
            "rx_errors",
            "tx_errors",
            "rx_drops",
            "tx_drops",
        ):
            a, b = i.get(k), prev.get(k)
            i[k + "_delta"] = (
                a - b
                if continuity and a is not None and b is not None and a >= b
                else None
            )
    sample = TelemetrySample(
        device_id=device.id,
        sampled_at=envelope.sampled_at,
        cpu_percent=snap.cpu_percent,
        ram_percent=snap.ram_percent,
        temperature_c=snap.temperature_c,
        raw_data={"mikrotik_v1": values},
    )
    db.add(sample)
    db.flush()
    return sample
