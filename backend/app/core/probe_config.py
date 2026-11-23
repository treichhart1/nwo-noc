"""Read probe connection/path values without rebuilding application models.

The installer and worker still validate complete Settings. Container env has the
same precedence as Settings; standalone .env users retain the validated fallback.
"""
import os
from pathlib import Path

DEFAULT_REDIS_URL = "redis://redis:6379/0"
DEFAULT_PLANNING_STORAGE_ROOT = "/var/lib/nwo-noc/planning"


def probe_value(name, default):
    environment = {key.lower(): value for key, value in os.environ.items()}
    field = name.lower()
    if field in environment:
        return environment[field]
    if Path(".env").is_file():
        from app.core.config import settings
        return getattr(settings, field)
    return default
