#!/usr/bin/env python3
"""Build only the reviewed cancellation plugin, reproducibly."""

import argparse, importlib.util, json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "signup_builder", root / "scripts/signup/build.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def build(root, output):
    return module.build(root, output, plugin="nwo-customer-cancellation")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    print(json.dumps(build(root, p.parse_args().output), indent=2))
