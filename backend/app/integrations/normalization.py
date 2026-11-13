"""Explicit import schemas; never retain arbitrary remote JSON or credentials."""
from datetime import datetime, timezone
import math
import re
from ipaddress import ip_interface
from app.integrations.read_only import IntegrationFailure,secret


def mapping(value):return value if isinstance(value,dict) else {}

def identifier(value,credentials=()):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}',value):raise ValueError('invalid identifier')
    if any(secret(credential) and secret(credential) in value for credential in credentials):raise ValueError('credential in identifier')
    return value


def text(value,*,secrets=(),limit=255):
    if value is None:return None
    if not isinstance(value,str) or len(value)>limit:raise ValueError('invalid text')
    for credential in secrets:
        credential=secret(credential)
        if credential:value=value.replace(credential,'[redacted]')
    return ''.join(c for c in value if c.isprintable())


def number(value,low=0,high=1e15):
    if isinstance(value,bool):return None
    try:n=float(value)
    except (TypeError,ValueError,OverflowError):return None
    return n if math.isfinite(n) and low<=n<=high else None


def parse_dt(value):
    try:
        if isinstance(value,(int,float)) and not isinstance(value,bool):
            return datetime.fromtimestamp(value/1000 if value>1e12 else value,tz=timezone.utc)
        if not isinstance(value,str):return None
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo and result.utcoffset() is not None else None
    except (ValueError,TypeError,OverflowError,OSError):return None


def mac(value):
    if not isinstance(value,str):return None
    value=re.sub('[:-]','',value).upper()
    return ':'.join(value[i:i+2] for i in range(0,12,2)) if re.fullmatch('[0-9A-F]{12}',value) else None


def uisp_device(raw,credentials=()):
    if not isinstance(raw,dict):raise ValueError('invalid device')
    ident=mapping(raw.get('identification'));overview=mapping(raw.get('overview'))
    site=mapping(ident.get('site'));location=mapping(raw.get('location'))
    external=identifier(ident.get('id'),credentials)
    values={'name':text(ident.get('displayName') or ident.get('name') or external,secrets=credentials),
        'hostname':text(ident.get('hostname'),secrets=credentials),'model':text(ident.get('model'),secrets=credentials,limit=120),
        'model_name':text(ident.get('modelName'),secrets=credentials,limit=120),
        'vendor':text(ident.get('vendor') or ident.get('vendorName'),secrets=credentials,limit=120),
        'firmware_version':text(ident.get('firmwareVersion'),secrets=credentials,limit=120),
        'ip_address':str(ip_interface(raw['ipAddress']).ip) if raw.get('ipAddress') else None,
        'mac_address':mac(ident.get('mac')),'authorized':ident.get('authorized') is True,
        'enabled':raw.get('enabled',True) is True,
        'site_id':identifier(site['id'],credentials) if site.get('id') else None,
        'site_name':text(site.get('name'),secrets=credentials),
        'latitude':number(location.get('latitude'),-90,90),'longitude':number(location.get('longitude'),-180,180)}
    observed=parse_dt(overview.get('lastSeen'))
    status=overview.get('status')
    values['status']=status if status in {'active','online','connected','offline','unreachable','disconnected','warning'} and observed else 'unknown'
    return external,values,observed
