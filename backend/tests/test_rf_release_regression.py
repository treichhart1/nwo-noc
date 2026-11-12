"""Release regression invariants for the RF read API and safe defaults."""
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.core.config import Settings


RF_ENDPOINTS = (
    "/api/devices/1/rf-diagnostics",
    "/api/rf/collection-health",
    "/api/rf/collection-history",
    "/api/rf/collection-reliability",
    "/api/rf/site-health",
)


@pytest.mark.parametrize("endpoint", RF_ENDPOINTS)
def test_rf_endpoints_never_allow_anonymous_reads(endpoint):
    app.dependency_overrides.clear()
    response = TestClient(app).get(endpoint)
    assert response.status_code in (401, 403), (endpoint, response.status_code)


def test_rf_collection_disabled_by_default():
    fields = Settings.model_fields
    assert fields["rf_collection_enabled"].default is False
    assert fields["rf_history_pruning_enabled"].default is False


def test_api_documentation_disabled_by_default():
    assert Settings.model_fields["api_docs_enabled"].default is False
