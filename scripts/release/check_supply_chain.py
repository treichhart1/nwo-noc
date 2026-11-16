"""Reject mutable action/image defaults and incomplete Python artifact locks."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def read_lock(path):
    records = {}
    logical = path.read_text().replace("\\\n", " ")
    for line in logical.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9_.-]+)==([^\s]+)(?:\s+--hash=sha256:[a-f0-9]{64})+\s*", line
        )
        if not match:
            raise ValueError(f"Unpinned or unhashed requirement in {path.name}")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in records:
            raise ValueError("Duplicate locked requirement")
        records[name] = match[2]
    if not records:
        raise ValueError("Empty dependency lock")
    return records


def validate_image_source(source, approved):
    local_builds = {
        "${NOC_BACKEND_IMAGE:-noc84-backend:rc}",
        "${NOC_FRONTEND_IMAGE:-noc84-frontend:rc}",
    }
    for reference in re.findall(r"(?m)^\s*(?:FROM|image:)\s+(\S+)", source):
        if reference in local_builds:
            continue
        default = re.fullmatch(r"\$\{NOC_[A-Z_]+:-(.+)\}", reference)
        if default:
            reference = default[1]
        if reference not in approved:
            raise ValueError("Image source is not a reviewed immutable reference")


def validate(root=ROOT):
    runtime = read_lock(root / "backend/requirements.lock")
    tests = read_lock(root / "backend/requirements-test.lock")
    audit = read_lock(root / "backend/requirements-audit.lock")
    for name in tests.keys() & audit.keys():
        if tests[name] != audit[name]:
            raise ValueError("Audit and test dependency versions differ")
    for name, version in runtime.items():
        if tests.get(name) != version:
            raise ValueError("Test and runtime dependency versions differ")
    for filename in (
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-audit.txt",
    ):
        for line in (root / "backend" / filename).read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            name, version = line.strip().split("==")
            name = re.sub(r"\[.*\]", "", name)
            name = re.sub(r"[-_.]+", "-", name).lower()
            closure = audit if filename == "requirements-audit.txt" else tests
            if closure.get(name) != version:
                raise ValueError("Direct constraint differs from dependency lock")
    ci = (root / ".github/workflows/ci.yml").read_text()
    actions = re.findall(r"uses:\s+(\S+)", ci)
    if not actions or any(
        not re.fullmatch(r"actions/[\w-]+@[a-f0-9]{40}", a) for a in actions
    ):
        raise ValueError("Actions must use reviewed immutable commits")
    images = json.loads((root / "deploy/image-lock.json").read_text())["images"]
    for tag, reference in images.items():
        if not re.fullmatch(re.escape(tag) + r"@sha256:[a-f0-9]{64}", reference):
            raise ValueError("Invalid immutable image reference")
    for filename in (
        "deploy/Dockerfile.ubuntu-rehearsal",
        "examples/signup/compose.yml",
        "backend/Dockerfile",
        "frontend/Dockerfile",
        "docker-compose.yml",
        "deploy/compose.isolated.yml",
        "scripts/release/pin_images.py",
        ".github/workflows/ci.yml",
    ):
        source = (root / filename).read_text()
        validate_image_source(source, set(images.values()))
        for tag, reference in images.items():
            if source.count(tag) != source.count(reference):
                raise ValueError(f"Mutable or inconsistent image default in {filename}")
    return {
        "runtime_locked": len(runtime),
        "test_locked": len(tests),
        "audit_locked": len(audit),
        "immutable_actions": len(actions),
        "immutable_images": len(images),
    }


if __name__ == "__main__":
    print(json.dumps(validate()))
