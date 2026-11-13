"""Closed RF v2 contract. Original values are only accepted telemetry scalars."""
from datetime import timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, StrictInt, StrictFloat, StrictStr, field_validator, model_validator
from app.services.rf_observation import RFMeasurements

PROFILES = {'airmax-ac-mib-v1': 'ubiquiti', 'airmax-legacy-mib-v1': 'ubiquiti',
            'epmp-4600-mib-v1': 'cambium', 'epmp-legacy-mib-v1': 'cambium'}


class RadioMetrics(RFMeasurements):
    remote_rssi_dbm: float | None = Field(None, ge=-150, le=20, strict=True)
    tx_throughput_mbps: float | None = Field(None, ge=0, le=1e6, strict=True)
    rx_throughput_mbps: float | None = Field(None, ge=0, le=1e6, strict=True)
    mcs_index: int | None = Field(None, ge=0, le=31, strict=True)
    subscriber_count: int | None = Field(None, ge=0, le=10000, strict=True)
    utilization_percent: float | None = Field(None,ge=0,le=100,strict=True)


class RadioCounters(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', hide_input_in_errors=True)
    tx_errors: int | None = Field(None, ge=0, le=2**64-1, strict=True)
    rx_errors: int | None = Field(None, ge=0, le=2**64-1, strict=True)
    retries: int | None = Field(None, ge=0, le=2**64-1, strict=True)
    disconnects: int | None = Field(None, ge=0, le=2**64-1, strict=True)


ENUMS = {'radio_status': {'up', 'down', 'degraded', 'unknown'},
         'gps_sync': {'locked', 'unlocked', 'not_present', 'unknown'},
         'modulation': {'BPSK', 'QPSK', '16QAM', '64QAM', '256QAM', '1024QAM', '4096QAM', 'unknown'}}


class RadioObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', hide_input_in_errors=True)
    schema_version: Literal['rf_radio_v2'] = 'rf_radio_v2'
    vendor: Literal['ubiquiti', 'cambium']
    profile: Literal['airmax-ac-mib-v1', 'airmax-legacy-mib-v1', 'epmp-4600-mib-v1', 'epmp-legacy-mib-v1']
    source_kind: Literal['synthetic_export', 'snmpv3_read_only']
    mapping_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    observed_at: AwareDatetime
    received_at: AwareDatetime
    timestamp_basis: Literal['collector_receipt', 'source_export']
    measurements: RadioMetrics
    counters: RadioCounters = Field(default_factory=RadioCounters)
    radio_status: Literal['up', 'down', 'degraded', 'unknown'] | None = None
    gps_sync: Literal['locked', 'unlocked', 'not_present', 'unknown'] | None = None
    modulation: Literal['BPSK', 'QPSK', '16QAM', '64QAM', '256QAM', '1024QAM', '4096QAM', 'unknown'] | None = None
    unavailable: dict[str, Literal['missing', 'unsupported', 'invalid']] = Field(default_factory=dict)
    # No arbitrary JSON or strings: keys are canonical fields and values validated
    # against typed measurements/counters or fixed enumerations below.
    original_values: dict[str, StrictInt | StrictFloat | StrictStr] = Field(default_factory=dict)
    original_units: dict[str, Literal['dbm', 'db', 'percent', 'mhz', 'hz', 'khz', 'mbps', 'bits_per_second', 'kbps', 'seconds', 'centiseconds', 'celsius', 'count', 'enum']] = Field(default_factory=dict)

    @field_validator('observed_at', 'received_at')
    @classmethod
    def utc(cls, value):
        return value.astimezone(timezone.utc)

    @model_validator(mode='after')
    def provenance(self):
        import math
        if PROFILES[self.profile] != self.vendor:
            raise ValueError('Vendor/profile mismatch')
        if self.source_kind == 'snmpv3_read_only' and (self.timestamp_basis != 'collector_receipt' or self.observed_at != self.received_at):
            raise ValueError('SNMP observations require collector receipt time')
        fields = set(RadioMetrics.model_fields) | set(RadioCounters.model_fields) | set(ENUMS)
        if set(self.original_units) != set(self.original_values):
            raise ValueError('Original telemetry units required')
        if set(self.unavailable) - fields or set(self.original_values) - fields:
            raise ValueError('Unknown telemetry field')
        for name in fields:
            value = getattr(self.measurements, name) if name in RadioMetrics.model_fields else getattr(self.counters, name) if name in RadioCounters.model_fields else getattr(self, name)
            if value is None and name not in self.unavailable or value is not None and name in self.unavailable:
                raise ValueError('Availability conflicts with telemetry')
            if name in self.original_values:
                original = self.original_values[name]
                if value is None or isinstance(original, bool):
                    raise ValueError('Invalid original telemetry')
                if name in ENUMS:
                    if not (isinstance(original, int) and 0 <= original <= 65535 or original in ENUMS[name]):
                        raise ValueError('Unknown original enumeration')
                else:
                    try: finite = isinstance(original, (int, float)) and math.isfinite(original)
                    except OverflowError: finite = False
                    if not finite: raise ValueError('Original telemetry must be finite numeric')
                    if name in RadioCounters.model_fields or name in {'subscriber_count', 'mcs_index'}:
                        if self.original_units[name] != 'count' or original != value:
                            raise ValueError('Original counter/count mismatch')
                    scales = {'hz': 1e-6, 'khz': .001, 'bits_per_second': 1e-6, 'kbps': .001, 'centiseconds': .01}
                    if not math.isclose(original * scales.get(self.original_units[name], 1), value, rel_tol=1e-12):
                        raise ValueError('Original/normalized telemetry mismatch')
        return self
