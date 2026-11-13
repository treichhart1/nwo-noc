"""RF self-healing preview remains read-only and blocked until validated telemetry exists."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from app.api.rf_routes import rf_self_healing_preview
from app.api.rf_permissions import require_rf_reader


class FakeDB:
    def __init__(self, device, rows=None):
        self.device = device
        self.rows = rows or []
        self.reads = []

    def scalars(self, query):
        return SimpleNamespace(all=lambda: self.rows)

    def get(self, model, device_id):
        self.reads.append(device_id)
        return self.device


def test_preview_blocks_without_validated_rf_samples():
    db = FakeDB(SimpleNamespace(id=42, name="Test AP", monitor_enabled=False, monitor_status="unknown", monitor_last_checked_at=None))
    result = rf_self_healing_preview(device_id=42, db=db, _user=SimpleNamespace(role="operator"))
    assert db.reads == [42]
    assert result["decision"] == "blocked"
    assert result["execution_enabled"] is False
    assert result["mode"] == "simulation_only"
    assert result["requires_validated_rf_telemetry"] is True
    assert "three valid recent RF samples" in result["reason"].lower()


def test_preview_unknown_device_is_not_found():
    with pytest.raises(HTTPException) as error:
        rf_self_healing_preview(device_id=42, db=FakeDB(None), _user=SimpleNamespace(role="operator"))
    assert error.value.status_code == 404


@pytest.mark.parametrize("role", ["viewer", "customer", "unknown", ""])
def test_rf_reader_rejects_unprivileged_roles(role):
    with pytest.raises(HTTPException) as error:
        require_rf_reader(SimpleNamespace(role=role))
    assert error.value.status_code == 403


def test_three_valid_degraded_samples_allow_only_simulated_proposal():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(sampled_at=now - timedelta(minutes=n),
                        raw_data={"rf_metrics_v1": {"rssi_dbm": -65, "snr_db": 10, "ccq_percent": 45}})
        for n in (1, 2, 3)
    ]
    device = SimpleNamespace(id=42, name="Test AP", monitor_enabled=True,
                             monitor_status="up", monitor_last_checked_at=now - timedelta(minutes=1))
    result = rf_self_healing_preview(device_id=42, db=FakeDB(device, rows), _user=SimpleNamespace(role="operator"))
    assert result["decision"] == "pending_approval"
    assert result["execution_enabled"] is False
    assert result["mode"] == "simulation_only"


def test_stale_reachability_blocks_even_with_degraded_rf_samples():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(sampled_at=now - timedelta(minutes=n),
                        raw_data={"rf_metrics_v1": {"rssi_dbm": -65, "snr_db": 10, "ccq_percent": 45}})
        for n in (1, 2, 3)
    ]
    device = SimpleNamespace(id=42, name="Test AP", monitor_enabled=True,
                             monitor_status="up", monitor_last_checked_at=now - timedelta(minutes=30))
    result = rf_self_healing_preview(device_id=42, db=FakeDB(device, rows), _user=SimpleNamespace(role="operator"))
    assert result["decision"] == "blocked"
