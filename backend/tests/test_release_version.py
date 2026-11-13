import json
from pathlib import Path
from app.main import app, health
from app.version import VERSION


def test_release_version_matches_health_api_and_frontend():
    assert health()["version"] == app.version == VERSION
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    assert json.loads((frontend / "package.json").read_text())["version"] == VERSION
    lock = json.loads((frontend / "package-lock.json").read_text())
    assert lock["version"] == lock["packages"][""]["version"] == VERSION
