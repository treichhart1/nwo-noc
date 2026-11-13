from types import SimpleNamespace
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import current_user


def test_operational_summary_requires_authentication():
    assert TestClient(app).get('/api/operations/readiness').status_code==401


def test_operational_summary_denies_viewer():
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(role='viewer')
    try:assert TestClient(app).get('/api/operations/readiness').status_code==403
    finally:app.dependency_overrides.clear()
