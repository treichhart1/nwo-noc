"""Read the existing planning heartbeat without starting ORM/GIS work."""

import time
from .storage_path import root
from app.core.probe_config import DEFAULT_PLANNING_STORAGE_ROOT, probe_value


def main():
    p = root(probe_value("PLANNING_STORAGE_ROOT", DEFAULT_PLANNING_STORAGE_ROOT)) / "worker.heartbeat"
    if p.is_symlink() or not p.exists() or time.time() - p.stat().st_mtime > 150:
        raise SystemExit("Planning heartbeat stale/missing")
