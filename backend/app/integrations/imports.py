"""Transactional identity reconciliation with source provenance and conflict guards."""
from datetime import timedelta
import hashlib
import json
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from app.models.entities import Device, Site, NetworkLink, Sector, IntegrationBinding
from app.integrations.normalization import identifier,mapping,text,number,mac,uisp_device

MODELS={'device':Device,'site':Site,'link':NetworkLink,'sector':Sector}


def binding(db,integration,kind,external):
    return db.scalar(select(IntegrationBinding).where(IntegrationBinding.integration==integration,
        IntegrationBinding.kind==kind,IntegrationBinding.external_id==external))


def mapped_id(db,integration,kind,external):
    row=binding(db,integration,kind,external) if external else None
    return getattr(row,kind+'_id') if row else None


def apply(db,integration,kind,external,values,now,*,observed=None,existing=None):
    row=binding(db,integration,kind,external)
    model=MODELS[kind];item=db.get(model,getattr(row,kind+'_id')) if row else existing
    if row and row.observed_at and observed and observed<row.observed_at:return item,1
    if observed and observed>now+timedelta(minutes=1):raise ValueError('future observation')
    if row and row.observed_at and not observed:
        values={key:value for key,value in values.items() if key not in {'status','last_seen'}}
    conflicts=0
    if item is None:
        defaults={'source':integration,'external_id':external} if kind=='device' else {}
        item=model(**(defaults|values));db.add(item);db.flush()
    else:
        for key,value in values.items():
            old=getattr(item,key)
            previous=(row.values or {}).get(key) if row else None
            # User-owned site/parent associations and manual edits are never overwritten.
            if key in {'wisp_site_id','parent_device_id'} and old is not None and old!=value and (not row or old!=previous):
                conflicts+=1;continue
            if row and key in row.values and old!=previous and str(old)!=str(previous) and old!=value:
                conflicts+=1;continue
            setattr(item,key,value)
    safe=json.loads(json.dumps(values,default=str))
    if row is None:
        row=IntegrationBinding(integration=integration,kind=kind,external_id=external,**{kind+'_id':item.id},values={})
        db.add(row)
    row.observed_at=observed or row.observed_at;row.received_at=now
    row.digest=hashlib.sha256(json.dumps(safe,sort_keys=True).encode()).hexdigest();row.values=safe
    db.flush()
    return item,conflicts


def import_inventory(db,snapshot,now,credentials=(),*,progress=None):
    def safe_id(value):return identifier(value,credentials)
    count=skipped=conflicts=0
    seen=set()
    for raw in snapshot.get('sites',[]):
        if progress:progress()
        try:
            external=safe_id(mapping(raw).get('id'))
            if ('site',external) in seen:skipped+=1;continue
            seen.add(('site',external))
            values={'name':text(raw.get('name') or external,secrets=credentials),
                'site_type':'core-pop' if raw.get('type')=='pop' else 'tower'}
            existing=db.scalar(select(Site).where(Site.name==values['name']))
            with db.begin_nested():_,n=apply(db,'uisp','site',external,values,now,existing=existing)
            conflicts+=n
        except (ValueError,TypeError,IntegrityError):skipped+=1
    for raw in snapshot.get('devices',[]):
        if progress:progress()
        try:
            external,values,observed=uisp_device(raw,credentials)
            if ('device',external) in seen:skipped+=1;continue
            seen.add(('device',external))
            if observed and observed<now-timedelta(minutes=15):
                values.pop('status',None);skipped+=1
            if observed:values['last_seen']=observed
            if values.get('site_id'):
                site_id=mapped_id(db,'uisp','site',values['site_id'])
                if site_id:values['wisp_site_id']=site_id
                elif values.get('site_name'):
                    site=db.scalar(select(Site).where(Site.name==values['site_name']))
                    if site:values['wisp_site_id']=site.id
            existing=db.scalar(select(Device).where(Device.source=='uisp',Device.external_id==external))
            if existing is None and values.get('mac_address'):
                canonical=func.upper(func.replace(func.replace(Device.mac_address,':',''),'-',''))
                matches=list(db.scalars(select(Device).where(canonical==values['mac_address'].replace(':',''))))
                if len(matches)>1:conflicts+=1;continue
                if matches:existing=matches[0]
            if existing is not None:
                values.pop('enabled',None)
                if values.get('mac_address') and existing.mac_address and mac(existing.mac_address)!=values['mac_address']:
                    conflicts+=1;continue
                if existing.last_seen and (observed is None or observed<existing.last_seen):
                    values.pop('status',None);values.pop('last_seen',None);conflicts+=1
            if existing is not None and existing.source!='uisp':
                values={k:v for k,v in values.items() if k=='wisp_site_id'}
                conflicts+=1
            with db.begin_nested():
                item,n=apply(db,'uisp','device',external,values,now,observed=observed,existing=existing)
                if item is not None:item.raw_data={'integration_provenance':{'uisp_id':external}}
            conflicts+=n;count+=1
        except (ValueError,TypeError,IntegrityError):skipped+=1
    for raw in snapshot.get('devices',[]):
        ident=mapping(mapping(raw).get('identification'));parent=ident.get('parentId')
        if not parent:continue
        try:
            item_id=mapped_id(db,'uisp','device',safe_id(ident.get('id')))
            parent_id=mapped_id(db,'uisp','device',safe_id(parent))
            if not item_id or not parent_id or item_id==parent_id:conflicts+=1;continue
            item=db.get(Device,item_id)
            if item.parent_device_id is None:item.parent_device_id=parent_id
            elif item.parent_device_id!=parent_id:conflicts+=1
        except (ValueError,TypeError,IntegrityError):skipped+=1
    for kind,key in [('link','links'),('sector','sectors')]:
        for raw in snapshot.get(key,[]):
            if progress:progress()
            try:
                external=safe_id(mapping(raw).get('id'))
                if (kind,external) in seen:skipped+=1;continue
                seen.add((kind,external))
                values={'name':text(raw.get('name') or external,secrets=credentials)}
                if kind=='link':
                    a=mapped_id(db,'uisp','site',safe_id(raw.get('fromSiteId')))
                    z=mapped_id(db,'uisp','site',safe_id(raw.get('toSiteId')))
                    if not a or not z or a==z:raise ValueError('unresolved sites')
                    values|={'a_site_id':a,'z_site_id':z,'link_type':'unlicensed-wireless',
                        'capacity_mbps':number(raw.get('capacityMbps'))}
                    for source,target in [('fromDeviceId','a_device_id'),('toDeviceId','z_device_id')]:
                        if raw.get(source):values[target]=mapped_id(db,'uisp','device',safe_id(raw[source]))
                else:
                    site_id=mapped_id(db,'uisp','site',safe_id(raw.get('siteId')))
                    if not site_id:raise ValueError('unresolved site')
                    values|={'site_id':site_id,'frequency_mhz':number(raw.get('frequencyMhz'),100,100000),
                        'channel_width_mhz':number(raw.get('channelWidthMhz'),1,5000),
                        'capacity_mbps':number(raw.get('capacityMbps'))}
                    if raw.get('deviceId'):values['device_id']=mapped_id(db,'uisp','device',safe_id(raw['deviceId']))
                with db.begin_nested():_,n=apply(db,'uisp',kind,external,values,now)
                conflicts+=n
            except (ValueError,TypeError,IntegrityError):skipped+=1
    return count,skipped,conflicts
