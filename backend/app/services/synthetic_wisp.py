"""Deterministic rural WISP fixture. No network I/O or equipment credentials."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.models.entities import Device, Site, NetworkLink, Sector, TelemetrySample

VENDORS = ('MikroTik', 'Ubiquiti', 'Cambium', 'Aviat', 'Racom')


def seed_network(db, *, devices=400, now=None):
    if not 250 <= devices <= 1000:
        raise ValueError('Synthetic inventory must contain 250–1000 devices')
    if db.scalar(select(Device.id).limit(1)) or db.scalar(select(Site.id).limit(1)):
        raise ValueError('Synthetic seed requires an empty disposable inventory')
    now = now or datetime.now(timezone.utc)
    sites = [Site(name=f'Synthetic {"POP" if i % 10 == 0 else "Tower"} {i:02}',
        site_type='core-pop' if i % 10 == 0 else 'tower', latitude=40.5+(i//8)*.08,
        longitude=-84.7+(i%8)*.1, customer_count=50+i) for i in range(40)]
    db.add_all(sites); db.flush()
    radios=[]; routers=[]
    for i in range(devices):
        site_index=i%40; tier=i//40
        vendor='MikroTik' if tier<2 else VENDORS[(i+ tier)%len(VENDORS)]
        role='tower-router' if tier==0 else 'switch' if tier==1 else 'backhaul' if tier==2 else 'access-point' if tier<6 else 'customer-radio'
        device=Device(source='synthetic',external_id=f'noc85-{i:04}',name=f'{vendor} {role} {i:04}',
            vendor=vendor,model_name=f'Synthetic {vendor}',role=role,wisp_site_id=sites[site_index].id,
            site_name=sites[site_index].name,ip_address=f'198.18.{i//254}.{i%254+1}',
            authorized=True,enabled=True,status='active',monitor_enabled=True,
            monitor_host=f'198.18.{i//254}.{i%254+1}',monitor_port=443,
            monitor_interval_seconds=15,monitor_timeout_seconds=1,monitor_status='up',
            monitor_last_checked_at=now,monitor_last_up_at=now,last_seen=now)
        db.add(device);radios.append(device)
        if tier==0: routers.append(device)
    db.flush()
    for i,d in enumerate(radios):
        site_index=i%40
        if i>=40: d.parent_device_id=routers[site_index].id
        elif site_index%10: d.parent_device_id=routers[(site_index//10)*10].id
    for i in range(1,40):
        parent=(i//10)*10 if i%10 else 0
        db.add(NetworkLink(name=f'Synthetic backhaul {i:02}',a_site_id=sites[parent].id,
            z_site_id=sites[i].id,a_device_id=routers[parent].id,z_device_id=routers[i].id,
            vendor=VENDORS[i%5],capacity_mbps=500,status='active'))
    for i,d in enumerate(radios):
        if d.role=='access-point':
            db.add(Sector(name=f'Synthetic sector {i:04}',site_id=d.wisp_site_id,device_id=d.id,
                technology=d.vendor,capacity_mbps=200,customer_count=20))
        for minute in (1,2,3):
            db.add(TelemetrySample(device_id=d.id,sampled_at=now-timedelta(minutes=minute),
                rx_mbps=20,tx_mbps=10,raw_data={'rf_metrics_v1':{
                    'rssi_dbm':-60,'snr_db':30,'ccq_percent':95,'tx_rate_mbps':400,'rx_rate_mbps':400}}))
    db.flush()
    return {'sites':sites,'devices':radios,'routers':routers}
