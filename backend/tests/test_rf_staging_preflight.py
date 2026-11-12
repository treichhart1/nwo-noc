from scripts.rf_staging_preflight import evaluate


def test_safe_staging_configuration():
    result = evaluate({"APP_ENV": "staging", "RF_COLLECTION_ENABLED": "false",
                       "RF_HISTORY_PRUNING_ENABLED": "false", "UISP_VERIFY_TLS": "true"})
    assert result["ready"] is True


def test_production_environment_is_not_staging():
    assert evaluate({})["ready"] is False


def test_collection_or_pruning_opt_in_blocks_preflight():
    for setting in ("RF_COLLECTION_ENABLED", "RF_HISTORY_PRUNING_ENABLED"):
        assert evaluate({"APP_ENV": "staging", setting: "true"})["ready"] is False


def test_disabled_tls_blocks_preflight():
    assert evaluate({"APP_ENV": "staging", "UISP_VERIFY_TLS": "false"})["ready"] is False
