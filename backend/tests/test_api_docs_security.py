"""Deployment API documentation exposure regression tests."""
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app


def test_api_docs_disabled_in_default_configuration(monkeypatch):
    monkeypatch.delenv("API_DOCS_ENABLED", raising=False)
    settings = Settings(
        _env_file=None,
        secret_key="test-only-secret",
        database_url="postgresql+psycopg://user:password@localhost:5432/test",
        admin_password="test-only-password",
    )
    assert settings.api_docs_enabled is False


def test_docs_and_schema_not_published_by_default():
    # App is created using CI defaults with API_DOCS_ENABLED unset.
    if app.openapi_url is None:
        client = TestClient(app)
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
