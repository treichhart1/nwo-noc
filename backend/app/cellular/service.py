"""Bounded local imports; no downloads, proprietary scraping or coverage promises."""
import csv,hashlib,io,json,math
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,text
from app.models.entities import Device,TelemetrySample,AuditLog
from .models import Dataset,SiteObservation
from .contracts import CellularEvidence

OPEN_COLUMNS=['radio','mcc','net','area','cell','unit','lon','lat','range','samples','changeable','created','updated','averageSignal']
REGIONS={'ohio':(-84.9,38.3,-80.5,42.0),'indiana':(-88.1,37.7,-84.7,41.9),'ohio_indiana':(-88.1,37.7,-80.5,42.0)}
KEY='cellular_recorded_v1'


def utc(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _int(value,low,high):
    if not isinstance(value,str) or not value.isascii() or not value.isdigit() or len(value)>16:raise ValueError('Invalid integer field')
    result=int(value)
    if not low<=result<=high:raise ValueError('Out-of-range field')
    return result


def preview(b,*,now=None):
    now=now or datetime.now(timezone.utc)
    if b.observed_at>now:raise ValueError('Future dataset acquisition date')
    expected={'opencellid':'CC-BY-SA-4.0','osm_reviewed':'ODbL-1.0','fcc_asr_reviewed':'operator-reviewed-public-terms'}
    if not b.reviewed_license or b.license!=expected[b.source]:raise ValueError('Explicit source license review required')
    # API payload limit also enforced before parsing. No archive decompression.
    reader=csv.DictReader(io.StringIO(b.csv_text,newline=''))
    columns=OPEN_COLUMNS if b.source=='opencellid' else ['identity','latitude','longitude','observed_at']
    if reader.fieldnames!=columns:raise ValueError('Exact documented CSV header required')
    rows={};duplicates=outside=unsupported=0
    west,south,east,north=REGIONS[b.region]
    for index,r in enumerate(reader):
        if index>=5000:raise ValueError('Partition input into at most5000 rows')
        if None in r or any(v is None or len(v)>180 for v in r.values()):raise ValueError('Malformed bounded CSV row')
        if b.source=='opencellid':
            if r['radio']!='LTE':unsupported+=1;continue # current vendor CSV contract does not establish NR semantics
            _int(r['samples'],0,1000000000)
            if r['unit']:_int(r['unit'],0,503)
            _int(r['changeable'],1,1);_int(r['averageSignal'],0,0)
            mcc=_int(r['mcc'],1,999);mnc=_int(r['net'],0,999);area=_int(r['area'],0,65535);cell=_int(r['cell'],0,2**28-1)
            lat=float(r['lat']);lon=float(r['lon']);radius=_int(r['range'],0,1000000)
            observed=datetime.fromtimestamp(_int(r['updated'],0,2**40),timezone.utc)
            created=datetime.fromtimestamp(_int(r['created'],0,2**40),timezone.utc)
            if created>observed:raise ValueError('Invalid observation history')
            ident=f'LTE:{mcc}:{mnc}:{area}:{cell}'
            row={'identity':ident,'kind':'estimated_cell','technology':'LTE','mcc':mcc,'mnc':mnc,'area':str(area),'cell':str(cell),
                 'latitude':lat,'longitude':lon,'uncertainty_m':radius,'observed_at':observed,'confidence':'estimated'}
        else:
            import re
            if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}',r['identity']):raise ValueError('Invalid source identity')
            lat=float(r['latitude']);lon=float(r['longitude']);observed=datetime.fromisoformat(r['observed_at'])
            if observed.tzinfo is None:raise ValueError('Aware source observation date required')
            row={'identity':r['identity'],'kind':'registered_structure' if b.source=='fcc_asr_reviewed' else 'mapped_structure',
                'technology':None,'latitude':lat,'longitude':lon,'uncertainty_m':None,'observed_at':utc(observed),'confidence':'unverified'}
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Invalid coordinates')
        if observed>now or observed>b.observed_at:raise ValueError('Future or post-acquisition observation')
        if not west<=lon<=east or not south<=lat<=north:outside+=1;continue
        if row['identity'] in rows:
            if rows[row['identity']]!=row:raise ValueError('Conflicting duplicate observation')
            duplicates+=1
        rows[row['identity']]=row
    metadata=b.model_dump(mode='json',exclude={'preview_digest'})
    digest=hashlib.sha256(json.dumps(metadata,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'digest':digest,'rows':list(rows.values()),'count':len(rows),'duplicates':duplicates,'outside_region':outside,
            'unsupported_technology':unsupported,'region_basis':'approximate regional bounding rectangle; not state polygon',
            'source_authenticity':'operator_supplied_not_independently_verified','attribution':b.attribution,'license':b.license}


def import_dataset(db,b,actor,*,now=None):
    result=preview(b,now=now)
    if result['digest']!=b.preview_digest:raise ValueError('Preview fingerprint approval required')
    db.execute(text('SELECT pg_advisory_xact_lock(134000001)'))
    old=db.scalar(select(Dataset).where(Dataset.digest==result['digest']))
    if old:return {'id':old.id,'duplicate':True,'count':old.row_count}
    row=Dataset(digest=result['digest'],source=b.source,version=b.version,attribution=b.attribution,license=b.license,
        approval_reference=b.approval_reference,observed_at=b.observed_at,actor_id=actor,row_count=result['count'])
    db.add(row);db.flush()
    db.add_all([SiteObservation(dataset_id=row.id,**r) for r in result['rows']])
    db.add(AuditLog(user_id=actor,action='cellular.dataset_import',entity_type='dataset',entity_id=str(row.id),
                    details={'count':result['count'],'digest':result['digest'],'source':b.source}))
    db.flush();return {'id':row.id,'duplicate':False,'count':result['count']}


def layers(db,*,west,south,east,north,source=None,technology=None,mcc=None,mnc=None,maximum_age_days=None,confidence=None,limit=300,now=None):
    if not all(math.isfinite(v) for v in (west,south,east,north)) or not -180<=west<east<=180 or not -90<=south<north<=90 or east-west>10 or north-south>10 or not 1<=limit<=500:
        raise ValueError('Bounded valid regional viewport required')
    now=now or datetime.now(timezone.utc)
    q=select(SiteObservation,Dataset).join(Dataset).where(SiteObservation.longitude.between(west,east),SiteObservation.latitude.between(south,north))
    if source:q=q.where(Dataset.source==source)
    if technology:q=q.where(SiteObservation.technology==technology)
    if mcc is not None:q=q.where(SiteObservation.mcc==mcc)
    if mnc is not None:q=q.where(SiteObservation.mnc==mnc)
    if maximum_age_days is not None:q=q.where(SiteObservation.observed_at>=now-timedelta(days=maximum_age_days))
    if confidence:q=q.where(SiteObservation.confidence==confidence)
    rows=db.execute(q.order_by(SiteObservation.observed_at.desc(),SiteObservation.id.desc()).limit(limit+1)).all()
    items=[];attributions={}
    for r,d in rows[:limit]:
        attributions[d.id]={'text':d.attribution,'license':d.license,'source':d.source,'version':d.version,'acquired_at':d.observed_at.isoformat()}
        items.append({'id':r.id,'dataset_id':d.id,'kind':r.kind,'latitude':r.latitude,'longitude':r.longitude,
            'technology':r.technology,'mcc':r.mcc,'mnc':r.mnc,'cell':r.cell,'area':r.area,'band':r.band,
            'uncertainty_m':r.uncertainty_m,'confidence':r.confidence,'observed_at':r.observed_at.isoformat(),
            'stale':now-utc(r.observed_at)>timedelta(days=180),'physical_tower_confirmed':False})
    return {'items':items,'bounded':len(rows)>limit,'attributions':list(attributions.values()),'private':True,'coverage_guaranteed':False}


def distance_bearing(lat,lon,a,b):
    x,y=math.radians(a-lat),math.radians(b-lon)
    h=math.sin(x/2)**2+math.cos(math.radians(lat))*math.cos(math.radians(a))*math.sin(y/2)**2
    distance=6371000*2*math.atan2(math.sqrt(min(1,h)),math.sqrt(max(0,1-h)))
    bearing=(math.degrees(math.atan2(math.sin(y)*math.cos(math.radians(a)),math.cos(math.radians(lat))*math.sin(math.radians(a))-math.sin(math.radians(lat))*math.cos(math.radians(a))*math.cos(y)))+360)%360
    return distance,bearing


def correlate(db,evidence,*,latitude=None,longitude=None,now=None):
    now=now or datetime.now(timezone.utc)
    if (now-evidence.observed_at).total_seconds()<0 or now-evidence.observed_at>timedelta(minutes=10):
        return {'status':'unknown','reason':'stale_or_future_telemetry','candidates':[]}
    if None in (evidence.mcc,evidence.mnc,evidence.area,evidence.cell,evidence.cell_identity_technology):
        return {'status':'unknown','reason':'incomplete_identity','candidates':[]}
    q=select(SiteObservation).where(SiteObservation.kind=='estimated_cell',SiteObservation.mcc==evidence.mcc,
        SiteObservation.mnc==evidence.mnc,SiteObservation.area==str(int(evidence.area)),SiteObservation.cell==str(int(evidence.cell)),
        SiteObservation.technology==evidence.cell_identity_technology)
    rows=db.scalars(q.order_by(SiteObservation.observed_at.desc(),SiteObservation.id.desc()).limit(51)).all()
    # Preserve genuinely conflicting locations but avoid counting version copies as independent candidates.
    candidates={}
    for r in rows[:50]:
        key=(r.latitude,r.longitude)
        if key in candidates:continue
        distance=bearing=None
        if latitude is not None and longitude is not None:distance,bearing=distance_bearing(latitude,longitude,r.latitude,r.longitude)
        candidates[key]={'observation_id':r.id,'latitude':r.latitude,'longitude':r.longitude,'distance_m':distance,'bearing_degrees':bearing,
            'uncertainty_m':r.uncertainty_m,'stale':now-utc(r.observed_at)>timedelta(days=180),'confidence':'candidate_only'}
    return {'status':'unknown' if not candidates else 'ambiguous' if len(candidates)>1 or len(rows)>50 else 'candidate',
        'candidates':list(candidates.values()),'confirmed_serving_tower':False,'service_qualification':'manual_survey_required',
        'reason':'exact technology/MCC/MNC/TAC/full-cell identity; estimated location is not a confirmed physical tower'}


def record_evidence(db,e,actor,*,now=None):
    now=now or datetime.now(timezone.utc)
    if not 0<=(now-e.observed_at).total_seconds()<=3600:raise ValueError('Recent nonfuture observation required')
    d=db.get(Device,e.device_id)
    if d is None or not d.enabled or e.source_kind=='synthetic' and d.source!='synthetic':raise ValueError('Matching existing inventory required')
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':861000+d.id})
    old=db.scalar(select(TelemetrySample).where(TelemetrySample.device_id==d.id,TelemetrySample.sampled_at==e.observed_at,TelemetrySample.raw_data[KEY].is_not(None)))
    data=e.model_dump(mode='json')
    if old:
        if old.raw_data[KEY]!=data:raise ValueError('Conflicting immutable cellular observation')
        return old
    # No projection into live incidents from unverified/manual observations.
    row=TelemetrySample(device_id=d.id,sampled_at=e.observed_at,raw_data={KEY:data});db.add(row);db.flush()
    db.add(AuditLog(user_id=actor,action='cellular.evidence_recorded',entity_type='device',entity_id=str(d.id),details={'sample_id':row.id,'source':e.source_kind}))
    return row


def device_history(db,device_id,*,now=None):
    now=now or datetime.now(timezone.utc)
    if db.get(Device,device_id) is None:raise LookupError('Device not found')
    rows=db.scalars(select(TelemetrySample).where(TelemetrySample.device_id==device_id,TelemetrySample.raw_data[KEY].is_not(None)).order_by(TelemetrySample.sampled_at.desc(),TelemetrySample.id.desc()).limit(120)).all()
    samples=[];invalid=0
    for r in reversed(rows):
        try:
            e=CellularEvidence.model_validate(r.raw_data[KEY])
            if e.device_id!=device_id or e.observed_at!=utc(r.sampled_at):raise ValueError()
            samples.append(e)
        except (ValueError,TypeError):invalid+=1
    changes=[]
    for a,b in zip(samples,samples[1:]):
        if (a.interface_id,a.source_kind)!=(b.interface_id,b.source_kind):continue
        if (a.cell,a.area,a.mcc,a.mnc,a.cell_identity_technology)!=(b.cell,b.area,b.mcc,b.mnc,b.cell_identity_technology):
            changes.append({'at':b.observed_at.isoformat(),'event':'observed_identity_change','source':b.source_kind})
    fresh=bool(samples and 0<=(now-samples[-1].observed_at).total_seconds()<=600 and not invalid)
    return {'device_id':device_id,'samples':[e.model_dump(mode='json') for e in samples],'invalid_samples':invalid,
        'identity_changes':changes,'freshness':'fresh' if fresh else 'unknown','native_monitor':'BLOCKED: source minimization contract required',
        'registration':samples[-1].registration if fresh else 'unknown','service_availability':'not_guaranteed'}


def qualification_advisory(db,latitude,longitude,technology,*,now=None):
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not -89<=latitude<=89 or not -179<=longitude<=179:
        raise ValueError('Valid qualification coordinates required')
    data=layers(db,west=longitude-.5,east=longitude+.5,south=latitude-.5,north=latitude+.5,
        technology='LTE' if technology=='lte' else 'NR',limit=100,now=now)
    candidates=[]
    for r in data['items']:
        if r['kind']!='estimated_cell':continue
        distance,bearing=distance_bearing(latitude,longitude,r['latitude'],r['longitude'])
        if distance<=25000:
            candidates.append({'observation_id':r['id'],'dataset_id':r['dataset_id'],'approximate_distance_m':round(distance),
                'approximate_bearing_degrees':round(bearing,1),'confidence':r['confidence'],'stale':r['stale'],
                'carrier_mcc':r['mcc'],'carrier_mnc':r['mnc'],'carrier_availability_verified':False})
    return {'status':'manual_review_required','survey_required':True,'service_guaranteed':False,'capacity':'unavailable',
        'candidates':sorted(candidates,key=lambda r:r['approximate_distance_m'])[:20],'bounded':data['bounded'],
        'attributions':data['attributions'],'basis':'Open estimated observations are not coverage or a confirmed serving tower',
        'technology':technology,'five_g_dataset_contract':'unverified' if technology=='5g' else None}
