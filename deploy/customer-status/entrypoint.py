"""Production gateway bootstrap: never inherit private NOC application credentials."""
import os,stat
from pathlib import Path
from urllib.parse import urlsplit,parse_qs

def private(path):
 p=Path(path)
 if not p.is_absolute() or any(x.is_symlink() for x in [p,*p.parents]):raise ValueError()
 st=p.stat()
 if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o077 or not 32<=st.st_size<=4096:raise ValueError()
 return p.read_text().strip()
try:
 url=private('/run/secrets/status_db_url');key=private('/run/secrets/status_rate_key');u=urlsplit(url)
 if u.scheme!='postgresql+psycopg' or u.username!='noc_status_public' or not u.password or not u.hostname or u.path!='/nwo_noc' or parse_qs(u.query).get('sslmode')!=['verify-full']:raise ValueError()
 # These independent, gateway-only values satisfy shared configuration validation.
 # No private NOC JWT key, device credentials or provider credentials are passed.
 os.environ.update(DATABASE_URL=url,SECRET_KEY=key,ADMIN_PASSWORD=key,CUSTOMER_STATUS_RATE_KEY_FILE='/run/secrets/status_rate_key',OFFLINE_MODE='true')
 os.execvp('uvicorn',['uvicorn','app.customer_status.public_app:app','--host','0.0.0.0','--port','8002','--no-access-log','--proxy-headers','--forwarded-allow-ips',os.environ.get('NOC_STATUS_TRUSTED_PROXIES','127.0.0.1')])
except (OSError,ValueError):raise SystemExit('Public status gateway configuration rejected: require owned0600 credential files, restricted gateway DB user and verify-full PostgreSQL TLS. No credentials are logged.') from None
