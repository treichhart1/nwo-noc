"""NOC112 release inputs fail closed when supply-chain pins are removed."""

import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "supply_chain", ROOT / "scripts/release/check_supply_chain.py"
)
chain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chain)


def test_release_inputs_are_immutable_and_runtime_versions_match_tests():
    result = chain.validate(ROOT)
    assert result["runtime_locked"] >= 55
    assert result["test_locked"] > result["runtime_locked"]
    assert result["immutable_images"] == 6


@pytest.mark.parametrize(
    "content",
    [
        "httpx==0.28.1\n",
        "httpx>=0.28\n",
        "-e https://example.invalid/repo\n",
        "",
        "httpx==0.28.1 --hash=sha256:bad\n",
    ],
)
def test_incomplete_artifact_locks_are_rejected(tmp_path, content):
    path = tmp_path / "requirements.lock"
    path.write_text(content)
    with pytest.raises(ValueError):
        chain.read_lock(path)


@pytest.mark.parametrize(
    "source",
    [
        "FROM python:latest\n",
        "image: ${NOC_POSTGRES_IMAGE:-postgres:latest}\n",
        "    image: postgres:16-alpine\n",
        "FROM unreviewed.invalid/image:tag AS build\n",
    ],
)
def test_mutable_or_unreviewed_images_are_rejected(source):
    with pytest.raises(ValueError):
        chain.validate_image_source(source, set())


def test_reviewed_digest_is_accepted():
    image = "python:3.12-slim@sha256:" + "a" * 64
    chain.validate_image_source("FROM " + image + " AS build\n", {image})


def test_workflow_yaml_and_run_steps_parse():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert len(workflow["jobs"]) == 12
    assert "signup-acceptance" in workflow["jobs"]["rf-release-acceptance"]["needs"]
    assert "SIGNUP_RESULT" in workflow["jobs"]["rf-release-acceptance"]["steps"][0]["run"]
    assert "wordpress-status" in workflow["jobs"]["rf-release-acceptance"]["needs"]
    assert "independent-pilot" in workflow["jobs"]["rf-release-acceptance"]["needs"]
    assert "PILOT_VM_RESULT" in workflow["jobs"]["rf-release-acceptance"]["steps"][0]["run"]
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "run" in step:
                assert isinstance(step["run"], str)
