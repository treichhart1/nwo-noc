"""Test the combined offline deployment audit without real services."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("deployment_readiness_audit", ROOT / "scripts" / "deployment_readiness_audit.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def safe_compose():
    return {"services": {
        "db": {}, "redis": {}, "worker": {},
        "backend": {"ports": [{"host_ip": "127.0.0.1"}]},
        "frontend": {"ports": [{"host_ip": "127.0.0.1"}]},
    }}


def safe_env():
    return {
        "APP_ENV": "production", "BIND_ADDRESS": "127.0.0.1",
        "API_DOCS_ENABLED": "false", "RF_COLLECTION_ENABLED": "false",
        "RF_HISTORY_PRUNING_ENABLED": "false", "UISP_VERIFY_TLS": "true",
        "SECRET_KEY": "test-only-long-random-placeholder-not-real-123456",
        "ADMIN_PASSWORD": "test-only-admin-password",
        "POSTGRES_PASSWORD": "test-only-postgres-password",
        "CORS_ORIGINS": "http://127.0.0.1:8090",
    }


def test_safe_offline_configuration_is_reviewable_not_deployable():
    report = module.audit(safe_compose(), safe_env())
    assert report["ready_for_configuration_review"]
    assert report["limitations"]
    assert "Staging acceptance" in report["limitations"][-1]


def test_public_port_blocks_review():
    compose = safe_compose()
    compose["services"]["backend"]["ports"][0]["host_ip"] = "0.0.0.0"
    report = module.audit(compose, safe_env())
    assert not report["ready_for_configuration_review"]
    assert not report["checks"]["compose_network_exposure"]["passed"]


def test_example_credentials_block_review():
    env = safe_env()
    env["ADMIN_PASSWORD"] = "ChangeThisPassword123!"
    report = module.audit(safe_compose(), env)
    assert not report["ready_for_configuration_review"]
    assert not report["checks"]["deployment_environment"]["passed"]
