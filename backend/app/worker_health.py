"""Redis heartbeat health check for the background worker."""
import time
from redis import Redis
from app.core.config import settings


def main():
    value = Redis.from_url(settings.redis_url, socket_timeout=3).get('noc:worker:heartbeat')
    if value is None or not 0 <= time.time() - float(value) < 60:
        raise SystemExit('Worker heartbeat missing/stale; inspect worker and Redis logs')


if __name__ == '__main__':
    main()
