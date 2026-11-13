"""RouterOS REST read-only collection. No CLI, POST, PATCH or config operations."""
from datetime import datetime, timezone
import re
import hashlib
import json
from sqlalchemy import select,text as sql
from app.core.config import settings
from app.models.entities import Device,Integration,IntegrationBinding,TelemetrySample,MonitorCheck,AuditLog
from app.integrations.read_only import ReadClient,ReadPolicy,MIKROTIK_PATHS,IntegrationFailure,secret
from app.integrations.normalization import mapping,text,number


def uptime_seconds(value):
    numeric=number(value,0,315360000)
    if numeric is not None:return numeric
    if not isinstance(value,str):return None
    parts=re.fullmatch(r'(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?',value)
    if parts and any(parts.groups()):return sum(int(v or 0)*scale for v,scale in zip(parts.groups(),[604800,86400,3600,60,1]))
    parts=re.fullmatch(r'(?:(\d+)w)?(?:(\d+)d)?(\d\d):(\d\d):(\d\d)',value)
    if parts:return sum(int(v or 0)*scale for v,scale in zip(parts.groups(),[604800,86400,3600,60,1]))
    return None



def counter(value):
    if isinstance(value,bool):return None
    if isinstance(value,int):return value if 0<=value<=2**64-1 else None
    if isinstance(value,str) and re.fullmatch(r'\d{1,20}',value):
        result=int(value);return result if result<=2**64-1 else None
    return None


def flag(value):
    return True if value is True or value=='true' else False if value is False or value=='false' else None


def one(payload):
    if isinstance(payload,list) and len(payload)==1:payload=payload[0]
    if not isinstance(payload,dict):raise IntegrationFailure('invalid_response')
    return payload


def normalize(payload,credentials=()):
    identity=one(payload['identity']);resource=one(payload['resource'])
    if not isinstance(identity.get('name'),str) or not identity['name'] or not isinstance(resource.get('version'),str) or not resource['version'].strip():raise IntegrationFailure('invalid_response')
    interfaces=payload['interfaces']
    if not isinstance(interfaces,list) or len(interfaces)>4096:raise IntegrationFailure('invalid_response')
    normalized=[]
    for raw in interfaces:
        if not isinstance(raw,dict) or not isinstance(raw.get('name'),str):raise IntegrationFailure('invalid_response')
        normalized.append({'id':text(raw.get('.id') or raw['name'],secrets=credentials,limit=120),
            'name':text(raw['name'],secrets=credentials,limit=120),
            'running':flag(raw.get('running')),
            'disabled':flag(raw.get('disabled')),
            **{target:counter(raw.get(source)) for source,target in
                [('rx-byte','rx_bytes'),('tx-byte','tx_bytes'),('rx-error','rx_errors'),('tx-error','tx_errors'),('rx-drop','rx_drops'),('tx-drop','tx_drops')]}})
    neighbors=payload.get('neighbors',[])
    if not isinstance(neighbors,list) or len(neighbors)>4096:raise IntegrationFailure('invalid_response')
    ospf=[]
    for raw in neighbors:
        if not isinstance(raw,dict):raise IntegrationFailure('invalid_response')
        state=raw.get('state')
        ospf.append({'router_id':text(raw.get('router-id'),secrets=credentials,limit=120),
            'state':state if state in {'Full','full','Down','down','Init','init','2-Way','2-way','ExStart','Exchange','Loading'} else 'unknown'})
    total=counter(resource.get('total-memory'));free=counter(resource.get('free-memory'))
    temperature=None
    health=payload.get('health',[])
    if isinstance(health,dict):health=[health]
    if not isinstance(health,list):raise IntegrationFailure('invalid_response')
    for sensor in health:
        if isinstance(sensor,dict) and sensor.get('name') in {'temperature','cpu-temperature'}:
            temperature=number(sensor.get('value'),-100,200)
    return {'identity':text(identity['name'],secrets=credentials),'routeros_version':text(resource['version'],secrets=credentials,limit=120),
        'cpu_percent':number(resource.get('cpu-load'),0,100),'total_memory_bytes':total,'free_memory_bytes':free,
        'ram_percent':round((1-free/total)*100,2) if total and free is not None and free<=total else None,
        'uptime_seconds':uptime_seconds(resource.get('uptime')),'temperature_c':temperature,
        'interfaces':normalized,'ospf_neighbors':ospf}


def collect_mikrotik(db,*,transport=None,now=None):
    now=now or datetime.now(timezone.utc)
    if settings.offline_mode or not settings.mikrotik_enabled or not settings.integrations_admin_approved:raise IntegrationFailure('disabled')
    if not settings.mikrotik_device_id or not settings.mikrotik_username or not secret(settings.mikrotik_password):raise IntegrationFailure('configuration_invalid')
    policy=ReadPolicy(settings.mikrotik_base_url,tuple(settings.mikrotik_allowed_origins.split(',')),MIKROTIK_PATHS,True,True)
    policy.validate()
    state=db.scalar(select(Integration).where(Integration.name=='mikrotik'))
    if state is None:state=Integration(name='mikrotik');db.add(state);db.flush()
    try:
        if db.get_bind().dialect.name=='postgresql' and not db.scalar(sql('SELECT pg_try_advisory_xact_lock(860002)')):raise IntegrationFailure('overlap_skipped')
        state.last_attempt_at=now
        device=db.get(Device,settings.mikrotik_device_id)
        if device is None:raise IntegrationFailure('configuration_invalid')
        payload={};errors=[]
        with ReadClient(policy,auth=(settings.mikrotik_username,secret(settings.mikrotik_password)),transport=transport) as client:
            for name,path in [('identity','/rest/system/identity'),('resource','/rest/system/resource'),('interfaces','/rest/interface'),('health','/rest/system/health'),('neighbors','/rest/routing/ospf/neighbor')]:
                try:payload[name]=client.request(path)[0]
                except IntegrationFailure as exc:
                    if name not in {'health','neighbors'} or exc.code!='unsupported':raise
                    payload[name]=[];errors.append('unsupported')
        values=normalize(payload,(settings.mikrotik_password,))
        external=str(device.id)
        row=db.scalar(select(IntegrationBinding).where(IntegrationBinding.integration=='mikrotik',IntegrationBinding.kind=='device',IntegrationBinding.external_id==external))
        if row and row.observed_at and now<=row.observed_at:raise IntegrationFailure('invalid_response')
        previous=db.scalar(select(TelemetrySample).where(TelemetrySample.device_id==device.id,
            TelemetrySample.sampled_at==row.observed_at,
            TelemetrySample.raw_data['mikrotik_v1'].is_not(None)).order_by(TelemetrySample.id.desc()).limit(1)) if row and row.observed_at else None
        if previous and previous.sampled_at>=now:raise IntegrationFailure('invalid_response')
        before=(previous.raw_data.get('mikrotik_v1') or {}) if previous else {}
        if previous and hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()!=row.digest:
            before={};errors.append('invalid_response')
        old={i['id']:i for i in before.get('interfaces',[])}
        reset=bool(before.get('uptime_seconds') is not None and values['uptime_seconds'] is not None and values['uptime_seconds']<before['uptime_seconds'])
        for interface in values['interfaces']:
            earlier=old.get(interface['id'],{})
            for key in ('rx_bytes','tx_bytes','rx_errors','tx_errors','rx_drops','tx_drops'):
                a,b=interface.get(key),earlier.get(key)
                interface[key+'_delta']=a-b if before.get('uptime_seconds') is not None and values['uptime_seconds'] is not None and not reset and a is not None and b is not None and a>=b else None
        values['firmware_conflict']=bool(device.firmware_version and device.firmware_version.strip() and device.firmware_version.split()[0]!=values['routeros_version'].split()[0])
        values['counter_reset']=reset
        values['degraded']=any(i['disabled'] is False and (i['running'] is False or any((i.get(k+'_delta') or 0)>0 for k in ('rx_errors','tx_errors','rx_drops','tx_drops'))) for i in values['interfaces']) or any(n['state'].lower()!='full' for n in values['ospf_neighbors'])
        db.add(TelemetrySample(device_id=device.id,sampled_at=now,cpu_percent=values['cpu_percent'],ram_percent=values['ram_percent'],temperature_c=values['temperature_c'],raw_data={'mikrotik_v1':values}))
        db.add(MonitorCheck(device_id=device.id,status='up',error=None,checked_at=now,source='mikrotik-read-only'))
        if row is None:
            row=IntegrationBinding(integration='mikrotik',kind='device',external_id=external,device_id=device.id);db.add(row)
        row.observed_at=row.received_at=now;row.values={'identity':values['identity'],'routeros_version':values['routeros_version']}
        row.digest=hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
        state.status='degraded' if errors or values['firmware_conflict'] else 'connected';state.last_error='partial_sync' if state.status=='degraded' else None
        state.last_sync_at=now
        if state.status=='connected':state.last_success_at=now
        state.last_item_count=state.last_received_count=1;state.last_skipped_count=len(errors)+int(values['firmware_conflict'])
        db.add(AuditLog(action='integration.mikrotik.read_collect',entity_type='device',entity_id=str(device.id),details={'read_only':True,'degraded':values['degraded'],'validation_issues':len(errors),'firmware_conflict':values['firmware_conflict']}))
        db.commit();return {'ok':True,'imported':1,'degraded':values['degraded']}
    except Exception as exc:
        code=exc.code if isinstance(exc,IntegrationFailure) else 'sync_failed'
        db.rollback();state=db.scalar(select(Integration).where(Integration.name=='mikrotik'))
        if state is None:state=Integration(name='mikrotik');db.add(state)
        state.status='error';state.last_attempt_at=now;state.last_error=code;db.commit()
        raise IntegrationFailure(code) from None
