"""Assemble validated unsigned release evidence offline; never sign, push or approve."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from evidence import KINDS, registry_identity, verify_release_evidence
from policy import private_file


def assemble(manifest, payloads):
    if (manifest.get("format") != 1
        or manifest.get("repository") != "treichhart1/nwo-noc"
        or manifest.get("purpose") != "release"
        or set(manifest.get("images", {})) != {"backend", "frontend", "postgres", "redis"}
        or set(payloads) != KINDS
        or not all(re.fullmatch(r"[a-f0-9]{40}", manifest.get(k, "")) for k in ("commit", "tree"))
        or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", manifest.get("migration_head", ""))
        or not re.fullmatch(r"[A-Za-z0-9_.:/ -]{3,160}", manifest.get("approval_reference", ""))
        or "REPLACE" in manifest["approval_reference"]
        or not isinstance(manifest.get("upgrade_from"), list)
        or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{40}", v) for v in manifest["upgrade_from"])):
        raise ValueError("Complete independently reviewed release manifest required")
    for ref in manifest["images"].values():
        registry_identity(ref)  # Mutable/local-only identities are rejected.
    approved = dict(manifest)
    approved["evidence"] = {}
    files = {}
    for kind in sorted(KINDS):
        name = kind + ".envelope.json"
        doc = {"format": 1, "kind": kind,
               **{k: approved[k] for k in ("commit", "tree", "images")},
               "payload": payloads[kind]}
        raw = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
        files[name] = raw
        approved["evidence"][kind] = {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}

    def read(path, **kwargs):
        return files[Path(path).name]

    # Reuse the installer's exact release gate. Unresolved blockers are not cleared.
    verify_release_evidence(approved, Path("unsigned-release.json"), read)
    files["unsigned-release.json"] = (json.dumps(approved, indent=2, sort_keys=True) + "\n").encode()
    return files


def write_new(folder, files):
    folder = Path(folder)
    parent = folder.parent.lstat()
    import stat
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise ValueError("Private owned publication parent required")
    folder.mkdir(mode=0o700)  # Never overwrite even an existing empty output folder.
    for name, data in files.items():
        fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    def read(name):
        return json.loads(private_file(args.inputs / name, owner=os.geteuid(), max_bytes=64 * 1024 * 1024))
    manifest = read("manifest.json")
    def git(*argv):
        return subprocess.check_output(["git", "-C", str(args.source), *argv], text=True, timeout=30).strip()
    if git("rev-parse", "HEAD") != manifest.get("commit") or git("rev-parse", "HEAD^{tree}") != manifest.get("tree") or git("status", "--porcelain"):
        raise ValueError("Clean exact source checkout required")
    files = assemble(manifest, {kind: read(kind + ".payload.json") for kind in KINDS})
    for prior in manifest["upgrade_from"]:
        subprocess.run(["git", "-C", str(args.source), "merge-base", "--is-ancestor", prior, manifest["commit"]], check=True, capture_output=True, timeout=30)
    write_new(args.output, files)
    print("Validated unsigned evidence assembled; independent risk approval, signing and publication remain required.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError):
        print("Publication preparation refused: check protected inputs, exact source, complete reports and unresolved blockers. No signing or publication performed.", file=sys.stderr)
        raise SystemExit(1)
