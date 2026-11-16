"""Offline Ed25519 approval verification; trust is supplied independently by the operator."""
import base64
from pathlib import Path
import subprocess
import tempfile


def verify_signature(payload: bytes, signature: bytes, public_key: bytes):
    try:
        lines = public_key.strip().splitlines()
        if lines[0] != b"-----BEGIN PUBLIC KEY-----" or lines[-1] != b"-----END PUBLIC KEY-----":
            raise ValueError()
        der = base64.b64decode(b"".join(lines[1:-1]), validate=True)
        if len(der) != 44 or der[:12] != bytes.fromhex("302a300506032b6570032100") or len(signature) != 64:
            raise ValueError()
        with tempfile.TemporaryDirectory(prefix="noc-release-verification-") as folder:
            root = Path(folder)
            for name, data in (("message", payload), ("signature", signature), ("key", public_key)):
                target = root / name
                target.touch(mode=0o600)
                target.write_bytes(data)
            result = subprocess.run(
                ["/usr/bin/openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(root / "key"),
                 "-rawin", "-in", str(root / "message"), "-sigfile", str(root / "signature")],
                capture_output=True, timeout=10, check=False,
            )
            if result.returncode != 0:
                raise ValueError()
    except (ValueError, IndexError, OSError, subprocess.TimeoutExpired):
        raise ValueError("Trusted Ed25519 release approval signature verification failed") from None
