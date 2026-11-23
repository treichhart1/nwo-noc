"""Preserve verified public release trust material for independently checked recovery."""
import hashlib
import json
from pathlib import Path

from artifacts import verify_signature
from evidence import verify_release_evidence
from policy import Reject, private_file

RESERVED = {"approval.json", "approval.json.sig", "trusted-public.pem", "trust-index.json"}


def fingerprint(key):
    import base64
    return hashlib.sha256(base64.b64decode(b"".join(key.strip().splitlines()[1:-1]), validate=True)).hexdigest()


def capture(approved, manifest_path, destination, *, reader=private_file):
    """Reverify exact captured bytes; never archive a private signing key."""
    manifest_path, destination = Path(manifest_path), Path(destination)
    raw = reader(manifest_path)
    if json.loads(raw) != approved or approved.get("purpose") != "release":
        raise Reject("Release approval changed before trust material capture")
    signature = reader(Path(str(manifest_path) + ".sig"))
    public = reader(Path("/etc/nwo-noc-release.pub"))
    verify_signature(raw, signature, public)
    names = [ref["file"] for ref in approved["evidence"].values()]
    if len(set(names)) != len(names) or set(names) & RESERVED:
        raise Reject("Release evidence filenames conflict with protected trust records")
    captured = {}

    def retain(path, **kwargs):
        data = reader(path, **kwargs)
        captured[Path(path).name] = data
        return data

    verify_release_evidence(approved, manifest_path, retain)
    captured.update({"approval.json": raw, "approval.json.sig": signature, "trusted-public.pem": public})
    destination.mkdir(mode=0o700)
    for name, data in captured.items():
        path = destination / name
        with path.open("xb") as stream:
            path.chmod(0o600)
            stream.write(data)
    index = {"format": 1, "public_key_spki_sha256": fingerprint(public), "files": {name: hashlib.sha256(data).hexdigest() for name, data in captured.items()}}
    path = destination / "trust-index.json"
    with path.open("x") as stream:
        path.chmod(0o600)
        json.dump(index, stream, indent=2)
    return index


def verify(directory, expected_public_fingerprint, *, reader=private_file):
    """Expected fingerprint must come from an independent approved trust channel."""
    directory = Path(directory)
    index = json.loads(reader(directory / "trust-index.json"))
    if index.get("format") != 1 or not isinstance(index.get("files"), dict):
        raise Reject("Recovery trust index invalid")
    for name, digest in index["files"].items():
        if Path(name).name != name or hashlib.sha256(reader(directory / name, max_bytes=64 * 1024**2)).hexdigest() != digest:
            raise Reject("Recovery trust file hash mismatch")
    public = reader(directory / "trusted-public.pem")
    if fingerprint(public) != expected_public_fingerprint or index.get("public_key_spki_sha256") != expected_public_fingerprint:
        raise Reject("Recovered release key does not match independent trust fingerprint")
    raw = reader(directory / "approval.json")
    verify_signature(raw, reader(directory / "approval.json.sig"), public)
    approved = json.loads(raw)
    if approved.get("purpose") != "release":
        raise Reject("Production recovery requires a production release approval")
    verify_release_evidence(approved, directory / "approval.json", reader)
    return approved
