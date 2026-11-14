"""Fail closed before any Compose mutation; never print secret values."""
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit


def validate(path):
    path = Path(path)
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError('acceptance.env must be mode 0600')
    values = dict(line.split('=', 1) for line in path.read_text().splitlines() if line and not line.startswith('#'))
    required = dict(APP_ENV='staging', OFFLINE_MODE='true', UISP_BASE_URL='', UISP_TOKEN='',
        RF_COLLECTION_ENABLED='false', RF_HISTORY_PRUNING_ENABLED='false', UISP_VERIFY_TLS='true',
        API_DOCS_ENABLED='false', REDIS_URL='redis://redis:6379/0')
    for key, expected in required.items():
        if values.get(key) != expected:
            raise ValueError(f'{key} violates isolated acceptance policy')
    for name in ['UISP_ENABLED','MIKROTIK_ENABLED','INTEGRATIONS_ADMIN_APPROVED','RF_SNMP_ENABLED','RF_MICROWAVE_ENABLED','RF_ANALYTICS_RETENTION_ENABLED']:
        if values.get(name,'false').lower()!='false':raise ValueError(f'{name} must remain disabled for acceptance')
    if any(values.get(name) for name in ['MIKROTIK_BASE_URL','MIKROTIK_PASSWORD','UISP_TOKEN_FILE','MIKROTIK_PASSWORD_FILE','RF_SNMP_CONFIG_FILE','RF_MICROWAVE_CONFIG_FILE']):
        raise ValueError('Acceptance must not contain MikroTik endpoints or credentials')
    db = urlsplit(values['DATABASE_URL'])
    if (db.scheme, db.hostname, db.port, db.path, db.username) != ('postgresql+psycopg', 'db', 5432, '/nwo_noc', 'noc_test'):
        raise ValueError('DATABASE_URL must address the isolated db service/nwo_noc as noc_test')
    if db.password != values['POSTGRES_PASSWORD']:
        raise ValueError('Database passwords do not match')
    for key in ['SECRET_KEY', 'ADMIN_PASSWORD', 'POSTGRES_PASSWORD']:
        if len(values.get(key, '')) < 32:
            raise ValueError(f'{key} must contain at least 32 characters')
    if values.get('NOC_RUNTIME_DATABASE_URL'):
        runtime=urlsplit(values['NOC_RUNTIME_DATABASE_URL'])
        if (runtime.scheme,runtime.hostname,runtime.port,runtime.path,runtime.username)!=('postgresql+psycopg','db',5432,'/nwo_noc','noc_app') or runtime.password!=values.get('APP_DATABASE_PASSWORD') or len(values.get('APP_DATABASE_PASSWORD',''))<32:raise ValueError('Runtime database must use the isolated restricted noc_app role')
    if values.get('NOC_FRONTEND_INTERNAL_PORT','80') not in {'80','8080'}:raise ValueError('Unreviewed internal frontend port')
    return values


if __name__ == '__main__':
    try:
        validate(sys.argv[1])
    except (ValueError, KeyError, OSError) as exc:
        raise SystemExit(f'Invalid isolated configuration: {exc}')
