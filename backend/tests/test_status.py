from app.api.routes import normalized_status


def test_status_normalization():
    assert normalized_status("online") == "active"
    assert normalized_status("connected") == "active"
    assert normalized_status("unreachable") == "offline"
    assert normalized_status(None) == "unknown"
