"""Offline regression checks for Compose port exposure policy."""
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_compose_network.py"
spec = importlib.util.spec_from_file_location("validate_compose_network", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def safe_config():
    return {"services": {
        "db": {}, "redis": {}, "worker": {},
        "backend": {"ports": [{"host_ip": "127.0.0.1", "published": "8000", "target": 8000}]},
        "frontend": {"ports": [{"host_ip": "127.0.0.1", "published": "8090", "target": 80}]},
    }}


def test_private_compose_passes():
    assert module.validate(safe_config()) == []


def test_public_backend_binding_fails():
    config = safe_config()
    config["services"]["backend"]["ports"][0]["host_ip"] = "0.0.0.0"
    assert module.validate(config)


def test_missing_host_ip_fails():
    config = safe_config()
    del config["services"]["frontend"]["ports"][0]["host_ip"]
    assert module.validate(config)


def test_internal_services_must_not_publish_ports():
    for service in ("db", "redis", "worker"):
        config = safe_config()
        config["services"][service]["ports"] = [{"host_ip": "127.0.0.1", "published": "5432"}]
        assert module.validate(config)


def test_missing_required_service_fails():
    config = safe_config()
    del config["services"]["redis"]
    assert module.validate(config)
