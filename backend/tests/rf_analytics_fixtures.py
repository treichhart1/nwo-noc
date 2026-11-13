"""Synthetic historical telemetry. No radio requests or invented historical values."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from app.models.entities import Device,Site,NetworkLink
from app.services.radio_history import stage_radio
from app.services.microwave_history import stage_microwave
from radio_fixtures import observation as radio_observation
from microwave_fixtures import observation as microwave_observation,mapping as microwave_mapping

NOW=datetime(2026,11,13,18,tzinfo=timezone.utc)


def historical_times(now):
    times={now-timedelta(days=i) for i in range(1,31)}
    times.update(now-timedelta(hours=i*6) for i in range(1,28))
    times.update(now-timedelta(minutes=i*10) for i in range(7,145))
    times.update(now-timedelta(minutes=i*2) for i in range(8,30))
    times.update(now-timedelta(seconds=i*60) for i in range(8))
    return sorted(times)


def device(db,*,name='Synthetic analytics radio',vendor='Cambium',external_id='noc89-radio',site=None):
    if site is None:
        site=Site(name='Synthetic analytics tower '+external_id);db.add(site);db.flush()
    d=Device(source='synthetic',external_id=external_id,name=name,vendor=vendor,wisp_site_id=site.id,
        enabled=True,authorized=True,monitor_enabled=True,monitor_status='up',monitor_last_checked_at=NOW,monitor_last_up_at=NOW)
    db.add(d);db.flush();return d


def seed_radio(db,d,*,now=NOW,scenario='healthy',profile='epmp-4600-mib-v1'):
    times=historical_times(now);states={};local=now.astimezone(ZoneInfo('America/New_York'))
    if scenario=='recurring':
        for day in range(1,8):
            when=(local-timedelta(days=day)).replace(hour=20,minute=0,second=0,microsecond=0)
            for minute in (0,3,6,9):
                t=(when+timedelta(minutes=minute)).astimezone(timezone.utc);times.append(t);states[t]=day<=5
    for i,when in enumerate(sorted(set(times))):
        current=when>=now-timedelta(minutes=7)
        data={'utilization_percent':30,'uptime_seconds':100000+i*60000,'tx_errors':i*60,'rx_errors':i*30,'retries':i*60}
        if current:
            if scenario=='interference':data.update(noise_floor_dbm=-80,snr_db=10,tx_errors=i*60000,retries=i*60000)
            if scenario=='congestion':data.update(utilization_percent=95,tx_throughput_mbps=480000000)
            if scenario=='gradual':data.update(rssi_dbm=-62-(7-(now-when).total_seconds()/60)*2)
            if scenario=='sudden':data.update(rssi_dbm=-80)
            if scenario=='fallback':data.update(modulation=1,retries=i*60000)
            if scenario=='outages':data.update(radio_status=1 if int((now-when).total_seconds()/60)%2 else 2)
            if scenario=='missing':data.update(rssi_dbm=None,snr_db=None,noise_floor_dbm=None)
        if states.get(when):data.update(rssi_dbm=-85,radio_status=2)
        stage_radio(db,d,radio_observation(profile,at=when,**data))
    return len(set(times))


def seed_microwave(db,*,now=NOW,profile='racom-ray3-mib-v1'):
    a=device(db,name='Synthetic microwave analytics A',vendor='Aviat' if profile.startswith('aviat') else 'Racom',external_id='noc89-micro-a')
    z=device(db,name='Synthetic microwave analytics Z',vendor='Aviat' if profile.startswith('aviat') else 'Racom',external_id='noc89-micro-z')
    z.parent_device_id=a.id
    link=NetworkLink(name='Synthetic analytics backhaul',a_site_id=a.wisp_site_id,z_site_id=z.wisp_site_id,a_device_id=a.id,z_device_id=z.id)
    db.add(link);db.flush();m=microwave_mapping(profile)
    for i,when in enumerate(historical_times(now)):
        bad=when>=now-timedelta(minutes=7)
        stage_microwave(db,a,link,microwave_observation(when,m=m,
            changes={'c1':{'radio_status':2 if bad else 1,'local_rsl_dbm':-85 if bad else -60,'capacity_mbps':50000000 if bad else 500000000,'errors':i*60},'c2':{'errors':i*60}},
            link_changes={'uptime_seconds':100000+i*60000}))
    return a,z,link
