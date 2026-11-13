"""PostgreSQL microwave staging/history with explicit inventory link association."""
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,text
from app.models.entities import Device,NetworkLink,Site,TelemetrySample,AuditLog
from app.services.microwave_model import MicrowaveObservation,PROFILES
from app.services.microwave_diagnostics import analyze_microwave
from app.integrations.read_only import ERROR_CODES


def validate_association(device,link,profile):
    aliases={'racom':{'racom'},'aviat':{'aviat','aviat networks'}}
    if not device.id or not link or device.id not in {link.a_device_id,link.z_device_id}:raise ValueError('Explicit endpoint/link association required')
    if (device.vendor or '').casefold() not in aliases[PROFILES[profile]]:raise ValueError('Microwave vendor mismatch')


def stage_microwave(db,device,link,observation):
    if not isinstance(observation,MicrowaveObservation):raise ValueError('Validated microwave observation required')
    validate_association(device,link,observation.profile)
    if not observation.original_values:return None
    if db.get_bind().dialect.name=='postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':861000+device.id})
    existing=db.scalar(select(TelemetrySample).where(TelemetrySample.device_id==device.id,
        TelemetrySample.sampled_at==observation.observed_at,
        TelemetrySample.raw_data['rf_microwave_v1']['profile'].as_string()==observation.profile).limit(1))
    payload=observation.model_dump(mode='json')
    if existing:
        compare=lambda p:{k:v for k,v in p.items() if k!='received_at'}
        if existing.raw_data.get('microwave_link_id')!=link.id or compare(existing.raw_data['rf_microwave_v1'])!=compare(payload):raise ValueError('Conflicting microwave sample')
        return existing
    # No carrier chosen as an artificial device aggregate; do not project the
    # first channel or summed capacity into legacy one-radio diagnostics.
    row=TelemetrySample(device_id=device.id,sampled_at=observation.observed_at,
        raw_data={'rf_microwave_v1':payload,'microwave_link_id':link.id})
    db.add(row);db.flush();return row


def parse_microwave_row(row):
    obs=MicrowaveObservation.model_validate((row.raw_data or {})['rf_microwave_v1'])
    when=row.sampled_at.replace(tzinfo=timezone.utc) if row.sampled_at.tzinfo is None else row.sampled_at
    if obs.observed_at!=when:raise ValueError('Microwave timestamp mismatch')
    link_id=(row.raw_data or {}).get('microwave_link_id')
    if isinstance(link_id,bool) or not isinstance(link_id,int) or link_id<1:raise ValueError('Invalid microwave link identifier')
    return obs


def microwave_history(db,device_id,*,now=None,limit=288,thresholds=None):
    if not 1<=limit<=1000:raise ValueError('Invalid microwave history limit')
    device=db.get(Device,device_id)
    if device is None:raise LookupError('device_not_found')
    now=now or datetime.now(timezone.utc)
    rows=list(db.scalars(select(TelemetrySample).where(TelemetrySample.device_id==device_id,
        TelemetrySample.sampled_at>=now-timedelta(days=7),TelemetrySample.raw_data['rf_microwave_v1'].is_not(None))
        .order_by(TelemetrySample.sampled_at.desc(),TelemetrySample.id.desc()).limit(limit)))
    unique={};invalid=0;link_ids=set()
    for row in reversed(rows):
        try:
            obs=parse_microwave_row(row);link_id=row.raw_data['microwave_link_id']
            if isinstance(link_id,bool) or not isinstance(link_id,int) or link_id<1:raise ValueError()
            unique[obs.observed_at]=(obs,link_id);link_ids.add(link_id)
        except (ValueError,KeyError,TypeError):invalid+=1
    records=[unique[t] for t in sorted(unique)]
    # Reassignment to another topology link is a new history segment.
    current=[]
    for obs,link_id in records:
        if current and link_id!=current[-1][1]:current=[]
        current.append((obs,link_id))
    report=analyze_microwave([o for o,_ in current],now=now,thresholds=thresholds)
    links=list(db.scalars(select(NetworkLink).where(NetworkLink.id.in_(link_ids)))) if link_ids else []
    sites=list(db.scalars(select(Site).where(Site.id.in_({s for l in links for s in (l.a_site_id,l.z_site_id) if s})))) if links else []
    names={s.id:s.name for s in sites}
    topology=[{'id':l.id,'name':l.name,'a_site':names.get(l.a_site_id),'z_site':names.get(l.z_site_id),
        'a_device_id':l.a_device_id,'z_device_id':l.z_device_id,'endpoint_matches':device_id in {l.a_device_id,l.z_device_id}} for l in links]
    association_ok=bool(current) and any(l['id']==current[-1][1] and l['endpoint_matches'] for l in topology)
    if association_ok:
        try:validate_association(device,next(l for l in links if l.id==current[-1][1]),current[-1][0].profile)
        except ValueError:association_ok=False
    if invalid or not association_ok:report.update(state='unknown',rf_state='unknown',reasons=[])
    attempt=db.scalar(select(AuditLog).where(AuditLog.action=='microwave.read_only_poll',AuditLog.entity_id==str(device_id))
                      .order_by(AuditLog.created_at.desc(),AuditLog.id.desc()).limit(1))
    result=(attempt.details or {}).get('result') if attempt else None
    def serialize(obs,link_id):
        data=obs.model_dump(mode='json')
        for key,carrier in obs.carriers.items():data['carriers'][key]['errors_exact']=str(carrier.errors) if carrier.errors is not None else None
        data['original_display_values']={k:str(v) if isinstance(v,int) and abs(v)>2**53-1 else v for k,v in obs.original_values.items()}
        return data|{'network_link_id':link_id}
    return {'device_id':device_id,'read_only':True,'invalid_samples':invalid,'association_valid':association_ok,
        'topology':topology,'samples':[serialize(o,l) for o,l in records],
        'last_attempt':{'at':attempt.created_at.isoformat(),'result':result if result in ERROR_CODES|{'success'} else 'sync_failed'} if attempt else None,
        'diagnostics':report}
