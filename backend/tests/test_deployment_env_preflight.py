"""Read-only deployment environment preflight regression coverage."""
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_deployment_env.py"
spec = importlib.util.spec_from_file_location("deployment_env_preflight", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def safe_env():
    return {
        "APP_ENV": "production",
        "BIND_ADDRESS": "127.0.0.1",
        "API_DOCS_ENABLED": "false",
        "RF_COLLECTION_ENABLED": "false",
        "RF_HISTORY_PRUNING_ENABLED": "false",
        "UISP_VERIFY_TLS": "true",
        "SECRET_KEY": "test-only-unique-key-longer-than-32-characters",
        "ADMIN_PASSWORD": "not-a-real-password-test-only",
        "POSTGRES_PASSWORD": "not-a-real-database-password",
        "CORS_ORIGINS": "http://127.0.0.1:8090",
    }


def test_safe_environment_passes():
    assert module.validate(safe_env()) == []


def test_example_env_is_rejected():
    example = Path(__file__).resolve().parents[2] / ".env.example"
    failures = module.validate(module.parse_env(example.read_text(encoding="utf-8")))
    assert failures


def test_unsafe_overrides_fail():
    for key, value in (
        ("APP_ENV", "development"),
        ("BIND_ADDRESS", "0.0.0.0"),
        ("API_DOCS_ENABLED", "true"),
        ("RF_COLLECTION_ENABLED", "true"),
        ("RF_HISTORY_PRUNING_ENABLED", "true"),
        ("UISP_VERIFY_TLS", "false"),
        ("SECRET_KEY", "short"),
        ("ADMIN_PASSWORD", "ChangeThisPassword123!"),
        ("POSTGRES_PASSWORD", "replace-database-password"),
        ("CORS_ORIGINS", "*"),
    ):
        config = safe_env()
        config[key] = value
        assert module.validate(config), key


def test_env_parser_ignores_comments_and_quotes():
    assert module.parse_env('# ignored\nAPP_ENV="production"\nBIND_ADDRESS=127.0.0.1\n') == {
        "APP_ENV": "production",
        "BIND_ADDRESS": "127.0.0.1",
    }
