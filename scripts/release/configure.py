"""Generate fresh nonproduction credentials. Never overwrite configuration."""
import os
import secrets
import sys
from pathlib import Path


def generate(directory):
    path = Path(directory).resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    password = secrets.token_hex(24)
    values = dict(APP_ENV='staging', OFFLINE_MODE='true', POSTGRES_PASSWORD=password,
        SECRET_KEY=secrets.token_hex(32), ADMIN_USERNAME='acceptance',
        ADMIN_PASSWORD=secrets.token_hex(24), ADMIN_EMAIL='acceptance@example.invalid',
        DATABASE_URL=f'postgresql+psycopg://noc_test:{password}@db:5432/nwo_noc',
        REDIS_URL='redis://redis:6379/0', UISP_BASE_URL='', UISP_TOKEN='',
        UISP_VERIFY_TLS='true', RF_COLLECTION_ENABLED='false', RF_HISTORY_PRUNING_ENABLED='false',
        API_DOCS_ENABLED='false', CORS_ORIGINS='http://127.0.0.1:18090')
    target = path / 'acceptance.env'
    with open(target, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        handle.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    return target


if __name__ == '__main__':
    try:
        print(generate(sys.argv[1]))
    except FileExistsError:
        raise SystemExit('Configuration exists; refusing to overwrite. Reuse it or choose a new directory.')
