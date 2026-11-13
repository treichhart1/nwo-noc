from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import current_user


@pytest.mark.parametrize('endpoint',['/api/integrations/uisp/sync','/api/integrations/mikrotik/collect'])
def test_collection_trigger_requires_admin(endpoint):
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(role='operator')
    try:assert TestClient(app).post(endpoint).status_code==403
    finally:app.dependency_overrides.clear()


@pytest.mark.parametrize('endpoint',['/api/integrations/read-only-status','/api/devices/1/mikrotik-observation'])
def test_integration_views_require_authentication(endpoint):
    assert TestClient(app).get(endpoint).status_code==401
