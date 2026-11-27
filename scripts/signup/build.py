#!/usr/bin/env python3
"""Reproducible, dependency-free WordPress plugin package; never includes secrets."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

FILES = (
    "nwo-customer-signup.php",
    "transport.php",
    "uninstall.php",
    "block.json",
    "assets/editor.js",
    "assets/signup.css",
    "readme.txt",
)


def build(root: Path, output: Path, *, plugin="nwo-customer-signup") -> dict:
    if plugin not in {"nwo-customer-signup", "nwo-customer-cancellation"}:
        raise ValueError("Unknown reviewed plugin")
    cancellation = plugin == "nwo-customer-cancellation"
    files = tuple(n.replace("nwo-customer-signup.php", "nwo-customer-cancellation.php").replace("signup.css", "cancellation.css") for n in FILES) if cancellation else FILES
    source = root / "wordpress" / plugin
    output.mkdir(parents=True, exist_ok=True)
    target = output / (plugin + ".zip")
    entries = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(files):
            path = source / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Missing or unsafe plugin member: {name}")
            data = path.read_bytes()
            info = zipfile.ZipInfo(plugin + "/" + name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            entries.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / (plugin + ".zip.sha256")).write_text(
        f"{digest}  {plugin}.zip\n"
    )
    evidence = {
        "schema_version": 1,
        "version": "131.0.0" if cancellation else "129.0.0",
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
