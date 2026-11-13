"""RF self-healing preview remains read-only and blocked until validated telemetry exists."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from app.api.rf_routes import rf_self_healing_preview
from app.api.rf_permissions import require_rf_reader


class FakeDB:
    def __init__(self, device):
        self.device = device
        self.reads = []

    def get(self, model, device_id):
        self.reads.append(device_id)
        return self.device


def test_preview_blocks_without_validated_rf_samples():
    db = FakeDB(SimpleNamespace(id=42, name="Test AP"))
    result = rf_self_healing_preview(device_id=42, db=db, _user=SimpleNamespace(role="operator"))
    assert db.reads == [42]
    assert result["decision"] == "blocked"
    assert result["execution_enabled"] is False
    assert result["mode"] == "simulation_only"
    assert result["requires_validated_rf_telemetry"] is True
    assert "timestamp" in result["reason"].lower()


def test_preview_unknown_device_is_not_found():
    with pytest.raises(HTTPException) as error:
        rf_self_healing_preview(device_id=42, db=FakeDB(None), _user=SimpleNamespace(role="operator"))
    assert error.value.status_code == 404


@pytest.mark.parametrize("role", ["viewer", "customer", "unknown", ""])
def test_rf_reader_rejects_unprivileged_roles(role):
    with pytest.raises(HTTPException) as error:
        require_rf_reader(SimpleNamespace(role=role))
    assert error.value.status_code == 403
