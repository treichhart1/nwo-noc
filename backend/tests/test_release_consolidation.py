"""Release contracts and network-free one-device pilot guards."""

import importlib.util, json
from pathlib import Path
import pytest
from app.pilot_plan import review

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts/release" / f"{name}.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def plan():
    return dict(
        approved=True,
        approval_record="SYNTHETIC-ONLY-CHANGE",
        candidate_commit="a" * 40,
        device_id=1,
        vendor="cambium",
        address="10.93.0.1",
        protocol="snmpv3_get",
        port=161,
        minimum_privilege_reviewed=True,
        private_secret_mount_reviewed=True,
        deny_other_egress_verified=True,
        firmware_mapping_validated=True,
        backup_restore_verified=True,
    )


@pytest.mark.parametrize(
    "vendor", ["ubiquiti", "cambium", "racom", "aviat", "mikrotik"]
)
def test_pilot_plan_is_offline_and_cannot_activate(vendor, monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "create_connection", lambda *a, **k: pytest.fail("network call")
    )
    p = plan() | {"vendor": vendor}
    if vendor == "mikrotik":
        p.update(protocol="https_get", port=443)
    assert review(p)["network_calls"] == 0 and review(p)["execution_enabled"] is False


@pytest.mark.parametrize(
    "bad",
    [
        {"approved": False},
        {"address": "0.0.0.0"},
        {"address": "10.1.0.0/24"},
        {"address": "example.invalid"},
        {"address": "169.254.169.254"},
        {"address": "192.0.2.93"},
        {"interval_seconds": 1},
        {"concurrency": 2},
        {"batch_size": 2},
        {"tls_verified": False},
        {"simulation_only": False},
        {"password": "never-allowed"},
        {"deny_other_egress_verified": False},
        {"firmware_mapping_validated": False},
        {"minimum_privilege_reviewed": False},
        {"backup_restore_verified": False},
        {"device_ids": [1, 2]},
    ],
)
def test_pilot_rejects_unsafe_or_multiple_targets(bad):
    with pytest.raises(ValueError):
        review(plan() | bad)


def test_api_routes_have_no_duplicate_method_path_contracts():
    from app.main import app

    seen = set()
    for route in app.routes:
        for method in getattr(route, "methods", set()):
            key = (method, route.path)
            assert key not in seen, key
            seen.add(key)


def test_release_versions_and_migrations_consistent():
    from app.version import VERSION
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert (
        VERSION == json.loads((ROOT / "frontend/package.json").read_text())["version"]
    )
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    script = ScriptDirectory.from_config(config)
    assert (
        len(script.get_heads()) == 1
        and script.get_current_head() == "0013_customer_foundation"
    )


def test_reviewed_stack_complete_without_merging():
    r = load("audit_stack").audit()
    assert r["prs"] == 97 and r["logical_replays"] == 1 and r["direct_ancestors"] == 96


def test_pin_identity_is_private_and_never_silently_repinned(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/release"))
    conf = load("configure").generate(tmp_path / "state")
    m = load("pin_images")
    monkeypatch.setattr(
        m,
        "image_id",
        lambda name: name if name.startswith("sha256:") else "sha256:" + "a" * 64,
    )
    monkeypatch.setattr(m, "source_revision", lambda ref=None: "a" * 40)
    first = m.capture(conf.parent)
    assert len(first["images"]) == 4
    assert (
        conf.parent / "release-manifest.json"
    ).stat().st_mode & 0o777 == 0o600 and m.capture(conf.parent) == first
    conf.write_text(conf.read_text() + "RF_SCHEDULER_ENABLED=true\n")
    with pytest.raises(ValueError):
        m.capture(conf.parent)


@pytest.mark.parametrize(
    "value,expected",
    [("1048576B", 1), ("1024KiB", 1), ("12.5MiB", 12.5), ("1GiB", 1024)],
)
def test_container_resource_units(value, expected):
    assert load("resource_monitor").memory_mib(value) == expected


def test_pin_manifest_cannot_omit_images(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/release"))
    conf = load("configure").generate(tmp_path / "state")
    m = load("pin_images")
    monkeypatch.setattr(
        m,
        "image_id",
        lambda name: name if name.startswith("sha256:") else "sha256:" + "a" * 64,
    )
    monkeypatch.setattr(m, "source_revision", lambda ref=None: "a" * 40)
    manifest = m.capture(conf.parent)
    manifest["images"] = {}
    (conf.parent / "release-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        m.capture(conf.parent)
