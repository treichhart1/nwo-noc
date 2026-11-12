"""Staging gate must never imply production approval."""
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "staging_readiness_gate.py"
spec = importlib.util.spec_from_file_location("staging_readiness_gate", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_empty_evidence_fails_closed():
    report = module.evaluate({})
    assert not report["staging_gate_passed"]
    assert len(report["missing"]) == len(module.REQUIRED)
    assert report["production_deployment_approved"] is False


def test_complete_evidence_allows_staging_only():
    report = module.evaluate({key: True for key in module.REQUIRED})
    assert report["staging_gate_passed"]
    assert not report["missing"]
    assert report["production_deployment_approved"] is False


def test_truthy_strings_are_not_accepted():
    report = module.evaluate({key: "true" for key in module.REQUIRED})
    assert not report["staging_gate_passed"]


def test_missing_single_requirement_blocks_staging():
    for key in module.REQUIRED:
        evidence = {name: True for name in module.REQUIRED}
        evidence[key] = False
        report = module.evaluate(evidence)
        assert not report["staging_gate_passed"]
        assert key in report["missing"]


def test_default_template_does_not_pass():
    import json
    template = Path(__file__).resolve().parents[2] / "docs" / "noc-75t-staging-evidence-template.json"
    assert not module.evaluate(json.loads(template.read_text(encoding="utf-8")))["staging_gate_passed"]
