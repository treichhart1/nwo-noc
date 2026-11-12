"""RF collection must remain opt-in and reuse the existing UISP sync."""
from unittest.mock import Mock, patch

from app.core.config import settings
from app.integrations.uisp import sync_uisp


def _response():
    response = Mock()
    response.json.return_value = []
    response.raise_for_status.return_value = None
    return response


def test_rf_collection_disabled_by_default():
    assert settings.rf_collection_enabled is False


def test_disabled_sync_never_stages_rf():
    db = Mock()
    with patch("app.integrations.uisp.get_integration", return_value=Mock()), \
         patch("app.integrations.uisp.httpx.get", return_value=_response()), \
         patch("app.integrations.uisp.stage_uisp_rf_batch") as stage, \
         patch.object(settings, "uisp_base_url", "https://uisp.example"), \
         patch.object(settings, "uisp_token", "test-token"), \
         patch.object(settings, "rf_collection_enabled", False):
        result = sync_uisp(db)
    assert result == {"ok": True, "imported": 0}
    stage.assert_not_called()
    db.commit.assert_called_once()


def test_enabled_sync_stages_once_with_configured_bounds():
    db = Mock()
    with patch("app.integrations.uisp.get_integration", return_value=Mock()), \
         patch("app.integrations.uisp.httpx.get", return_value=_response()) as fetch, \
         patch("app.integrations.uisp.stage_uisp_rf_batch", return_value={"received": 0, "staged": 0, "skipped": 0}) as stage, \
         patch.object(settings, "uisp_base_url", "https://uisp.example"), \
         patch.object(settings, "uisp_token", "test-token"), \
         patch.object(settings, "rf_collection_enabled", True), \
         patch.object(settings, "rf_collection_min_interval_seconds", 600), \
         patch.object(settings, "rf_collection_max_devices", 100):
        result = sync_uisp(db)
    assert result["rf_collection"]["staged"] == 0
    stage.assert_called_once()
    assert stage.call_args.kwargs["min_interval_seconds"] == 600
    assert stage.call_args.kwargs["max_devices"] == 100
    fetch.assert_called_once()
    db.flush.assert_called_once()
    db.commit.assert_called_once()
