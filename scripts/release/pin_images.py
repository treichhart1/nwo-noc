"""Capture a private immutable isolated release identity; never repin silently."""

import hashlib, json, os, re, subprocess, sys
from pathlib import Path
from validate_config import validate

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {
    "NOC_BACKEND_IMAGE": "noc84-backend:rc",
    "NOC_FRONTEND_IMAGE": "noc84-frontend:rc",
    "NOC_POSTGRES_IMAGE": "postgres:16-alpine",
    "NOC_REDIS_IMAGE": "redis:7-alpine",
}
IMAGE_ID = re.compile(r"^sha256:[a-f0-9]{64}$")


def image_id(name):
    p = subprocess.run(
        [
            "docker",
            "--host=unix:///var/run/docker.sock",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            name,
        ],
        capture_output=True,
        text=True,
    )
    value = p.stdout.strip()
    if p.returncode or not IMAGE_ID.fullmatch(value):
        raise ValueError(
            "Required local release image missing; build/load application images and pull the documented PostgreSQL/Redis images first"
        )
    return value


def source_revision(ref=None):
    if ref is not None and not re.fullmatch(r"[a-f0-9]{40}", ref):
        raise ValueError("Full source commit required")
    if (
        ref is None
        and subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    ):
        raise ValueError("Commit candidate changes before pinning a release")
    return subprocess.run(
        ["git", "rev-parse", "--verify", (ref or "HEAD") + "^{commit}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def capture(state, source_commit=None):
    state = Path(state)
    envpath = state / "acceptance.env"
    values = validate(envpath)
    target = state / "release-manifest.json"
    if target.exists():
        info = target.lstat()
        if target.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Private owned release manifest required")
        manifest = json.loads(target.read_text())
        if (
            manifest.get("repository") != "treichhart1/nwo-noc"
            or not re.fullmatch(r"[a-f0-9]{40}", manifest.get("source_commit", ""))
            or set(manifest.get("images", {})) != set(DEFAULTS)
            or not all(IMAGE_ID.fullmatch(v) for v in manifest["images"].values())
        ):
            raise ValueError("Invalid release identity manifest")
        if (
            manifest["configuration_sha256"]
            != hashlib.sha256(envpath.read_bytes()).hexdigest()
        ):
            raise ValueError(
                "Pinned release configuration changed; review it and use a new release state instead of silently repinning"
            )
        for key, ident in manifest["images"].items():
            if values.get(key) != ident or image_id(ident) != ident:
                raise ValueError(
                    "Pinned release image unavailable or configuration mismatch"
                )
        return manifest
    images = {
        key: image_id(values.get(key, default)) for key, default in DEFAULTS.items()
    }
    if any(key in values and not IMAGE_ID.fullmatch(values[key]) for key in images):
        raise ValueError(
            "Existing image overrides require an existing reviewed manifest or a fresh state"
        )
    sha = source_revision(source_commit)
    content = envpath.read_text() + "".join(
        f"{key}={value}\n" for key, value in images.items() if key not in values
    )
    manifest = {
        "repository": "treichhart1/nwo-noc",
        "source_commit": sha,
        "images": images,
        "configuration_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "scope": "isolated synthetic acceptance; no pilot or production approval",
    }
    # Both files are private. A partial write fails closed on retry; retain state
    # for diagnosis rather than silently selecting newer mutable image tags.
    with open(target, "x", opener=lambda p, f: os.open(p, f, 0o600)) as f:
        json.dump(manifest, f, indent=2)
    temp = state / "acceptance.env.pinned"
    with open(temp, "x", opener=lambda p, f: os.open(p, f, 0o600)) as f:
        f.write(content)
    os.replace(temp, envpath)
    return manifest


if __name__ == "__main__":
    try:
        m = capture(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
        print(
            json.dumps(
                {
                    "source_commit": m["source_commit"],
                    "images": m["images"],
                    "scope": m["scope"],
                }
            )
        )
    except (
        ValueError,
        OSError,
        KeyError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ):
        raise SystemExit(
            "Release identity validation failed: preserve state, verify private configuration and local images; choose a fresh state for a different candidate"
        )
