"""Redis heartbeat health check for the background worker."""
import time
from pathlib import Path
from app.services.worker_runtime import ID_FILE
from redis import Redis
from app.core.config import settings


def main():
    worker=Path(ID_FILE).read_text().strip() if Path(ID_FILE).exists() else None
    key='noc:worker:'+worker+':heartbeat' if worker else 'noc:worker:heartbeat'
    value = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=3).get(key)
    if value is None or not 0 <= time.time() - float(value) < 60:
        raise SystemExit('Worker heartbeat missing/stale; inspect worker and Redis logs')


if __name__ == '__main__':
    main()
