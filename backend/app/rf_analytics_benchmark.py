"""Offline synthetic query benchmark, not collector throughput or field accuracy."""
import hashlib,json,time
from datetime import datetime,timedelta,timezone
from statistics import median
from sqlalchemy import select,insert,event
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device,Site,TelemetrySample,RFAnalyticsPoint as Point
from app.services.rf_analytics_overview import analytics_overview
from app.services.rf_analytics_queries import analytics_detail


def seed_query_dataset(db,*,devices,now,points_per_device=96):
    if not settings.offline_mode or settings.app_env!='staging':raise ValueError('Benchmark requires offline staging')
    if devices not in {250,500,1000} or not 48<=points_per_device<=192:raise ValueError('Unsupported benchmark size')
    if db.scalar(select(Device.id).limit(1)):raise ValueError('Benchmark requires empty disposable inventory')
    sites=[Site(name=f'Synthetic analytics benchmark tower {i:02}') for i in range(40)];db.add_all(sites);db.flush()
    fleet=[Device(source='synthetic',external_id=f'noc89-bench-{i}',name=f'Synthetic analytics device {i}',
        vendor=('Ubiquiti','Cambium','Racom','Aviat')[i%4],wisp_site_id=sites[i%40].id,enabled=True) for i in range(devices)]
    db.add_all(fleet);db.flush()
    digest=hashlib.sha256(b'synthetic-query-benchmark-v1').hexdigest()
    sources=[];values=[]
    def flush_batch():
        if not sources:return
        ids=list(db.scalars(insert(TelemetrySample).returning(TelemetrySample.id),sources))
        db.execute(insert(Point),[v|{'source_sample_id':source_id} for source_id,v in zip(ids,values)])
        sources.clear();values.clear()
    for d in fleet:
        for i in range(points_per_device):
            at=now-timedelta(days=30)+timedelta(seconds=i*(29*86400)/(points_per_device-5)) if i<points_per_device-4 else now-timedelta(seconds=(points_per_device-1-i)*60)
            sources.append({'device_id':d.id,'sampled_at':at,'raw_data':{'synthetic_query_benchmark':True}})
            adverse=i>=points_per_device-4 and d.id%5==0
            values.append(dict(device_id=d.id,site_id=d.wisp_site_id,link_id=None,series_key='radio',vendor=d.vendor.casefold(),
                source_profile='synthetic-query-benchmark-v1',mapping_digest=digest,sampled_at=at,projected_at=now,valid=True,
                radio_state='up',signal_dbm=-60,snr_db=10 if adverse else 30,noise_floor_dbm=-80 if adverse else -95,
                modulation_bits=8,capacity_mbps=500,tx_throughput_mbps=100,rx_throughput_mbps=50,utilization_percent=30,
                quality_percent=98,uptime_seconds=1000+(at-(now-timedelta(days=30))).total_seconds(),
                frequency_mhz=5800,bandwidth_mhz=40,tx_errors=i*10,rx_errors=i*5,retries=i*10))
            if len(sources)>=500:flush_batch()
    flush_batch()
    db.flush();return fleet


def measure(db,*,devices,now=None,points_per_device=96):
    now=now or datetime.now(timezone.utc);fleet=seed_query_dataset(db,devices=devices,now=now,points_per_device=points_per_device)
    times=[];queries=[];maximum=0;offset=0;series=0
    def count(_conn,_cursor,statement,*args):
        if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
    bind=db.get_bind();event.listen(bind,'before_cursor_execute',count)
    try:
        started=time.perf_counter()
        while True:
            queries.clear();begin=time.perf_counter();result=analytics_overview(db,window='30d',offset=offset,limit=100,now=now)
            times.append((time.perf_counter()-begin)*1000);maximum=max(maximum,len(queries));series+=len(result['series'])
            if not result['has_more']:break
            offset+=100
        fleet_ms=(time.perf_counter()-started)*1000
        queries.clear();begin=time.perf_counter();detail=analytics_detail(db,fleet[0].id,window='30d',now=now)
        detail_ms=(time.perf_counter()-begin)*1000;detail_queries=len(queries)
    finally:event.remove(bind,'before_cursor_execute',count)
    assert series==devices and maximum<=9 and detail_queries<=10
    assert max(times)<5000 and detail_ms<5000
    return {'devices':devices,'projection_points':devices*points_per_device,'points_per_device':points_per_device,
        'window':'30d','pages':len(times),'fleet_paged_ms':round(fleet_ms,2),'page_median_ms':round(median(times),2),
        'page_max_ms':round(max(times),2),'detail_ms':round(detail_ms,2),'max_page_selects':maximum,'detail_selects':detail_queries,
        'historical_buckets':len(detail['buckets']),'fixture':'direct_synthetic_projection_query_only','network_requests':0}


def main():
    if not settings.offline_mode or settings.app_env!='staging':raise SystemExit('Offline staging required')
    results=[]
    for size in (250,500,1000):
        with SessionLocal() as db:
            try:results.append(measure(db,devices=size))
            finally:db.rollback()
    print(json.dumps({'results':results,'accuracy':'synthetic_query_performance_only'},sort_keys=True))


if __name__=='__main__':main()
