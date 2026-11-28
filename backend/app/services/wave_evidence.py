"""Offline Wave evidence foundation, NOT a Ubiquiti wire/API contract.

No sockets, credentials, scheduler registration, network writes or inferred OIDs.
Live support is deliberately empty until authoritative secure contracts arrive.
"""
from datetime import datetime, timezone
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from app.models.entities import Device, NetworkLink, TelemetrySample
from app.services.rf_metrics import normalize_rf_metrics

MODELS = ('Wave LR', 'Wave Pro', 'Wave Nano', 'Wave AP', 'Wave AP Micro',
          'Wave AP Gen2', 'Wave Pico', 'airFiber 60', 'airFiber 60 LR', 'airFiber 60 XR')
KEY = 'wave_evidence_v1'


def compatibility():
    return {'native_collection_enabled': False, 'verified_firmware_contracts': [],
            'scope': 'Offline synthetic evidence; not equipment certification',
            'models': [{'model': m, 'status': 'BLOCKED', 'required':
                        'Official per-firmware MIB/UISP schema, secure protocol and sanitized fixture'} for m in MODELS]}


def require_native_contract(model: str, firmware: str):
    # An inventory model, a hash or administrator approval is not a wire contract.
    raise ValueError('Native Wave collection blocked: no verified model/firmware secure contract')


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)


class Metrics(Closed):
    rssi_dbm: float | None = Field(None, ge=-150, le=20, strict=True, allow_inf_nan=False)
    snr_db: float | None = Field(None, ge=-20, le=100, strict=True, allow_inf_nan=False)
    tx_capacity_mbps: float | None = Field(None, ge=0, le=1e6, strict=True, allow_inf_nan=False)
    rx_capacity_mbps: float | None = Field(None, ge=0, le=1e6, strict=True, allow_inf_nan=False)
    tx_throughput_mbps: float | None = Field(None, ge=0, le=1e6, strict=True, allow_inf_nan=False)
    rx_throughput_mbps: float | None = Field(None, ge=0, le=1e6, strict=True, allow_inf_nan=False)
    frequency_mhz: float | None = Field(None, ge=100, le=100000, strict=True, allow_inf_nan=False)
    channel_width_mhz: float | None = Field(None, ge=1, le=5000, strict=True, allow_inf_nan=False)
    temperature_c: float | None = Field(None, ge=-100, le=200, strict=True, allow_inf_nan=False)
    uptime_seconds: float | None = Field(None, ge=0, le=315360000, strict=True, allow_inf_nan=False)
    tx_mcs: int | None = Field(None, ge=0, le=255, strict=True)
    rx_mcs: int | None = Field(None, ge=0, le=255, strict=True)
    ethernet_speed_mbps: float | None = Field(None, ge=0, le=1e6, strict=True, allow_inf_nan=False)
    packet_loss_percent: float | None = Field(None, ge=0, le=100, strict=True, allow_inf_nan=False)
    tx_bytes: int | None = Field(None, ge=0, le=2**64-1, strict=True)
    rx_bytes: int | None = Field(None, ge=0, le=2**64-1, strict=True)
    ethernet_errors: int | None = Field(None, ge=0, le=2**64-1, strict=True)
    ethernet_status: Literal['up', 'down', 'unknown'] | None = None


class Station(Closed):
    device_id: int = Field(gt=0, strict=True)
    state: Literal['up', 'down', 'unknown']
    metrics: Metrics = Field(default_factory=Metrics)


class Observation(Closed):
    schema_version: Literal['wave_evidence_v1'] = KEY
    source_kind: Literal['synthetic_export'] = 'synthetic_export'
    model: str = Field(max_length=80)
    firmware: Literal['synthetic-v1'] = 'synthetic-v1'
    observed_at: AwareDatetime
    role: Literal['ptp', 'ap', 'subscriber']
    availability: Literal['up', 'down', 'unknown']
    metrics: Metrics = Field(default_factory=Metrics)
    link_id: int | None = Field(None, gt=0, strict=True)
    remote_device_id: int | None = Field(None, gt=0, strict=True)
    ap_device_id: int | None = Field(None, gt=0, strict=True)
    station_table_complete: bool = Field(False, strict=True)
    stations: list[Station] = Field(default_factory=list, max_length=256)
    connected_station_count: int | None = Field(None, ge=0, le=10000, strict=True)
    primary_state: Literal['up', 'down', 'unknown'] | None = None
    backup_state: Literal['up', 'down', 'unsupported', 'unknown'] | None = None
    active_path: Literal['primary', 'backup', 'none', 'unknown'] | None = None
    backup_rssi_dbm: float | None = Field(None, ge=-150, le=20, strict=True, allow_inf_nan=False)
    backup_capacity_mbps: float | None = Field(None, ge=0, le=1e6, strict=True, allow_inf_nan=False)

    @model_validator(mode='after')
    def relationships(self):
        if self.model not in MODELS:
            raise ValueError('Unsupported equipment identity')
        if self.role == 'ptp' and (self.link_id is None or self.remote_device_id is None):
            raise ValueError('Existing link and remote endpoint required')
        if self.role != 'ptp' and (self.link_id is not None or self.remote_device_id is not None):
            raise ValueError('PTP references on non-PTP record')
        if self.role == 'subscriber' and self.ap_device_id is None:
            raise ValueError('Existing AP association required')
        if self.role != 'subscriber' and self.ap_device_id is not None:
            raise ValueError('AP association only belongs to subscriber')
        if self.role != 'ap' and (self.stations or self.station_table_complete or self.connected_station_count is not None):
            raise ValueError('Station table only belongs to AP')
        ids = [s.device_id for s in self.stations]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate station identity')
        if self.station_table_complete and self.connected_station_count is not None:
            if self.connected_station_count != sum(s.state == 'up' for s in self.stations):
                raise ValueError('Complete station count conflicts with explicit states')
        if self.active_path == 'backup' and (self.backup_state != 'up' or self.availability != 'up'):
            raise ValueError('Backup path requires documented operational backup')
        if self.active_path == 'primary' and (self.primary_state != 'up' or self.availability != 'up'):
            raise ValueError('Primary path requires documented operational primary')
        if self.backup_state == 'unsupported' and (self.backup_rssi_dbm is not None or self.backup_capacity_mbps is not None):
            raise ValueError('Unsupported backup cannot have measurements')
        return self


def _utc(t):
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def stage_wave(db, device_id, observation, *, now, offline_authorized=False):
    """Caller owns transaction; lock same canonical device domain as RF staging."""
    if not offline_authorized or not isinstance(observation, Observation):
        raise ValueError('Explicit offline synthetic authorization required')
    if not 0 <= (_utc(now) - observation.observed_at).total_seconds() <= 3600:
        raise ValueError('Synthetic observation must be recent and not future')
    if db.get_bind().dialect.name != 'postgresql':
        raise ValueError('PostgreSQL required')
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': 861000 + device_id})
    device = db.get(Device, device_id)
    if device is None or device.source != 'synthetic' or not device.enabled or device.model != observation.model:
        raise ValueError('Matching enabled synthetic inventory required')
    if (device.vendor or '').casefold() not in {'ubiquiti', 'ubnt', 'ubiquiti networks'}:
        raise ValueError('Inventory vendor mismatch')
    references = {s.device_id for s in observation.stations}
    references |= {v for v in (observation.remote_device_id, observation.ap_device_id) if v is not None}
    if device_id in references:
        raise ValueError('Self association denied')
    if references:
        found = db.scalars(select(Device).where(Device.id.in_(references), Device.source == 'synthetic', Device.enabled.is_(True))).all()
        if len(found) != len(references):
            raise ValueError('Every association must use existing synthetic inventory')
    if observation.link_id is not None:
        link = db.get(NetworkLink, observation.link_id)
        if link is None or {link.a_device_id, link.z_device_id} != {device_id, observation.remote_device_id}:
            raise ValueError('Existing canonical link endpoints do not match')
    existing = db.scalar(select(TelemetrySample).where(TelemetrySample.device_id == device_id,
        TelemetrySample.sampled_at == observation.observed_at,
        TelemetrySample.raw_data[KEY].is_not(None)).limit(1))
    payload = observation.model_dump(mode='json')
    if existing:
        if existing.raw_data[KEY] != payload:
            raise ValueError('Conflicting observation at existing timestamp')
        return existing
    # Synthetic history is isolated from production incident/health projections.
    # No fake healthy state, capacity or native measurement is written to Device.
    row = TelemetrySample(device_id=device_id, sampled_at=observation.observed_at, raw_data={KEY: payload})
    db.add(row)
    db.flush()
    return row


def analyze(previous, current, *, now):
    """Derived evidence only; missing/incomplete tables never prove disconnect."""
    age = (_utc(now) - current.observed_at).total_seconds()
    result = {'scope': 'synthetic', 'freshness': 'fresh' if 0 <= age <= 600 else 'unknown',
              'availability': current.availability if 0 <= age <= 600 else 'unknown',
              'backup_transition': None, 'observed_backup_seconds': None,
              'subscriber_events': [], 'rates_mbps': {}, 'counter_state': 'baseline',
              'recommendations': [], 'recovery_verified': False}
    if result['freshness'] != 'fresh':
        return result
    evidence = current.observed_at.isoformat()
    if current.active_path == 'backup':
        result['recommendations'].append({'finding': 'operational_on_backup', 'evidence_at': evidence,
            'advice': 'Investigate primary path; do not force failover'})
    if current.metrics.rssi_dbm is not None and current.metrics.rssi_dbm < -75:
        result['recommendations'].append({'finding': 'weak_signal', 'evidence_at': evidence,
            'assumption': 'Advisory threshold -75 dBm; not manufacturer sensitivity',
            'advice': 'Review alignment, obstruction and independently recorded weather; cause unknown'})
    if previous is None:
        return result
    elapsed = (current.observed_at - previous.observed_at).total_seconds()
    if elapsed <= 0:
        raise ValueError('History must be strictly chronological')
    if (previous.model, previous.firmware, previous.role) != (current.model, current.firmware, current.role) or elapsed > 600:
        result['counter_state'] = 'boundary'
        return result
    if previous.active_path in {'primary', 'backup'} and current.active_path in {'primary', 'backup'}:
        if previous.active_path != current.active_path:
            result['backup_transition'] = previous.active_path + '_to_' + current.active_path
        elif current.active_path == 'backup':
            result['observed_backup_seconds'] = elapsed  # interval evidence, not continuous duration proof
    old = {s.device_id: s.state for s in previous.stations}
    new = {s.device_id: s.state for s in current.stations}
    for ident in sorted(old.keys() & new.keys()):
        if old[ident] != new[ident] and new[ident] in {'up', 'down'}:
            result['subscriber_events'].append({'device_id': ident, 'state': new[ident], 'basis': 'explicit_state'})
    if previous.station_table_complete and current.station_table_complete:
        for ident in sorted(old.keys() - new.keys()):
            result['subscriber_events'].append({'device_id': ident, 'state': 'absent', 'basis': 'complete_table_membership_only'})
    a, b = previous.metrics.uptime_seconds, current.metrics.uptime_seconds
    if a is None or b is None:
        result['counter_state'] = 'uptime_unknown'
        return result
    if b < a:
        result['counter_state'] = 'restart'
        return result
    result['counter_state'] = 'continuous'
    for direction in ('tx', 'rx'):
        before, after = getattr(previous.metrics, direction+'_bytes'), getattr(current.metrics, direction+'_bytes')
        if before is not None and after is not None:
            if after < before:
                result['counter_state'] = 'reset_or_wrap'
            else:
                result['rates_mbps'][direction] = (after-before)*8/elapsed/1e6
    return result


def history(db, device_id, *, now, limit=120):
    if not 1 <= limit <= 500:
        raise ValueError('History limit exceeded')
    if db.get(Device, device_id) is None:
        raise LookupError('Device not found')
    rows = db.scalars(select(TelemetrySample).where(TelemetrySample.device_id == device_id,
        TelemetrySample.raw_data[KEY].is_not(None)).order_by(TelemetrySample.sampled_at.desc(), TelemetrySample.id.desc()).limit(limit)).all()
    observations, invalid = [], 0
    for row in reversed(rows):
        try:
            o = Observation.model_validate(row.raw_data[KEY])
            if o.observed_at != _utc(row.sampled_at):
                raise ValueError('Timestamp mismatch')
            if observations and o.observed_at <= observations[-1].observed_at:
                raise ValueError('Duplicate or out-of-order history')
            observations.append(o)
        except (ValueError, TypeError):
            invalid += 1
    latest = observations[-1] if observations else None
    summary = analyze(observations[-2] if len(observations)>1 else None, latest, now=now) if latest else {'availability': 'unknown'}
    if invalid:
        summary = {'availability': 'unknown', 'reason': 'invalid_history'}
    # At least three consecutive actual UP samples on primary, bounded cadence.
    if latest and len(observations)>=3 and not invalid and summary.get('freshness')=='fresh':
        tail = observations[-3:]
        summary['recovery_verified'] = len({(o.model,o.role,o.link_id,o.remote_device_id,o.ap_device_id) for o in tail}) == 1 and all(o.availability=='up' and o.active_path=='primary' for o in tail) and all(
            0 < (b.observed_at-a.observed_at).total_seconds() <= 600 for a,b in zip(tail,tail[1:]))
    return {'device_id': device_id, 'native_compatibility': 'BLOCKED', 'read_only': True,
            'samples': [o.model_dump(mode='json') for o in observations], 'invalid_samples': invalid, 'analysis': summary}


class Weather(Closed):
    source_reference: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$')
    observed_at: AwareDatetime
    rainfall_mm_hour: float | None = Field(None, ge=0, le=1000, strict=True, allow_inf_nan=False)
    severe_weather: bool | None = Field(None, strict=True)


def weather_advisory(observation, weather):
    aligned = abs((observation.observed_at-weather.observed_at).total_seconds()) <= 300
    return {'time_aligned': aligned, 'source_reference': weather.source_reference,
            'weather_at': weather.observed_at.isoformat(), 'rf_at': observation.observed_at.isoformat(),
            'rainfall_mm_hour': weather.rainfall_mm_hour if aligned else None,
            'conclusion': 'correlation_only_cause_unverified' if aligned else 'insufficient_time_alignment'}


def projection(observation):
    """Preview for existing RF/Grafana consumers; never automatically published."""
    metrics = observation.metrics.model_dump(exclude_none=True)
    return {'scope': 'synthetic_preview_only', 'rf_metrics_v1': normalize_rf_metrics(metrics),
            'directional_capacity': {k: metrics.get(k) for k in ('tx_capacity_mbps', 'rx_capacity_mbps')},
            'internet_capacity_mbps': None, 'unsupported': sorted(set(Metrics.model_fields)-set(metrics))}
