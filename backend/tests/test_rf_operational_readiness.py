"""Operational readiness: RF failures must not take UISP inventory offline."""
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.core.config import settings
from app.integrations.uisp import sync_uisp
from app.services.rf_diagnostics import get_rf_diagnostics


def _response():
    response = Mock()
    response.json.return_value = []
    response.raise_for_status.return_value = None
    return response


def test_rf_batch_failure_preserves_inventory_commit():
    db = Mock()
    with patch("app.integrations.uisp.get_integration", return_value=Mock()) as state, \
         patch("app.integrations.uisp.fetch_snapshot", return_value={'devices':[],'errors':[]}) as fetch, \
         patch("app.integrations.uisp.stage_uisp_rf_batch", side_effect=ValueError("batch too large")), \
         patch.object(settings, "uisp_enabled", True), \
         patch.object(settings, "integrations_admin_approved", True), \
         patch.object(settings, "offline_mode", False), \
         patch.object(settings, "uisp_allowed_origins", "https://uisp.example"), \
         patch.object(settings, "uisp_base_url", "https://uisp.example"), \
         patch.object(settings, "uisp_token", "test-token"), \
         patch.object(settings, "rf_collection_enabled", True), \
         patch.object(settings, "rf_history_pruning_enabled", False):
        result = sync_uisp(db)
    assert result["ok"] is True
    assert result["rf_collection"]["status"] == "failed"
    assert state.return_value.status == "degraded"
    assert state.return_value.last_error == "partial_sync"
    fetch.assert_called_once()
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    db.begin_nested.assert_called_once()


def test_rf_pruning_failure_preserves_inventory_commit():
    db = Mock()
    counts = {"received": 1, "staged": 1, "skipped": 0}
    with patch("app.integrations.uisp.get_integration", return_value=Mock()), \
         patch("app.integrations.uisp.fetch_snapshot", return_value={'devices':[],'errors':[]}), \
         patch("app.integrations.uisp.stage_uisp_rf_batch", return_value=counts), \
         patch("app.integrations.uisp.prune_rf_collection_history", side_effect=ValueError("invalid retention")), \
         patch.object(settings, "uisp_enabled", True), \
         patch.object(settings, "integrations_admin_approved", True), \
         patch.object(settings, "offline_mode", False), \
         patch.object(settings, "uisp_allowed_origins", "https://uisp.example"), \
         patch.object(settings, "uisp_base_url", "https://uisp.example"), \
         patch.object(settings, "uisp_token", "test-token"), \
         patch.object(settings, "rf_collection_enabled", True), \
         patch.object(settings, "rf_history_pruning_enabled", True):
        result = sync_uisp(db)
    assert result["ok"] is True
    assert result["rf_collection"]["status"] == "failed"
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


def test_naive_persisted_utc_timestamp_is_normalized():
    db = Mock()
    db.get.return_value = Mock(name="radio")
    sample = Mock()
    sample.id = 1
    sample.sampled_at = datetime(2026, 11, 12, 15, 55)
    sample.raw_data = {"rf_metrics_v1": {"rssi_dbm": -65}}
    db.scalars.return_value.all.return_value = [sample]
    report = get_rf_diagnostics(db, 1, now=datetime(2026, 11, 12, 16, tzinfo=timezone.utc))
    assert report["rf_sample_count"] == 1
    assert report["freshness"]["status"] == "fresh"
    assert report["trends"]["status"] == "insufficient_data"
