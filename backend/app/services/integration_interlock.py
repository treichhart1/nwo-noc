"""Shared persistent, fail-closed operator stop latch; no device commands."""

import os, stat
from pathlib import Path
from app.core.config import settings

FLAG = "integrations.disabled"


def disabled():
    p = Path(settings.rf_scheduler_spool_dir) / FLAG
    try:
        s = p.lstat()
        return True  # Any existing latch, including malformed/symlink, stops access.
    except FileNotFoundError:
        return False
    except OSError:
        return True


def activate():
    from app.services.monitoring_spool import directory

    root = directory(settings.rf_scheduler_spool_dir)
    p = root / FLAG
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("disabled\n")
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        pass
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
