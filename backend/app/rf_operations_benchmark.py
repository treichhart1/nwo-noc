"""Synthetic API/query/concurrency benchmark in an owned disposable schema.

Never polls devices, changes the public schema, or certifies production SLAs.
"""
import json,time,uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from statistics import median
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text,insert,event,select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import Base,get_db
from app.api.deps import current_user
from app.api.rf_operations_routes import router
from app.api.routes import router as legacy_router
from app.models.entities import Device,Site,NetworkLink,Sector,Incident,User
from app.rf_analytics_benchmark import seed_query_dataset
from app.services.rf_operations import overview


def measure(size):
    if not settings.offline_mode or settings.app_env!='staging':raise ValueError('Offline staging required')
    if size not in {500,1000}:raise ValueError('Supported synthetic sizes: 500 and 1000')
    engine=create_engine(settings.database_url,pool_size=6,max_overflow=0)
    with engine.connect() as c:
        if c.scalar(text('SELECT current_database()'))!='nwo_noc':raise ValueError('Disposable nwo_noc database required')
    schema='noc91_bench_'+uuid.uuid4().hex[:12]
    with engine.begin() as c:c.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped=engine.execution_options(schema_translate_map={None:schema})
    now=datetime.now(timezone.utc);api=FastAPI();api.include_router(router);api.include_router(legacy_router)
    def session():
        with Session(scoped) as db:yield db
    api.dependency_overrides[get_db]=session
    api.dependency_overrides[current_user]=lambda:SimpleNamespace(id=None,role='operator')
    try:
        Base.metadata.create_all(scoped)
        with Session(scoped) as db:
            fleet=seed_query_dataset(db,devices=size,now=now,points_per_device=48)
            sites=list(db.scalars(select(Site).order_by(Site.id)))
            links=[NetworkLink(name=f'Synthetic operations backhaul {i}',a_site_id=sites[i].id,z_site_id=sites[i+1].id,
                a_device_id=fleet[i].id,z_device_id=fleet[i+1].id) for i in range(39)]
            db.add_all(links);db.flush()
            db.add_all([Sector(name=f'Synthetic operations sector {i}',site_id=sites[i%40].id,device_id=fleet[i].id) for i in range(80)])
            db.execute(insert(Incident),[{'title':f'Synthetic operations issue {i}','device_id':fleet[i%size].id,
                'site_id':sites[i%40].id,'status':('open','acknowledged','investigating','resolved','closed')[i%5],
                'severity':'critical' if i%11==0 else 'warning','started_at':now-timedelta(minutes=i),'resolution':'Synthetic benchmark finding'} for i in range(5000)])
            link_id=links[0].id;db.commit()
        query_count=[]
        def count(conn,cursor,statement,*args):
            if statement.lstrip().upper().startswith('SELECT'):query_count.append(1)
        event.listen(scoped,'before_cursor_execute',count)
        try:
            with Session(scoped) as db:
                started=time.perf_counter();direct=overview(db,window='30d',now=now);service_ms=(time.perf_counter()-started)*1000
                queries=len(query_count)
                assert len(direct['inventory'])==50 and direct['incident_total']==3000 and queries<=20
        finally:event.remove(scoped,'before_cursor_execute',count)
        api_times=[]
        with TestClient(api) as client:
            query_count.clear();event.listen(scoped,'before_cursor_execute',count)
            try:
                start=time.perf_counter();listed=client.get('/api/incidents?limit=200');incident_ms=(time.perf_counter()-start)*1000
                incident_selects=len(query_count)
                assert listed.status_code==200 and len(listed.json())==200 and incident_selects<=4
            finally:event.remove(scoped,'before_cursor_execute',count)
            for offset in (0,50,100):
                start=time.perf_counter();r=client.get(f'/api/rf/operations?window=30d&offset={offset}');elapsed=(time.perf_counter()-start)*1000
                assert r.status_code==200,r.text
                assert len(r.json()['inventory'])==50
                api_times.append(elapsed)
            start=time.perf_counter();chart=client.get(f'/api/rf/operations/links/{link_id}?window=30d');chart_ms=(time.perf_counter()-start)*1000
            assert chart.status_code==200 and chart.json()['endpoints'][0]['buckets']
        def operator(offset):
            with TestClient(api) as c:
                start=time.perf_counter();response=c.get(f'/api/rf/operations?window=30d&offset={offset}')
                assert response.status_code==200
                return (time.perf_counter()-start)*1000
        with ThreadPoolExecutor(max_workers=4) as pool:concurrent=list(pool.map(operator,(0,50,100,150)))
        # Reconnection after discarding idle connections; real DB/worker restart
        # acceptance is a separate release test against the Docker stack.
        engine.dispose()
        with TestClient(api) as client:
            start=time.perf_counter();reconnect=client.get('/api/rf/operations');reconnect_ms=(time.perf_counter()-start)*1000
            assert reconnect.status_code==200
        assert max(api_times)<5000 and chart_ms<5000 and max(concurrent)<10000
        return {'devices':size,'sites':40,'links':39,'sectors':80,'incidents':5000,'projection_points':48*size,
            'overview_selects':queries,'overview_service_ms':round(service_ms,2),'api_median_ms':round(median(api_times),2),
            'api_max_ms':round(max(api_times),2),'legacy_incident_list_api_ms':round(incident_ms,2),'legacy_incident_list_selects':incident_selects,'link_chart_api_ms':round(chart_ms,2),'concurrent_operators':4,
            'concurrent_max_ms':round(max(concurrent),2),'reconnection_api_ms':round(reconnect_ms,2),
            'authentication':'synthetic role dependency; real JWT/authorization tested separately','network_equipment_requests':0,'fixture':'48 direct synthetic projection points per device across 30d; 5000 synthetic incidents',
            'limits':'synthetic API/query checks; not dense ingestion, cold-cache production SLA or field accuracy'}
    finally:
        # Only this random, newly created namespace is removed; public data is untouched.
        Base.metadata.drop_all(scoped)
        with engine.begin() as c:c.execute(text(f'DROP SCHEMA "{schema}"'))
        engine.dispose()


def main():print(json.dumps({'results':[measure(n) for n in (500,1000)]},sort_keys=True))
if __name__=='__main__':main()
