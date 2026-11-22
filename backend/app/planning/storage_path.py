"""Private planning storage path without GIS/database initialization."""

from pathlib import Path


def root():
    from app.core.config import settings

    p = Path(settings.planning_storage_root)
    if p.is_symlink():
        raise ValueError("Unsafe terrain storage")
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p.resolve()
