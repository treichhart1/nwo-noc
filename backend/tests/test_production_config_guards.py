"""Production startup configuration must fail closed on unsafe settings."""
import pytest
from pydantic import ValidationError

from app.core.config import Settings


BASE = dict(
    _env_file=None,
    app_env="production",
    secret_key="valid-test-only-secret-key-at-least-32-characters",
    database_url="postgresql+psycopg://user:password@localhost:5432/test",
    admin_password="unique-test-only-admin-password",
    cors_origins="http://127.0.0.1:8090",
)


@pytest.mark.parametrize("override", [
    {"uisp_verify_tls": False},
    {"api_docs_enabled": True},
    {"secret_key": "short"},
    {"secret_key": "replace-with-a-long-random-secret"},
    {"admin_password": "ChangeThisPassword123!"},
    {"cors_origins": "*"},
])
def test_production_rejects_unsafe_configuration(override):
    with pytest.raises(ValidationError):
        Settings(**(BASE | override))


def test_production_accepts_safe_configuration():
    settings = Settings(**BASE)
    assert settings.api_docs_enabled is False
    assert settings.uisp_verify_tls is True
