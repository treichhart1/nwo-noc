"""Offline staging isolation lint regression tests."""
import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("validate_staging_isolation", SCRIPTS / "validate_staging_isolation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def compose():
    return {"services": {
        "db": {}, "redis": {}, "worker": {},
        "backend": {"ports": [{"host_ip": "127.0.0.1"}]},
        "frontend": {"ports": [{"host_ip": "127.0.0.1"}]},
    }}


def env():
    return {
        "APP_ENV": "staging", "BIND_ADDRESS": "127.0.0.1",
        "RF_COLLECTION_ENABLED": "false", "RF_HISTORY_PRUNING_ENABLED": "false",
        "API_DOCS_ENABLED": "false", "UISP_TOKEN": "",
        "UISP_VERIFY_TLS": "true", "CORS_ORIGINS": "http://127.0.0.1:8090",
        "DATABASE_URL": "postgresql+psycopg://user:test@db:5432/staging",
        "REDIS_URL": "redis://redis:6379/0",
    }


def test_isolated_static_configuration_passes():
    assert module.validate(compose(), env()) == []


def test_unsafe_environment_fails():
    for key, value in (
        ("APP_ENV", "production"), ("BIND_ADDRESS", "0.0.0.0"),
        ("RF_COLLECTION_ENABLED", "true"), ("RF_HISTORY_PRUNING_ENABLED", "true"),
        ("API_DOCS_ENABLED", "true"), ("UISP_TOKEN", "live-token"),
        ("UISP_VERIFY_TLS", "false"), ("CORS_ORIGINS", "*"),
        ("DATABASE_URL", "postgresql://user:pass@10.41.1.10/db"),
        ("REDIS_URL", "redis://10.210.0.20:6379/0"),
    ):
        sample = env()
        sample[key] = value
        assert module.validate(compose(), sample), key


def test_public_ports_and_internal_published_ports_fail():
    sample = compose()
    sample["services"]["backend"]["ports"][0]["host_ip"] = "0.0.0.0"
    assert module.validate(sample, env())
    sample = compose()
    sample["services"]["redis"]["ports"] = [{"host_ip": "127.0.0.1"}]
    assert module.validate(sample, env())
