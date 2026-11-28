from datetime import datetime,timezone
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Path
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.api.deps import require_admin,require_operator
from app.core.database import get_db
from app.models.entities import Device,TelemetrySample
from app.ipam.contracts import Reason,DiscoveryInput
from app.ipam import discovery
from .contracts import Closed,DatasetInput,CellularEvidence
from .models import Dataset
from . import service as s

router=APIRouter(prefix='/api/cellular',tags=['Private cellular and advanced router observations'],dependencies=[Depends(require_operator)])


def run(db,fn,write=False):
    try:
        result=fn()
        if write:db.commit()
        return result
    except LookupError:
        db.rollback();raise HTTPException(404,'Observation not found') from None
    except (ValueError,TypeError,OverflowError):
        db.rollback();raise HTTPException(409,'Check approved source, bounded data, preview fingerprint and canonical identifiers') from None
    except SQLAlchemyError:
        db.rollback();raise HTTPException(503,'Observation storage unavailable') from None


@router.post('/datasets/preview')
def preview(b:DatasetInput,db=Depends(get_db),u=Depends(require_admin)):
    def work():
        result=s.preview(b);result.pop('rows');return result
    return run(db,work)


@router.post('/datasets/import')
def import_dataset(b:DatasetInput,db=Depends(get_db),u=Depends(require_admin)):
    return run(db,lambda:s.import_dataset(db,b,u.id),True)


@router.get('/datasets')
def datasets(db=Depends(get_db)):
    return run(db,lambda:[{'id':r.id,'source':r.source,'version':r.version,'attribution':r.attribution,'license':r.license,
        'observed_at':r.observed_at,'imported_at':r.imported_at,'count':r.row_count} for r in db.scalars(select(Dataset).order_by(Dataset.id.desc()).limit(100))])


@router.get('/layers')
def layers(west:float=Query(-88.1,ge=-180,le=180),south:float=Query(37.7,ge=-90,le=90),east:float=Query(-80.5,ge=-180,le=180),north:float=Query(42,ge=-90,le=90),
           source:Literal['opencellid','fcc_asr_reviewed','osm_reviewed']|None=None,technology:Literal['LTE','NR']|None=None,
           mcc:int|None=Query(None,ge=1,le=999),mnc:int|None=Query(None,ge=0,le=999),
           maximum_age_days:int|None=Query(None,ge=0,le=36500),confidence:Literal['estimated','unverified']|None=None,
           limit:int=Query(300,ge=1,le=500),db=Depends(get_db)):
    return run(db,lambda:s.layers(db,west=west,south=south,east=east,north=north,source=source,technology=technology,
        mcc=mcc,mnc=mnc,maximum_age_days=maximum_age_days,confidence=confidence,limit=limit))


@router.post('/evidence')
def record(b:CellularEvidence,db=Depends(get_db),u=Depends(require_admin)):
    def work():return {'id':s.record_evidence(db,b,u.id).id,'native':False}
    return run(db,work,True)


@router.get('/devices/{device_id}/history')
def history(device_id:int=Path(gt=0),db=Depends(get_db)):
    return run(db,lambda:s.device_history(db,device_id))


class Correlate(Closed):
    evidence:CellularEvidence
    latitude:float|None=Field(None,ge=-90,le=90,allow_inf_nan=False)
    longitude:float|None=Field(None,ge=-180,le=180,allow_inf_nan=False)


@router.post('/correlate')
def correlate(b:Correlate,db=Depends(get_db)):
    return run(db,lambda:s.correlate(db,b.evidence,latitude=b.latitude,longitude=b.longitude))


@router.get('/devices/{device_id}/advanced')
def advanced(device_id:int=Path(gt=0),db=Depends(get_db)):
    def read():
        d=db.get(Device,device_id)
        if d is None:raise LookupError()
        query=select(TelemetrySample).where(TelemetrySample.device_id==device_id,
            TelemetrySample.raw_data['mikrotik_v1'].is_not(None)).order_by(TelemetrySample.sampled_at.desc(),TelemetrySample.id.desc()).limit(2)
        records=db.scalars(query).all()
        row=records[0] if records else None
        value=(row.raw_data.get('mikrotik_v1') or {}).get('advanced') if row else None
        if value is not None:
            from app.integrations.mikrotik_advanced import AdvancedSnapshot
            value=AdvancedSnapshot.model_validate(value).model_dump()
        fresh=bool(row and 0<=(datetime.now(timezone.utc)-s.utc(row.sampled_at)).total_seconds()<=600)
        transitions={'state':'baseline','events':[]}
        rates=[]
        if len(records)==2:
            current=records[0].raw_data['mikrotik_v1'];previous=records[1].raw_data['mikrotik_v1']
            elapsed=(s.utc(records[0].sampled_at)-s.utc(records[1].sampled_at)).total_seconds()
            continuity=previous.get('uptime_seconds') is not None and current.get('uptime_seconds') is not None and current['uptime_seconds']>=previous['uptime_seconds']
            if value and previous.get('advanced'):
                from app.integrations.mikrotik_advanced import transition_evidence,AdvancedSnapshot
                old=AdvancedSnapshot.model_validate(previous['advanced']).model_dump()
                transitions=transition_evidence(old,value,elapsed_seconds=elapsed,uptime_continuous=continuity and fresh)
            old_interfaces={i['id']:i for i in previous.get('interfaces',[])}
            for iface in current.get('interfaces',[]):
                old=old_interfaces.get(iface['id'],{});measurements={}
                for direction in ('rx','tx'):
                    a=old.get(direction+'_bytes');b=iface.get(direction+'_bytes')
                    measurements[direction+'_mbps']=(b-a)*8/elapsed/1e6 if fresh and continuity and 0<elapsed<=600 and a is not None and b is not None and b>=a else None
                rates.append({'id':iface['id'],**measurements})
        return {'device_id':device_id,'freshness':'fresh' if fresh else 'unknown','observation':value,
            'transitions':transitions,'interface_rates':rates,
            'sample_id':row.id if row else None,'native_compatibility':'documented contracts; hardware acceptance not performed',
            'management_tunnel_availability':'unknown','no_configuration_writes':True}
    return run(db,read)


class NativeReview(Reason):
    device_id:int=Field(gt=0)
    sample_id:int=Field(gt=0)
    vrf_id:int=Field(gt=0)
    preview_digest:str|None=Field(None,pattern=r'^[a-f0-9]{64}$')
    routing_domain:str=Field('main',pattern=r'^[A-Za-z0-9_.:-]{1,100}$')


def native_preview(db,b):
    import hashlib,json
    row=db.get(TelemetrySample,b.sample_id)
    if row is None or row.device_id!=b.device_id:raise ValueError()
    if not 0<=(datetime.now(timezone.utc)-s.utc(row.sampled_at)).total_seconds()<=3600:raise ValueError()
    values=row.raw_data.get('mikrotik_v1') or {};advanced=values.get('advanced') or {}
    resources=advanced.get('resources') or {};responses={}
    # Existing native collector's fixed properties; never execute discovery from this API.
    for name in ('addresses','ipv6_addresses','vlans','routes','ipv6_routes','tables','arp','leases','dhcp_networks'):
        if name in resources:responses[discovery.PATHS[name]+'/print']=resources[name]
    if not responses:raise ValueError()
    vrfs=resources.get('vrfs7',resources.get('vrfs6'))
    if vrfs is None:raise ValueError('VRF evidence unavailable')
    interface_domains={}
    for vrf in vrfs:
        domain=vrf.get('name') or vrf.get('routing-mark') or 'main'
        if domain=='main':continue
        members=vrf.get('interfaces','').split(',')
        if any(not v or v=='all' or any(c in v for c in '*?!') for v in members):raise ValueError('Review ambiguous interface domain')
        for iface in members:
            if iface in interface_domains and interface_domains[iface]!=domain:raise ValueError('Ambiguous VRF')
            interface_domains[iface]=domain
    filtered={}
    for path,items in responses.items():
        selected=[]
        for item in items:
            if path in {'/ip/route/print','/ipv6/route/print'}:
                domain=item.get('routing-table') or item.get('routing-mark') or 'main'
            elif 'interface' in item:
                domain=interface_domains.get(item['interface'],'main')
            else:
                # DHCP leases/networks and table-wide records lack proven per-interface routing context.
                # Preserve them in the native receipt, not a guessed IPAM domain.
                continue
            if domain!=b.routing_domain:continue
            selected.append(dict(item,**{'routing-table':domain}))
        if selected:filtered[path]=selected
    responses=filtered
    if not responses:raise ValueError('No observations in selected proven routing domain')
    version=values.get('routeros_version')
    from app.integrations.mikrotik_native import require_native_version
    require_native_version([{'version':version}])
    obj=DiscoveryInput(batch_key='native:'+str(row.id)+':vrf:'+str(b.vrf_id),vrf_id=b.vrf_id,device_id=b.device_id,
        observed_at=row.sampled_at,routeros_version=version,protocol='api7_recorded' if version.startswith('7.') else 'api6_recorded',
        responses=responses,reason=b.reason)
    normalized,unsupported=discovery.normalize(obj)
    digest=hashlib.sha256(json.dumps(obj.model_dump(mode='json'),sort_keys=True).encode()).hexdigest()
    return obj,{'digest':digest,'sample_id':row.id,'observations':normalized,'unsupported':unsupported,
        'requires_operator_review':True,'equipment_changes':0,'source':'stored_native_receipt','assignment_changes':0}


@router.post('/ipam/native-preview')
def ipam_preview(b:NativeReview,db=Depends(get_db),u=Depends(require_admin)):
    return run(db,lambda:native_preview(db,b)[1])


@router.post('/ipam/native-record')
def ipam_record(b:NativeReview,db=Depends(get_db),u=Depends(require_admin)):
    def write():
        obj,report=native_preview(db,b)
        if report['digest']!=b.preview_digest:raise ValueError()
        return discovery.ingest(db,obj,u.id)
    return run(db,write,True)


class Qualification(Closed):
    latitude:float=Field(ge=-89,le=89,allow_inf_nan=False)
    longitude:float=Field(ge=-179,le=179,allow_inf_nan=False)
    technology:Literal['lte','5g']


@router.post('/qualification')
def qualification(b:Qualification,db=Depends(get_db)):
    return run(db,lambda:s.qualification_advisory(db,b.latitude,b.longitude,b.technology))
