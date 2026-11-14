"""Explicitly mapped SNMPv3 authPriv GET only; no walk, SET or discovery API."""
import asyncio
import hashlib
import ipaddress
import json
import math
import time
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from app.integrations.read_only import IntegrationFailure
from app.services.radio_observation import PROFILES, ENUMS, RadioMetrics, RadioCounters, RadioObservation

BASE = {'rssi_dbm', 'snr_db', 'noise_floor_dbm', 'frequency_mhz', 'channel_width_mhz',
        'tx_rate_mbps', 'rx_rate_mbps', 'link_capacity_mbps', 'uptime_seconds', 'radio_status',
        'ccq_percent', 'tx_errors', 'rx_errors', 'retries', 'disconnects'}
CAPABILITIES = {
    'airmax-ac-mib-v1': BASE | {'remote_rssi_dbm', 'gps_sync', 'tx_throughput_mbps', 'rx_throughput_mbps','utilization_percent'},
    'airmax-legacy-mib-v1': BASE - {'link_capacity_mbps'},
    'epmp-4600-mib-v1': BASE | {'remote_rssi_dbm', 'mcs_index', 'modulation', 'subscriber_count', 'temperature_c', 'tx_throughput_mbps', 'rx_throughput_mbps','utilization_percent'},
    'epmp-legacy-mib-v1': BASE | {'modulation', 'subscriber_count'},
}
UNITS = {'dbm': 1, 'db': 1, 'percent': 1, 'mhz': 1, 'hz': 1e-6, 'khz': .001,
         'mbps': 1, 'bits_per_second': 1e-6, 'kbps': .001, 'seconds': 1,
         'centiseconds': .01, 'celsius': 1, 'count': 1, 'enum': 1}


def valid_units(field):
    if field in ENUMS: return {'enum'}
    if field in RadioCounters.model_fields or field in {'mcs_index', 'subscriber_count'}: return {'count'}
    if field.endswith('_dbm'): return {'dbm'}
    if field.endswith('_db'): return {'db'}
    if field.endswith('_percent'): return {'percent'}
    if field.endswith('_mhz'): return {'mhz', 'hz', 'khz'}
    if field.endswith('_mbps'): return {'mbps', 'bits_per_second', 'kbps'}
    return {'seconds', 'centiseconds'} if field == 'uptime_seconds' else {'celsius'}


class MIBField(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    oid: str = Field(pattern=r'^1\.3\.6\.1\.(?:[0-9]{1,10}\.)*[0-9]{1,10}$', max_length=128)
    unit: Literal['dbm', 'db', 'percent', 'mhz', 'hz', 'khz', 'mbps', 'bits_per_second', 'kbps',
                  'seconds', 'centiseconds', 'celsius', 'count', 'enum']
    states: dict[str, str] = Field(default_factory=dict)


class MIBMapping(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    profile: Literal['airmax-ac-mib-v1', 'airmax-legacy-mib-v1', 'epmp-4600-mib-v1', 'epmp-legacy-mib-v1']
    mib_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    fields: dict[str, MIBField] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def validate_fields(self):
        if set(self.fields) - CAPABILITIES[self.profile]: raise ValueError('Unsupported profile capability')
        if len({f.oid for f in self.fields.values()}) != len(self.fields): raise ValueError('Duplicate OID mapping')
        for name, field in self.fields.items():
            if field.unit not in valid_units(name): raise ValueError('Incompatible RF unit')
            if name in ENUMS:
                if not field.states or not set(field.states.values()) <= ENUMS[name]: raise ValueError('Invalid enumeration mapping')
                if any(not k.isascii() or not k.isdigit() or len(k) > 5 for k in field.states): raise ValueError('Only numeric wire enumerations supported')
            elif field.states: raise ValueError('Unexpected enumeration mapping')
            if any(int(part) > 4294967295 for part in field.oid.split('.')): raise ValueError('Invalid OID arc')
        return self

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class SNMPCredentials(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    username: SecretStr = Field(repr=False)
    auth_key: SecretStr = Field(repr=False)
    priv_key: SecretStr = Field(repr=False)

    @model_validator(mode='after')
    def secure_keys(self):
        if not 1 <= len(self.username.get_secret_value()) <= 32 or any(not 8 <= len(s.get_secret_value()) <= 128 for s in (self.auth_key, self.priv_key)):
            raise ValueError('SNMPv3 authPriv credentials required')
        return self


class SNMPPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    enabled: bool = False
    admin_approved: bool = False
    mapping_validated: bool = False
    address: str
    allowed_addresses: tuple[str, ...]
    timeout_seconds: float = Field(2, ge=.2, le=3, allow_inf_nan=False)
    retries: int = Field(1, ge=0, le=2)
    min_interval_seconds: float = Field(5, ge=5, le=3600, allow_inf_nan=False)

    def validate_connection(self):
        if not self.enabled or not self.admin_approved: raise IntegrationFailure('disabled')
        if not self.mapping_validated: raise IntegrationFailure('configuration_invalid')
        try:
            address = ipaddress.ip_address(self.address)
            allowed = {ipaddress.ip_address(a) for a in self.allowed_addresses}
            if address.version != 4 or address not in allowed or address.is_multicast or address.is_unspecified or address.is_loopback or address.is_link_local or int(address) == 0xffffffff:
                raise ValueError()
        except ValueError: raise IntegrationFailure('endpoint_denied') from None
        return str(address)


class PysnmpGET:
    """No public operation argument: only get_cmd is imported/called."""
    def __init__(self, *, version_oids=()):
        self.version_oids = frozenset(version_oids)

    async def get(self, address, credentials, oids, timeout):
        from pysnmp.hlapi.v3arch.asyncio import (SnmpEngine, UsmUserData, UdpTransportTarget, ContextData,
            ObjectType, ObjectIdentity, get_cmd, usmHMAC192SHA256AuthProtocol, usmAesCfb128Protocol)
        from pysnmp.proto.rfc1905 import NoSuchObject, NoSuchInstance, EndOfMibView
        from pysnmp.proto.rfc1902 import Integer, Integer32, Counter32, Counter64, Gauge32, TimeTicks, Unsigned32
        engine = SnmpEngine()
        try:
            user = UsmUserData(credentials.username.get_secret_value(), credentials.auth_key.get_secret_value(),
                credentials.priv_key.get_secret_value(), authProtocol=usmHMAC192SHA256AuthProtocol, privProtocol=usmAesCfb128Protocol)
            target = await UdpTransportTarget.create((address, 161), timeout=timeout, retries=0)
            error, status, _, bindings = await get_cmd(engine, user, target, ContextData(),
                *(ObjectType(ObjectIdentity(oid)) for oid in oids), lookupMib=False)
            if error:
                # Exception/error text can contain security names: never propagate it.
                from pysnmp.proto import errind
                if isinstance(error, errind.RequestTimedOut): raise IntegrationFailure('timeout')
                raise IntegrationFailure('authentication_failed')
            if status: raise IntegrationFailure('unavailable')
            result = {}
            for oid, value in bindings:
                key = str(oid)
                if isinstance(value, (NoSuchObject, NoSuchInstance, EndOfMibView)): result[key] = 'unsupported'
                elif isinstance(value, (Integer, Integer32, Counter32, Counter64, Gauge32, TimeTicks, Unsigned32)): result[key] = int(value)
                elif key in self.version_oids:
                    # Only explicit firmware OIDs can return a restricted version
                    # scalar. Never retain arbitrary names, credentials or JSON.
                    import re
                    try: text = value.asOctets().decode('ascii') if len(value.asOctets()) <= 40 else ''
                    except (AttributeError, UnicodeError): text = ''
                    result[key] = text if re.fullmatch(r'\d{1,4}(?:\.\d{1,4}){1,4}(?:-(?:rc|build)\d{1,6})?', text) else None
                else: result[key] = None  # No arbitrary OctetStrings retained.
            return result
        finally:
            engine.close_dispatcher()


class RadioCollector:
    def __init__(self, mapping, policy, *, transport=None, clock=time.monotonic, sleep=asyncio.sleep):
        self.mapping, self.policy = mapping, policy
        self.transport = transport or PysnmpGET()
        self.clock, self.sleep, self.last_attempt = clock, sleep, None
        self.lock = asyncio.Lock()

    def normalize(self, values, *, observed_at, received_at, source_kind='synthetic_export'):
        if not isinstance(values, dict) or len(values) > 64: raise IntegrationFailure('invalid_response')
        metrics, counters, enums, originals, unavailable = {}, {}, {}, {}, {}
        fields = set(RadioMetrics.model_fields) | set(RadioCounters.model_fields) | set(ENUMS)
        for name in fields:
            spec = self.mapping.fields.get(name)
            if spec is None: unavailable[name] = 'unsupported'; continue
            if spec.oid not in values: unavailable[name] = 'missing'; continue
            raw = values[spec.oid]
            if raw == 'unsupported': unavailable[name] = 'unsupported'; continue
            try: finite = isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw)
            except OverflowError: finite = False
            if not finite:
                unavailable[name] = 'invalid'; continue
            if name in ENUMS:
                state = spec.states.get(str(raw)) if isinstance(raw, int) else None
                if state is None: unavailable[name] = 'invalid'; continue
                enums[name], originals[name] = state, raw
                continue
            value = raw * UNITS[spec.unit]
            try:
                model = RadioCounters if name in RadioCounters.model_fields else RadioMetrics
                if spec.unit == 'count':
                    if not isinstance(raw, int): raise ValueError()
                    value = raw
                validated = getattr(model(**{name: value}), name)
            except (ValueError, TypeError, OverflowError): unavailable[name] = 'invalid'; continue
            (counters if model == RadioCounters else metrics)[name] = validated
            originals[name] = raw
        try:
            return RadioObservation(vendor=PROFILES[self.mapping.profile], profile=self.mapping.profile,
                mapping_digest=self.mapping.digest, source_kind=source_kind, observed_at=observed_at, received_at=received_at,
                timestamp_basis='collector_receipt' if source_kind == 'snmpv3_read_only' else 'source_export',
                measurements=RadioMetrics(**metrics), counters=RadioCounters(**counters), original_values=originals,
                original_units={k:self.mapping.fields[k].unit for k in originals},
                unavailable=unavailable, **enums)
        except (ValueError, TypeError): raise IntegrationFailure('invalid_response') from None

    async def collect(self, credentials):
        from app.services.integration_interlock import disabled
        if disabled():raise IntegrationFailure('disabled')
        address = self.policy.validate_connection()  # Before socket, sleep or credential use.
        oids = tuple(f.oid for f in self.mapping.fields.values())
        async with self.lock:
            if self.last_attempt is not None and self.clock() - self.last_attempt < self.policy.min_interval_seconds:
                raise IntegrationFailure('rate_limited')
            self.last_attempt = self.clock()
            for attempt in range(self.policy.retries + 1):
                try:
                    if disabled():raise IntegrationFailure('disabled')
                    values = await asyncio.wait_for(self.transport.get(address, credentials, oids, self.policy.timeout_seconds),
                                                    timeout=self.policy.timeout_seconds + .5)
                    now = datetime.now(timezone.utc)
                    return self.normalize(values, observed_at=now, received_at=now, source_kind='snmpv3_read_only')
                except (TimeoutError, IntegrationFailure) as error:
                    code = 'timeout' if isinstance(error, TimeoutError) else error.code
                    if code not in {'timeout', 'unavailable'} or attempt == self.policy.retries: raise IntegrationFailure(code) from None
                    await self.sleep(.2 * 2**attempt)
                except Exception: raise IntegrationFailure('unavailable') from None
