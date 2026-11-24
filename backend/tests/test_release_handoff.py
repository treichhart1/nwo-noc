"""Offline publication preparation never manufactures approval or omits findings."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/release"))
from prepare_publication import assemble, write_new
from test_backend_security import bundle


def inputs(tmp_path):
    manifest, docs, _ = bundle(tmp_path)
    manifest["approval_reference"] = "synthetic-disposable-review"
    return manifest, {k: v["payload"] for k, v in docs.items()}


def test_unsigned_assembly_keeps_complete_reports_and_hashes(tmp_path):
    manifest, payloads = inputs(tmp_path)
    before = copy.deepcopy(manifest)
    files = assemble(manifest, payloads)
    assert manifest == before
    assert set(files) == {"unsigned-release.json", *(k + ".envelope.json" for k in payloads)}
    approved = json.loads(files["unsigned-release.json"])
    assert not any(name.endswith(".sig") or "key" in name for name in files)
    for kind, entry in approved["evidence"].items():
        assert hashlib.sha256(files[entry["file"]]).hexdigest() == entry["sha256"]
        assert json.loads(files[entry["file"]])["payload"] == payloads[kind]


@pytest.mark.parametrize("bad", [
    "open_blocker", "high", "critical", "dev_omitted", "missing_image",
    "mutable", "source_mismatch", "missing_sbom", "unknown_role",
])
def test_unsafe_publication_inputs_fail_closed(tmp_path, bad):
    manifest, payloads = inputs(tmp_path)
    if bad == "open_blocker":
        payloads["provenance"]["risk_review"]["open_blockers"] = ["vendor-native-risk"]
    elif bad in {"high", "critical"}:
        payloads["containers"]["backend"]["Results"][0]["Vulnerabilities"] = [{"Severity": bad.upper()}]
    elif bad == "dev_omitted":
        payloads["dependencies"]["npm"]["metadata"]["dependencies"]["dev"] = 0
    elif bad == "missing_image":
        payloads["containers"].pop("redis")
    elif bad == "mutable":
        manifest["images"]["backend"] = "registry.example.invalid/backend:latest"
    elif bad == "source_mismatch":
        payloads["provenance"]["source_commit"] = "f" * 40
    elif bad == "missing_sbom":
        payloads["sbom"]["images"].pop("postgres")
    else:
        manifest["images"]["unknown"] = manifest["images"]["redis"]
    with pytest.raises(ValueError):
        assemble(manifest, payloads)


def test_publication_output_private_and_never_overwrites(tmp_path):
    tmp_path.chmod(0o700)
    manifest, payloads = inputs(tmp_path)
    files = assemble(manifest, payloads)
    output = tmp_path / "new-output"
    write_new(output, files)
    assert output.stat().st_mode & 0o777 == 0o700
    for name, raw in files.items():
        path = output / name
        assert path.read_bytes() == raw and path.stat().st_mode & 0o777 == 0o600
        assert path.stat().st_uid == os.geteuid()
    with pytest.raises(FileExistsError):
        write_new(output, files)
    assert (output / "unsigned-release.json").read_bytes() == files["unsigned-release.json"]


def test_publication_rejects_public_parent(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="Private owned"):
        write_new(tmp_path / "output", {})


@pytest.mark.parametrize("case", ["valid", "digest", "traversal", "duplicate", "symlink", "oversized"])
def test_historical_artifact_integrity_and_bounded_inspection(tmp_path, case):
    import zipfile
    from reconcile_evidence import inspect_archive
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as output:
        if case == "symlink":
            item = zipfile.ZipInfo("link.json")
            item.create_system = 3
            item.external_attr = 0o120777 << 16
            output.writestr(item, "{}")
        else:
            name = "../escape.json" if case == "traversal" else "summary.json"
            data = b"{}"
            output.writestr(name, data)
            if case == "duplicate":
                output.writestr(name, data)
    if case == "oversized":
        import struct
        declared = bytearray(archive.read_bytes())
        central = declared.index(b"PK\x01\x02")
        struct.pack_into("<I", declared, central + 24, 64 * 1024 * 1024 + 1)
        archive.write_bytes(declared)
    raw = archive.read_bytes()
    expected = {"id": 1, "name": "synthetic", "size_in_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest()}
    if case == "digest":
        expected["sha256"] = "0" * 64
    if case == "valid":
        report = inspect_archive(archive, expected)
        assert report["archive_digest_verified"] and len(report["json_files"]) == 1
    else:
        with pytest.raises(ValueError):
            inspect_archive(archive, expected)


@pytest.mark.parametrize("case", ["valid", "missing", "revision", "user", "base", "version", "probe_failed"])
def test_hardened_redis_requires_signed_source_and_runtime_contract(tmp_path, monkeypatch, case):
    from types import SimpleNamespace
    import manager
    import policy

    revision = "a" * 40
    base = "redis:7-alpine@sha256:" + "b" * 64
    configs = {role: {"Config": {"User": user, "Labels": {"org.opencontainers.image.revision": revision}}}
               for role, user in [("backend", "10001:10001"), ("frontend", "101:101"), ("postgres", ""), ("redis", "")]}
    configs["redis"]["Config"]["Labels"].update({"com.nwo-noc.redis.base": base, "com.nwo-noc.redis.version": "7.4.12"})
    approved = {"commit": revision, "images": {r: r for r in configs}}
    monkeypatch.setattr(manager, "git", lambda *args: "deploy/Dockerfile.redis" if args[0] == "ls-tree" else json.dumps({"images": {"redis:7-alpine": base}}))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if case == "probe_failed":
            raise policy.Reject("Synthetic package verification failure")
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(manager, "run", run)
    if case == "missing":
        approved["images"].pop("redis")
    elif case == "revision":
        configs["redis"]["Config"]["Labels"]["org.opencontainers.image.revision"] = "c" * 40
    elif case == "user":
        configs["redis"]["Config"]["User"] = "0:0"
    elif case == "base":
        configs["redis"]["Config"]["Labels"]["com.nwo-noc.redis.base"] = "other@sha256:" + "b" * 64
    elif case == "version":
        configs["redis"]["Config"]["Labels"]["com.nwo-noc.redis.version"] = "8.0"
    host = SimpleNamespace(image=lambda ref: configs[ref])
    if case != "valid":
        with pytest.raises(policy.Reject):
            manager.Host.images(host, approved)
    else:
        manager.Host.images(host, approved)
        assert approved["images"]["redis"] == "redis"
        assert len(calls) == 1
        assert calls[0][calls[0].index("--network") + 1] == "none"
        assert "--read-only" in calls[0] and "--cap-drop" in calls[0]
        assert calls[0][-1] == "/usr/local/bin/noc-redis-verify"
