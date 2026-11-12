"""Regression checks for secure UISP configuration defaults.

Use explicit constructor overrides so these tests do not depend on production
secrets, UISP connectivity, or a running PostgreSQL instance.
"""
from app.core.config import Settings


def test_uisp_tls_verification_enabled_by_default(monkeypatch):
    monkeypatch.delenv("UISP_VERIFY_TLS", raising=False)
    settings = Settings(
        _env_file=None,
        app_env="test",
        secret_key="test-only-secret",
        database_url="postgresql+psycopg://user:password@localhost:5432/test",
        admin_password="test-only-password",
    )
    assert settings.uisp_verify_tls is True


def test_uisp_tls_explicit_override_is_parsed(monkeypatch):
    monkeypatch.setenv("UISP_VERIFY_TLS", "false")
    settings = Settings(
        _env_file=None,
        app_env="test",
        secret_key="test-only-secret",
        database_url="postgresql+psycopg://user:password@localhost:5432/test",
        admin_password="test-only-password",
    )
    assert settings.uisp_verify_tls is False
