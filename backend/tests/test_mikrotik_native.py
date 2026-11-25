"""Only synthetic byte streams/socket pairs; never real RouterOS endpoints."""
import ipaddress
import socket
import ssl
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from test_rf_postgres_integration import postgres_db
from app.integrations.mikrotik_native import (
    NativeClient, NativeConfig, NativeCredentials, READS, READ_SENTENCES,
    encode_sentence, normalize_native, require_native_version,
)
from app.integrations.read_only import IntegrationFailure

ADDRESS = "192.0.2.121"
CANARY = "synthetic-native-password-canary"


def config(**changes):
    return NativeConfig(**({
        "device_id": 1, "enabled": True, "admin_approved": True,
        "address": ADDRESS, "allowed_addresses": [ADDRESS],
        "ca_file": "/synthetic/ca.pem", "credentials_file": "/synthetic/credentials.json",
    } | changes))


def credentials():
    return NativeCredentials(username="synthetic-reader", password=CANARY)


def payload(version="6.49.18 (long-term)"):
    return {
        "identity": [{"name": "synthetic-router"}],
        "resource": [{"version": version, "cpu-load": "23", "total-memory": "1000",
                      "free-memory": "600", "uptime": "1d02:03:04"}],
        "interfaces": [{".id": "*1", "name": "ether1", "running": "true",
                        "disabled": "false", "rx-byte": "18446744073709551615",
                        "tx-byte": "12", "rx-error": "2"}],
        "neighbors": [{"router-id": "192.0.2.2", "state": "Full"}],
        "health": [{"temperature": "430"}],
    }


class Stream:
    def __init__(self, data=b""):
        self.data, self.sent, self.timeouts = bytearray(data), [], []
    def recv(self, size):
        value = bytes(self.data[:min(size, 3)])
        del self.data[:len(value)]
        return value
    def sendall(self, data): self.sent.append(data)
    def settimeout(self, value): self.timeouts.append(value)


def ready(stream):
    client = NativeClient(config(), credentials())
    client._socket, client._deadline = stream, time.monotonic() + 30
    return client


@pytest.mark.parametrize("version", ["6.43", "6.49.18 (long-term)", "7.20.2 (stable)"])
def test_native_normalization_versions_and_exact_counters(version):
    value = normalize_native(payload(version))
    assert value["cpu_percent"] == 23 and value["ram_percent"] == 40
    assert value["interfaces"][0]["rx_bytes"] == 2**64 - 1
    assert value["uptime_seconds"] == 93784
    assert value["temperature_c"] is None  # Units not approved; never guessed.
    assert value["ospf_neighbors"][0]["state"] == "Full"


@pytest.mark.parametrize("version", ["6.42.12", "5.26", "8.1", "", None, "secret-6.49"])
def test_unsupported_versions_fail_without_fabricating_support(version):
    with pytest.raises(IntegrationFailure) as e:
        require_native_version([{"version": version}])
    assert e.value.code == "unsupported"


@pytest.mark.parametrize("health,unit,expected", [
    ([{"temperature": "430"}], "decicelsius", 43),
    ([{"name": "cpu-temperature", "value": "43"}], "celsius", 43),
    ([{"temperature": "430"}], "celsius", None),
    ([{"temperature": "43"}, {"temperature": "44"}], "celsius", None),
    ([], "decicelsius", None),
])
def test_explicit_health_units_and_ambiguous_sensors(health, unit, expected):
    data = payload() | {"health": health}
    assert normalize_native(data, temperature_unit=unit)["temperature_c"] == expected


@pytest.mark.parametrize("change,code", [
    ({"enabled": False}, "disabled"), ({"admin_approved": False}, "disabled"),
    ({"allowed_addresses": ["192.0.2.2"]}, "endpoint_denied"),
    ({"address": "router.synthetic.invalid"}, "endpoint_denied"),
    ({"address": "127.0.0.1", "allowed_addresses": ["127.0.0.1"]}, "endpoint_denied"),
    ({"ca_file": "relative.pem"}, "endpoint_denied"),
])
def test_denied_configuration_never_opens_socket(monkeypatch, change, code):
    def forbidden(*args, **kw): raise AssertionError("must not connect")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    with pytest.raises(IntegrationFailure) as e:
        NativeClient(config(**change), credentials()).collect()
    assert e.value.code == code


def test_no_cleartext_write_query_or_arbitrary_property_surface():
    assert all(path.endswith("/print") for path, _ in READS.values())
    stream = Stream()
    client = ready(stream)
    for words in [
        ("/system/reboot",), ("/interface/set", "=.id=*1", "=disabled=yes"),
        ("/system/script/run",), ("/interface/print", "=detail="),
        ("/interface/print", "?name=ether1"),
        ("/login", "=name=attacker", "=password=" + CANARY),
    ]:
        with pytest.raises(IntegrationFailure) as e:
            client._exchange(words, login=words[0] == "/login")
        assert e.value.code == "operation_denied"
    assert stream.sent == []


@pytest.mark.parametrize("size", [1, 127, 128, 16383, 16384, 65535])
def test_fragmented_wire_length_boundaries(size):
    words = ["!re", "=value=" + "x" * max(0, size - 7)]
    stream = Stream(encode_sentence(words))
    assert ready(stream)._sentence() == words


@pytest.mark.parametrize("data,code", [
    (b"\xf8", "invalid_response"), (b"\xf0\x00\x01\x00\x00", "response_too_large"),
    (b"\x03ab", "unavailable"), (b"\x01\xff\x00", "invalid_response"),
    (b"\x00", "invalid_response"),
])
def test_malformed_and_incomplete_wire(data, code):
    with pytest.raises(IntegrationFailure) as e:
        ready(Stream(data))._sentence()
    assert e.value.code == code


@pytest.mark.parametrize("reply,login,code", [
    (["!trap", "=message=" + CANARY], True, "authentication_failed"),
    (["!trap", "=message=" + CANARY], False, "operation_denied"),
    (["!fatal", "=message=" + CANARY], False, "operation_denied"),
    (["!done", "=ret=legacy-challenge"], True, "unsupported"),
    (["!re", "=name=a", "=name=b"], False, "invalid_response"),
])
def test_static_errors_do_not_leak_raw_reply(reply, login, code, caplog):
    client = ready(Stream(encode_sentence(reply)))
    words = ("/login", "=name=synthetic-reader", "=password=" + CANARY) if login else next(iter(READ_SENTENCES))
    with pytest.raises(IntegrationFailure) as e:
        client._exchange(words, login=login)
    assert e.value.code == code and CANARY not in str(e.value)
    assert CANARY not in caplog.text and CANARY not in repr(credentials())


def test_response_limits_and_timeout():
    client = ready(Stream(encode_sentence(["!re", "=name=x"]) * 513))
    with pytest.raises(IntegrationFailure) as e:
        client._read("identity")
    assert e.value.code == "limit_exceeded"
    client = ready(Stream())
    client._deadline = time.monotonic() - 1
    with pytest.raises(IntegrationFailure) as e:
        client._read("resource")
    assert e.value.code == "timeout"


def test_native_activation_requires_scheduler_and_excludes_rest():
    from app.core.config import Settings
    values = {
        "secret_key": "synthetic-settings-only-key-32-characters",
        "database_url": "sqlite:///:memory:", "admin_password": "synthetic-local-test-password",
        "app_env": "staging", "offline_mode": False, "integrations_admin_approved": True,
        "mikrotik_enabled": True, "mikrotik_device_id": 1,
        "mikrotik_native_config_file": "/synthetic/native.json", "rf_scheduler_enabled": True,
    }
    assert Settings(**values).mikrotik_native_config_file == "/synthetic/native.json"
    for changes in [{"rf_scheduler_enabled": False}, {"offline_mode": True},
                    {"integrations_admin_approved": False}, {"mikrotik_username": "mixed-rest"},
                    {"mikrotik_base_url": "https://192.0.2.1"}]:
        with pytest.raises(ValidationError):
            Settings(**(values | changes))


@pytest.mark.parametrize("untrusted", [False, True])
def test_actual_tls_socketpair_verification_and_fixed_reads(tmp_path, monkeypatch, untrusted):
    """Real TLS on AF_UNIX socketpair; no network address is contacted."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic NOC121 TLS")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ADDRESS))]), critical=False)
            .sign(key, hashes.SHA256()))
    certpath, keypath = tmp_path / "ca.pem", tmp_path / "key.pem"
    certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    keypath.chmod(0o600)
    left, right = socket.socketpair()
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(certpath, keypath)
    commands, errors = [], []

    def serve():
        try:
            with right, server.wrap_socket(right, server_side=True) as secured:
                parser = ready(secured)
                login = parser._sentence()
                commands.append(login[0])
                assert login == ["/login", "=name=synthetic-reader", "=password=" + CANARY]
                secured.sendall(encode_sentence(["!done"]))
                data = payload()
                for field in ("resource", "identity", "interfaces"):
                    words = parser._sentence()
                    commands.append(words[0])
                    assert tuple(words) in READ_SENTENCES
                    for row in data[field]:
                        secured.sendall(encode_sentence(["!re"] + ["=" + k + "=" + v for k, v in row.items()]))
                    secured.sendall(encode_sentence(["!done"]))
        except ssl.SSLError:
            if not untrusted: errors.append("unexpected_tls_error")
        except Exception:
            errors.append("synthetic_server_failure")

    thread = Thread(target=serve, daemon=True)
    thread.start()
    def connect(endpoint, timeout):
        assert endpoint == (ADDRESS, 8729) and timeout <= 3
        return left
    monkeypatch.setattr(socket, "create_connection", connect)
    if untrusted:
        # Retain standard CA verification but do not trust this disposable test CA.
        original = ssl.create_default_context
        monkeypatch.setattr(ssl, "create_default_context", lambda **kw: original())
    client = NativeClient(config(ca_file=str(certpath)), credentials())
    try:
        if untrusted:
            with pytest.raises(IntegrationFailure) as e: client.collect()
            assert e.value.code == "configuration_invalid" and commands == []
        else:
            values = client.collect()
            assert values["cpu_percent"] == 23 and values["temperature_c"] is None
            assert commands == ["/login", "/system/resource/print", "/system/identity/print", "/interface/print"]
    finally:
        left.close()
        right.close()
        thread.join(timeout=5)
    assert not thread.is_alive() and not errors


def test_native_scheduler_uses_existing_telemetry_and_private_config(postgres_db, monkeypatch, tmp_path):
    import json
    from sqlalchemy import select, func
    from app.core.config import settings
    from app.models.entities import Device, TelemetrySample, MonitoringReceipt, MonitoringJob
    from app.services.monitoring_scheduler import enroll, claim, execute

    db = postgres_db
    device = Device(source="synthetic", external_id="noc121-native",
                    name="Synthetic native", vendor="MikroTik", ip_address=ADDRESS,
                    enabled=True, authorized=True)
    db.add(device)
    db.flush()
    private = tmp_path / "credentials.json"
    private.write_text(json.dumps({"username": "synthetic-reader", "password": CANARY}))
    private.chmod(0o600)
    configfile = tmp_path / "native.json"
    configfile.write_text(config(device_id=device.id, credentials_file=str(private)).model_dump_json())
    # credentials_file is intentionally excluded from dumps; write explicit private input.
    data = json.loads(configfile.read_text())
    data["credentials_file"] = str(private)
    configfile.write_text(json.dumps(data))
    configfile.chmod(0o600)
    for k, v in {"offline_mode": False, "integrations_admin_approved": True,
                 "mikrotik_enabled": True, "rf_scheduler_enabled": True, "mikrotik_device_id": device.id,
                 "mikrotik_native_config_file": str(configfile)}.items():
        monkeypatch.setattr(settings, k, v)
    observed = []
    def fixture_collect(client):
        assert client.config.device_id == device.id
        assert client.credentials.password.get_secret_value() == CANARY
        observed.append(True)
        return normalize_native(payload())
    monkeypatch.setattr(NativeClient, "collect", fixture_collect)
    now = datetime.now(timezone.utc)
    enroll(db, {"kind": "mikrotik", "device_id": device.id,
                "interval_seconds": 60, "timeout_seconds": 40}, now=now)
    pair = claim(db, "synthetic-native-worker", now=now)[0]
    result = execute(db, *pair, now=now, spool_dir=tmp_path / "spool")
    assert result["result"] == "success" and len(observed) == 1
    row = db.scalar(select(TelemetrySample))
    assert row.raw_data["mikrotik_v1"]["interfaces"][0]["rx_bytes"] == 2**64-1
    assert row.temperature_c is None
    assert db.scalar(select(func.count(MonitoringReceipt.id))) == 1
    # Conflicting config/device association is denied before any transport call.
    from app.services.monitoring_collectors import collect
    data["device_id"] = device.id + 1
    configfile.write_text(json.dumps(data))
    with pytest.raises(IntegrationFailure) as e:
        collect(db, db.get(MonitoringJob, pair[0]), now)
    assert e.value.code == "endpoint_denied" and len(observed) == 1

