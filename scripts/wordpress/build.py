#!/usr/bin/env python3
"""Reproducible, dependency-free WordPress plugin package; never includes secrets."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

FILES = (
    "nwo-network-status.php",
    "validation.php",
    "transport.php",
    "uninstall.php",
    "block.json",
    "assets/editor.js",
    "assets/status.js",
    "assets/status.css",
    "readme.txt",
)


def build(root: Path, output: Path) -> dict:
    source = root / "wordpress/nwo-network-status"
    output.mkdir(parents=True, exist_ok=True)
    target = output / "nwo-network-status.zip"
    entries = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(FILES):
            path = source / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Missing or unsafe plugin member: {name}")
            data = path.read_bytes()
            info = zipfile.ZipInfo("nwo-network-status/" + name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            entries.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / "nwo-network-status.zip.sha256").write_text(
        f"{digest}  nwo-network-status.zip\n"
    )
    evidence = {
        "schema_version": 1,
        "version": "128.0.0",
        "sha256": digest,
        "members": entries,
    }
    (output / "plugin-manifest.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[2], args.output), indent=2))
