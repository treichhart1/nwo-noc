"""Private planning storage path without GIS/database initialization."""

from pathlib import Path


def root(value=None):
    if value is None:
        from app.core.config import settings
        value = settings.planning_storage_root
    p = Path(value)
    if p.is_symlink():
        raise ValueError("Unsafe terrain storage")
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p.resolve()
