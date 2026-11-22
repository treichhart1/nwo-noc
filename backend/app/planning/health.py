"""Read the existing planning heartbeat without starting ORM/GIS work."""

import time
from .storage_path import root


def main():
    p = root() / "worker.heartbeat"
    if p.is_symlink() or not p.exists() or time.time() - p.stat().st_mtime > 150:
        raise SystemExit("Planning heartbeat stale/missing")
