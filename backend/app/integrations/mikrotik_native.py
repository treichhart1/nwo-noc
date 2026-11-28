"""RouterOS >=6.43/7 API-SSL, fixed print commands only; disabled by default.

Protocol: https://help.mikrotik.com/docs/spaces/ROS/pages/47579160/API
No certificate-less/cleartext API, legacy MD5 login, arbitrary commands or writes.
"""
import ipaddress
import re
import socket
import ssl
import time
from types import MappingProxyType
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from typing import Literal
from app.integrations.mikrotik_advanced import ADVANCED_READS, GROUPS, bounded_rows, collect_optional, monitor_sentence
from app.integrations.read_only import IntegrationFailure

READS = MappingProxyType({
    "resource": ("/system/resource/print", "version,cpu-load,total-memory,free-memory,uptime"),
    "identity": ("/system/identity/print", "name"),
    "interfaces": ("/interface/print", ".id,name,running,disabled,rx-byte,tx-byte,rx-error,tx-error,rx-drop,tx-drop"),
    "health": ("/system/health/print", "name,value,type,temperature,cpu-temperature"),
    "neighbors": ("/routing/ospf/neighbor/print", "router-id,state"),
})
READ_SENTENCES = frozenset((path, "=.proplist=" + props) for path, props in READS.values())


class NativeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    enabled: bool = False
    admin_approved: bool = False
    device_id: int = Field(ge=1, strict=True)
    address: str
    allowed_addresses: tuple[str, ...]
    ca_file: str = Field(repr=False)
    credentials_file: str = Field(repr=False, exclude=True)
    timeout_seconds: float = Field(3, ge=.2, le=3, allow_inf_nan=False)
    min_interval_seconds: int = Field(60, ge=60, le=86400)
    collect_ospf: bool = False
    advanced_groups: tuple[Literal['router', 'switch', 'tunnels', 'ipam'], ...] = Field(default=(), max_length=4)
    ethernet_monitor_ids: tuple[str, ...] = Field(default=(), max_length=8)
    poe_monitor_ids: tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator('ethernet_monitor_ids', 'poe_monitor_ids')
    @classmethod
    def interface_ids(cls, value):
        if len(value) != len(set(value)) or any(not re.fullmatch(r'\*[0-9A-Fa-f]{1,8}', v) for v in value):
            raise ValueError('Explicit bounded RouterOS interface IDs required')
        return value

    # Hardware/API units differ: never guess Celsius from magnitude.
    temperature_unit: str | None = Field(None, pattern=r"^(celsius|decicelsius)$")

    def validate_connection(self):
        if not self.enabled or not self.admin_approved:
            raise IntegrationFailure("disabled")
        try:
            address = ipaddress.ip_address(self.address)
            allowed = {ipaddress.ip_address(a) for a in self.allowed_addresses}
            if (address.version != 4 or address not in allowed or address.is_loopback
                    or address.is_multicast or address.is_unspecified or address.is_link_local
                    or int(address) == 0xffffffff or not self.ca_file.startswith("/")
                    or not self.credentials_file.startswith("/")):
                raise ValueError()
        except ValueError:
            raise IntegrationFailure("endpoint_denied") from None
        return str(address)


class NativeCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    username: SecretStr = Field(repr=False)
    password: SecretStr = Field(repr=False)

    def validate_values(self):
        user, password = self.username.get_secret_value(), self.password.get_secret_value()
        if (not 1 <= len(user.encode()) <= 64 or not 8 <= len(password.encode()) <= 128
                or any(ord(c) < 32 or ord(c) == 127 for c in user)):
            raise IntegrationFailure("configuration_invalid")
        return user, password


def require_native_version(payload):
    from app.integrations.mikrotik import one
    resource = one(payload)
    version = resource.get("version")
    m = re.match(r"^(6|7)\.(\d+)(?:\.|\s|$)", version.strip()) if isinstance(version, str) else None
    if not m or (int(m[1]) == 6 and int(m[2]) < 43):
        raise IntegrationFailure("unsupported")
    return resource


def encode_sentence(words):
    result = bytearray()
    for word in words:
        data = word.encode("utf-8")
        size = len(data)
        if not 0 < size <= 65535:
            raise IntegrationFailure("limit_exceeded")
        if size < 0x80:
            result.append(size)
        elif size < 0x4000:
            result.extend((size | 0x8000).to_bytes(2, "big"))
        else:
            result.extend((size | 0xc00000).to_bytes(3, "big"))
        result.extend(data)
    result.append(0)
    return bytes(result)


class NativeClient:
    """One bounded, verified TLS session; caller supplies durable pacing/fencing."""
    def __init__(self, config, credentials):
        self.config, self.credentials = config, credentials
        self._socket = None
        self._remaining = 2097152
        self._deadline = 0

    def _budget(self):
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise IntegrationFailure("timeout")
        self._socket.settimeout(min(self.config.timeout_seconds, left))

    def _exact(self, size):
        if size > self._remaining:
            raise IntegrationFailure("response_too_large")
        result = bytearray()
        while len(result) < size:
            self._budget()
            chunk = self._socket.recv(size - len(result))
            if not chunk:
                raise IntegrationFailure("unavailable")
            self._remaining -= len(chunk)
            result.extend(chunk)
        return bytes(result)

    def _sentence(self):
        words = []
        for _ in range(128):
            first = self._exact(1)[0]
            if first < 0x80:
                size = first
            elif first < 0xc0:
                size = ((first & 0x3f) << 8) | int.from_bytes(self._exact(1), "big")
            elif first < 0xe0:
                size = ((first & 0x1f) << 16) | int.from_bytes(self._exact(2), "big")
            elif first < 0xf0:
                size = ((first & 0x0f) << 24) | int.from_bytes(self._exact(3), "big")
            elif first == 0xf0:
                size = int.from_bytes(self._exact(4), "big")
            else:
                raise IntegrationFailure("invalid_response")
            if size == 0:
                if not words:
                    raise IntegrationFailure("invalid_response")
                return words
            if size > 65535:
                raise IntegrationFailure("response_too_large")
            try:
                words.append(self._exact(size).decode("utf-8", errors="strict"))
            except UnicodeError:
                raise IntegrationFailure("invalid_response") from None
        raise IntegrationFailure("limit_exceeded")

    def _exchange(self, words, *, login=False):
        words = tuple(words)
        if login:
            user, password = self.credentials.validate_values()
            allowed = words == ("/login", "=name=" + user, "=password=" + password)
        else:
            allowed = words in READ_SENTENCES
            enabled = {name for group in self.config.advanced_groups for name in GROUPS[group]}
            allowed = allowed or any(words == (ADVANCED_READS[name][0], '=.proplist='+ADVANCED_READS[name][1]) for name in enabled)
            allowed = allowed or any(words == monitor_sentence(kind, ident) for kind, ids in
                [('ethernet', self.config.ethernet_monitor_ids), ('poe', self.config.poe_monitor_ids)] for ident in ids)
        if not allowed or self._socket is None:
            raise IntegrationFailure("operation_denied")
        self._budget()
        self._socket.sendall(encode_sentence(words))
        rows = []
        for _ in range(514):
            reply = self._sentence()
            state = reply[0]
            if state in {"!trap", "!fatal"}:
                # Router-provided error text may contain credentials; never propagate it.
                raise IntegrationFailure("authentication_failed" if login else "operation_denied")
            if state not in {"!re", "!done", "!empty"}:
                raise IntegrationFailure("invalid_response")
            values = {}
            for word in reply[1:]:
                if not word.startswith("=") or "=" not in word[1:]:
                    raise IntegrationFailure("invalid_response")
                key, value = word[1:].split("=", 1)
                if not key or key in values:
                    raise IntegrationFailure("invalid_response")
                if key.casefold() in {"private-key","preshared-key","password","imsi","iccid","imei","pin","sim-pin"}:
                    raise IntegrationFailure("invalid_response")
                values[key] = value
            if state == "!done":
                if "ret" in values:
                    raise IntegrationFailure("unsupported")
                if values:
                    raise IntegrationFailure("invalid_response")
                return rows
            if state == "!empty":
                if values:
                    raise IntegrationFailure("invalid_response")
                continue
            rows.append(values)
            if len(rows) > 512:
                raise IntegrationFailure("limit_exceeded")
        raise IntegrationFailure("limit_exceeded")

    def _read(self, name):
        if name not in READS:
            raise IntegrationFailure("operation_denied")
        command, properties = READS[name]
        values = self._exchange((command, "=.proplist=" + properties))
        allowed = set(properties.split(","))
        return [{k: v for k, v in row.items() if k in allowed} for row in values]

    def _read_advanced(self, name):
        path, fields = ADVANCED_READS[name]
        return bounded_rows(self._exchange((path, '=.proplist='+fields)), set(fields.split(',')), self.credentials.validate_values())

    def collect(self):
        address = self.config.validate_connection()
        user, password = self.credentials.validate_values()
        self._deadline = time.monotonic() + 30
        self._remaining = 2097152
        try:
            try:
                context = ssl.create_default_context(cafile=self.config.ca_file)
            except (OSError, ssl.SSLError):
                raise IntegrationFailure("configuration_invalid") from None
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            # create_default_context retains CERT_REQUIRED + hostname verification.
            with socket.create_connection((address, 8729), timeout=self.config.timeout_seconds) as raw:
                with context.wrap_socket(raw, server_hostname=address) as secured:
                    self._socket = secured
                    if self._exchange(("/login", "=name=" + user, "=password=" + password), login=True):
                        raise IntegrationFailure("invalid_response")
                    payload = {"resource": self._read("resource")}
                    require_native_version(payload["resource"])
                    for name in ("identity", "interfaces"):
                        payload[name] = self._read(name)
                    payload["neighbors"] = self._read("neighbors") if self.config.collect_ospf else []
                    payload["health"] = self._read("health") if self.config.temperature_unit else []
                    values = normalize_native(payload, (user, password), temperature_unit=self.config.temperature_unit)
                    if self.config.advanced_groups or self.config.ethernet_monitor_ids or self.config.poe_monitor_ids:
                        values['advanced'] = collect_optional(self, values['routeros_version'], payload['neighbors'], payload['health'] if self.config.temperature_unit else None)
                    return values
        except IntegrationFailure:
            raise
        except (TimeoutError, socket.timeout):
            raise IntegrationFailure("timeout") from None
        except ssl.SSLError:
            raise IntegrationFailure("configuration_invalid") from None
        except OSError:
            raise IntegrationFailure("unavailable") from None
        finally:
            self._socket = None


def normalize_native(payload, credentials=(), *, temperature_unit=None):
    from app.integrations.mikrotik import normalize
    from app.integrations.normalization import number
    require_native_version(payload["resource"])
    # Suppress ambiguous native health units before using the shared normalizer.
    values = normalize(payload | {"health": []}, credentials, native=True)
    if temperature_unit not in {None, "celsius", "decicelsius"}:
        raise IntegrationFailure("configuration_invalid")
    health = payload.get("health", [])
    if not isinstance(health, list) or len(health) > 512:
        raise IntegrationFailure("invalid_response")
    if temperature_unit:
        readings = []
        for row in health:
            if not isinstance(row, dict):
                raise IntegrationFailure("invalid_response")
            if row.get("name") in {"temperature", "cpu-temperature"}:
                readings.append(row.get("value"))
            readings.extend(row[k] for k in ("temperature", "cpu-temperature") if k in row)
        if len(readings) == 1:
            reading = number(readings[0], -2000, 2000)
            if reading is not None:
                reading /= 10 if temperature_unit == "decicelsius" else 1
                values["temperature_c"] = reading if -100 <= reading <= 200 else None
    return values
