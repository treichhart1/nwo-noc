from copy import deepcopy

import pytest

from app.integrations.uisp_rf import extract_uisp_rf_metrics


def test_extracts_explicit_unit_bearing_fields():
    device = {
        "radio": {"rssi_dbm": -61, "snr_db": 34, "frequency_mhz": 11075},
        "overview": {"ccq_percent": 98, "temperature_c": 43},
    }
    assert extract_uisp_rf_metrics(device) == {
        "rssi_dbm": -61.0,
        "snr_db": 34.0,
        "ccq_percent": 98.0,
        "frequency_mhz": 11075.0,
        "temperature_c": 43.0,
    }


def test_ambiguous_fields_are_not_assumed_to_have_units():
    device = {"overview": {"signal": -63, "frequency": 11075, "txRate": 1000}}
    assert extract_uisp_rf_metrics(device) == {}


def test_malformed_nested_data_is_ignored():
    assert extract_uisp_rf_metrics({"radio": [], "overview": None}) == {}


def test_radio_takes_precedence_and_source_is_unchanged():
    device = {"radio": {"rssi_dbm": -60}, "overview": {"rssi_dbm": -80}}
    before = deepcopy(device)
    assert extract_uisp_rf_metrics(device) == {"rssi_dbm": -60.0}
    assert device == before


def test_invalid_value_is_omitted():
    assert extract_uisp_rf_metrics({"radio": {"ccq_percent": 150}}) == {}


def test_non_mapping_rejected():
    with pytest.raises(TypeError):
        extract_uisp_rf_metrics(None)
