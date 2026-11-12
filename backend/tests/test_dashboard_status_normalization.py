"""Dashboard status normalization regression checks (no external integrations)."""
import pytest

from app.api.routes import normalized_status


@pytest.mark.parametrize(("raw", "expected"), [
    ("active", "active"),
    ("online", "active"),
    ("connected", "active"),
    ("offline", "offline"),
    ("unreachable", "offline"),
    ("disconnected", "offline"),
    ("unknown", "unknown"),
    (None, "unknown"),
    ("", "unknown"),
])
def test_normalized_radio_status(raw, expected):
    assert normalized_status(raw) == expected
