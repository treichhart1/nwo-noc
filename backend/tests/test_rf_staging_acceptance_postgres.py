"""Offline RF staging acceptance against migrated disposable PostgreSQL."""
from unittest.mock import patch

from sqlalchemy import select

from app.core.config import settings
from app.integrations.uisp import sync_uisp
from app.models.entities import Device, RFCollectionRun, TelemetrySample
from test_rf_end_to_end_postgres import SimulatedUISPResponse
from test_rf_postgres_integration import postgres_db


def test_offline_staging_keeps_rf_disabled_and_inventory_intact(postgres_db):
    db = postgres_db
    with patch("app.integrations.uisp.httpx.get", return_value=SimulatedUISPResponse()) as fetch, \
         patch.object(settings, "uisp_base_url", "https://uisp.invalid"), \
         patch.object(settings, "uisp_token", "ci-placeholder-only"), \
         patch.object(settings, "rf_collection_enabled", False), \
         patch.object(settings, "rf_history_pruning_enabled", False):
        result = sync_uisp(db)
    assert result["ok"] is True
    assert result["imported"] == 2
    assert db.scalar(select(Device).where(Device.external_id == "rf-e2e-ci-radio")) is not None
    assert db.scalars(select(TelemetrySample)).all() == []
    assert db.scalars(select(RFCollectionRun)).all() == []
    fetch.assert_called_once()
