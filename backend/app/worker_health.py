"""Redis heartbeat health check for the background worker."""

import time
from pathlib import Path
from app.services.worker_identity import ID_FILE
from redis import Redis
from app.core.probe_config import DEFAULT_REDIS_URL, probe_value


def main():
    worker = Path(ID_FILE).read_text().strip() if Path(ID_FILE).exists() else None
    key = "noc:worker:" + worker + ":heartbeat" if worker else "noc:worker:heartbeat"
    value = Redis.from_url(
        probe_value("REDIS_URL", DEFAULT_REDIS_URL), socket_connect_timeout=2, socket_timeout=3
    ).get(key)
    if value is None or not 0 <= time.time() - float(value) < 60:
        raise SystemExit(
            "Worker heartbeat missing/stale; inspect worker and Redis logs"
        )


if __name__ == "__main__":
    main()
