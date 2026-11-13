"""Explicit synthetic history/export fixture. No device requests or automatic jobs."""
import hashlib,json
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device,NetworkLink
from app.services.radio_observation import RadioObservation,RadioMetrics,RadioCounters,ENUMS
from app.services.radio_history import stage_radio
from app.services.rf_analytics_projection import project_batch


def synthetic_observation(at,*,now,sequence,scenario):
    current=at>=now-timedelta(seconds=14)
    metrics=dict(rssi_dbm=-60,remote_rssi_dbm=-62,snr_db=30,noise_floor_dbm=-95,frequency_mhz=5800,channel_width_mhz=40,
        link_capacity_mbps=500,ccq_percent=98,tx_rate_mbps=400,rx_rate_mbps=350,tx_throughput_mbps=100,rx_throughput_mbps=50,
        utilization_percent=30,mcs_index=9,temperature_c=40,uptime_seconds=100000+sequence*60000)
    counters=dict(tx_errors=sequence*10,rx_errors=sequence*5,retries=sequence*10,disconnects=0);state='up'
    if current:
        if scenario=='interference':metrics.update(noise_floor_dbm=-80,snr_db=10);counters.update(retries=sequence*60000)
        elif scenario=='congestion':metrics.update(utilization_percent=95,tx_throughput_mbps=480)
        elif scenario=='missing':metrics.update(rssi_dbm=None,snr_db=None,noise_floor_dbm=None)
    local=at.astimezone(ZoneInfo('America/New_York'));local_days=(now.astimezone(ZoneInfo('America/New_York')).date()-local.date()).days
    if scenario=='recurring' and local.hour==20 and 1<=local_days<=5:
        metrics['rssi_dbm']=-85;state='down'
    values=RadioMetrics(**metrics);counts=RadioCounters(**counters)
    unavailable={k:'missing' for k,v in values.model_dump().items() if v is None}|{'gps_sync':'unsupported'}
    originals=values.model_dump(exclude_none=True)|counters|{'radio_status':1 if state=='up' else 2,'modulation':6}
    def unit(k):
        if k in {'radio_status','modulation'}:return 'enum'
        if k in counters or k in {'mcs_index','subscriber_count'}:return 'count'
        if k.endswith('_dbm'):return 'dbm'
        if k.endswith('_db'):return 'db'
        if k.endswith('_mhz'):return 'mhz'
        if k.endswith('_mbps'):return 'mbps'
        if k.endswith('_percent'):return 'percent'
        return 'seconds' if k=='uptime_seconds' else 'celsius'
    return RadioObservation(vendor='cambium',profile='epmp-4600-mib-v1',mapping_digest=hashlib.sha256(b'noc89-synthetic-export-v1').hexdigest(),
        source_kind='synthetic_export',timestamp_basis='source_export',observed_at=at,received_at=at,measurements=values,counters=counts,
        radio_status=state,modulation='1024QAM',gps_sync=None,unavailable=unavailable,
        original_values=originals,original_units={k:unit(k) for k in originals})


def main():
    if not settings.offline_mode or settings.app_env!='staging' or not settings.synthetic_monitoring_enabled:
        raise SystemExit('Analytics rehearsal requires explicit offline synthetic staging')
    now=datetime.now(timezone.utc);ids={};sites={}
    with SessionLocal() as db:
        devices=list(db.scalars(select(Device).order_by(Device.id)))
        if not devices or any(d.source!='synthetic' for d in devices):raise SystemExit('Synthetic inventory required')
        candidates=[d for d in devices if (d.vendor or '').casefold()=='cambium']
        if len(candidates)<4:raise SystemExit('Expected the synthetic WISP fixture')
        for d,scenario in zip(candidates,['interference','congestion','missing','recurring']):
            d.name='NOC89 Synthetic '+scenario
            times={now-timedelta(days=i) for i in range(1,31)}
            times.update(now-timedelta(hours=i*6) for i in range(1,28))
            times.update(now-timedelta(minutes=i*10) for i in range(7,145))
            times.update(now-timedelta(minutes=i*2) for i in range(8,30))
            times.update(now-timedelta(seconds=i*2) for i in range(8))
            if scenario=='recurring':
                local=now.astimezone(ZoneInfo('America/New_York'))
                for day in range(1,8):
                    start=(local-timedelta(days=day)).replace(hour=20,minute=0,second=0,microsecond=0)
                    times.update((start+timedelta(minutes=m)).astimezone(timezone.utc) for m in (0,3,6,9))
            for sequence,at in enumerate(sorted(times)):stage_radio(db,d,synthetic_observation(at,now=now,sequence=sequence,scenario=scenario))
            ids[scenario]=d.id;sites[scenario]=d.wisp_site_id
        links=list(db.scalars(select(NetworkLink).where(NetworkLink.name.like('Synthetic NOC88 %')).order_by(NetworkLink.id)))
        link_ids=[l.id for l in links]
        db.commit()
        while True:
            result=project_batch(db,limit=5000);db.commit()
            if not result['processed']:break
    print(json.dumps({'device_ids':ids,'site_ids':sites,'microwave_link_ids':link_ids,
        'network_requests':0,'synthetic_export':True},sort_keys=True))


if __name__=='__main__':main()
