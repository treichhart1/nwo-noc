"""Exact historical evidence must not be contaminated by the current worktree."""

import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/release"))
import integration_manifest as manifest
import release_acceptance as acceptance


def test_historical_inventory_uses_selected_git_tree(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True
        ).strip()

    git("init", "-q")
    git("config", "user.name", "Synthetic acceptance")
    git("config", "user.email", "acceptance@example.invalid")
    config = tmp_path / "backend/app/core/config.py"
    config.parent.mkdir(parents=True)
    config.write_text("class Settings:\n    baseline_only: bool = False\n")
    migration = tmp_path / "backend/alembic/versions/head.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("revision='0028_customer_status'\ndown_revision=None\n")
    git("add", ".")
    git("commit", "-qm", "Synthetic original inventory")
    old = git("rev-parse", "HEAD")
    config.write_text(
        "class Settings:\n    unrelated_new_setting: str = 'never serialize values'\n"
    )
    migration.write_text("revision='future_head'\ndown_revision=None\n")
    monkeypatch.setattr(manifest, "ROOT", tmp_path)
    assert manifest.configuration_inventory(old) == [
        {"environment": "BASELINE_ONLY", "type": "bool"}
    ]
    assert manifest.migration_inventory(old)[0]["revision"] == "0028_customer_status"
    # The working-tree scanner still exposes its deliberate unpinned semantics.
    assert (
        manifest.configuration_inventory()[0]["environment"] == "UNRELATED_NEW_SETTING"
    )
    with pytest.raises(ValueError):
        manifest.migration_inventory()


def test_complete_release_manifest_resolves_sources_in_the_baseline(monkeypatch):
    for name in ("configuration_inventory", "migration_inventory"):
        original = getattr(manifest, name)

        def selected_only(candidate=None, original=original):
            assert candidate == acceptance.BASELINE
            return original(candidate)

        monkeypatch.setattr(manifest, name, selected_only)
    result = acceptance.build(acceptance.BASELINE)
    assert result["candidate"] == acceptance.BASELINE and result["commit_count"] == 311
    assert len(result["pull_requests"]) == 118 and len(result["features"]) == 20
    assert len(result["migrations"]) == 28
    assert all(
        p["included_by"] == "direct_ancestry"
        for p in result["pull_requests"]
        if p["number"] != 86
    )
    assert (
        next(p for p in result["pull_requests"] if p["number"] == 86)["included_by"]
        == "equivalent_replay"
    )
    assert all(not p["default_contains_tip"] for p in result["pull_requests"])
    assert all(w["mode"] == "100755" for w in result["installer_wrappers"])


def test_missing_feature_source_cannot_be_reported_as_complete(tmp_path):
    for name in ("pr-snapshot.json", "feature-catalog.json"):
        (tmp_path / name).write_text((acceptance.INPUTS / name).read_text())
    path = tmp_path / "feature-catalog.json"
    value = json.loads(path.read_text())
    value["features"][0]["backend"] = ["backend/app/does-not-exist.py"]
    path.write_text(json.dumps(value))
    with pytest.raises(subprocess.CalledProcessError):
        acceptance.build(acceptance.BASELINE, inputs=tmp_path)


def test_pinned_execution_evidence_does_not_promote_missing_validation():
    evidence = json.loads((acceptance.INPUTS / "evidence.json").read_text())
    assert evidence["audited_baseline"] == acceptance.BASELINE
    assert evidence["sources"][0]["checkpoint_count"] == 37
    assert evidence["sources"][1]["host_checkpoint_count"] == 77
    assert evidence["sources"][1]["failure_assertion_count"] == 26
    assert evidence["sources"][2]["status"] == "FAIL"
    assert all(
        item["status"] in {"NOT TESTED", "BLOCKED"} for item in evidence["unexecuted"]
    )
    assert all(
        not source.get("full_host_boot_tested", False) for source in evidence["sources"]
    )


def test_high_release_gates_cannot_be_mistaken_for_go():
    matrix = json.loads((acceptance.INPUTS / "acceptance-matrix.json").read_text())
    assert matrix["decision"] == "NO-GO"
    assert set(matrix["allowed_statuses"]) == {"PASS", "FAIL", "NOT TESTED", "BLOCKED"}
    assert all(row["status"] in matrix["allowed_statuses"] for row in matrix["rows"])
    rows = {row["id"]: row for row in matrix["rows"]}
    assert rows["security"]["status"] == "BLOCKED"
    assert rows["live-features"]["status"] == "BLOCKED"
    assert rows["offhost"]["status"] == "NOT TESTED"
    assert rows["performance"]["status"] == "FAIL"


def test_configuration_review_worksheet_cannot_activate_integrations():
    data = json.loads(
        (ROOT / "deploy/host/installation-review.example.json").read_text()
    )
    assert not data["installer_input"] and not data["approved"]
    assert data["core"]["offline_mode"] and data["core"][
        "dashboard_binding"
    ].startswith("127.0.0.1")
    assert all(not item["enabled"] for item in data["integrations"].values())
    assert (
        not data["pilot_activation_authorized"]
        and not data["automatic_remediation_enabled"]
    )


def test_database_release_constraints_are_validated(postgres_db):
    from sqlalchemy import text

    db = postgres_db
    assert (
        db.scalar(text("SELECT version_num FROM alembic_version"))
        == "0035_unified_communications"
    )
    counts = db.execute(
        text(
            "SELECT contype,count(*) AS n FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' GROUP BY contype"
        )
    ).all()
    assert dict(counts)["f"] > 100 and dict(counts)["u"] > 20
    assert (
        db.scalar(
            text(
                "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND NOT convalidated"
            )
        )
        == 0
    )
    assert (
        db.scalar(
            text(
                "SELECT count(*) FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND (NOT indisvalid OR NOT indisready)"
            )
        )
        == 0
    )
    indexes = db.scalar(
        text(
            "SELECT count(*) FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public'"
        )
    )
    print(
        json.dumps(
            {
                "release_database_constraints": dict(counts),
                "unvalidated_constraints": 0,
                "invalid_indexes": 0,
                "indexes": indexes,
                "scope": "read-only inspection of disposable migrated PostgreSQL; existing domain suites exercise FK/unique/rollback/history behavior",
            }
        )
    )


from test_rf_postgres_integration import postgres_db
