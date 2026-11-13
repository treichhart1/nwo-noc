"""PostgreSQL-only, indexed and bounded historical RF queries."""
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,func,case,and_,cast,Float,text
from app.models.entities import RFAnalyticsPoint as Point,RFAnalyticsCheckpoint,Device,Site,NetworkLink,Incident
from app.services.rf_analytics_policy import AnalyticsPolicy,METRICS,RATES,WINDOWS
from app.services.rf_analytics_engine import analyze_history,score


def utc(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def frame_for(ids,now,seconds,max_gap_seconds=900):
    ranked=select(Point,func.row_number().over(partition_by=(Point.device_id,Point.series_key,Point.sampled_at),order_by=Point.id.desc()).label('duplicate_rank')).where(
        Point.device_id.in_(ids),Point.sampled_at>=now-timedelta(seconds=seconds),Point.sampled_at<=now).subquery()
    chosen=select(ranked).where(ranked.c.duplicate_rank==1).subquery()
    partition=(chosen.c.device_id,chosen.c.series_key);order=(chosen.c.sampled_at,chosen.c.id)
    lag=lambda col:func.lag(col).over(partition_by=partition,order_by=order)
    columns=[chosen.c[c.name] for c in Point.__table__.c]
    columns += [lag(chosen.c.sampled_at).label('prev_at'),lag(chosen.c.valid).label('prev_valid'),
        lag(chosen.c.mapping_digest).label('prev_mapping'),lag(chosen.c.uptime_seconds).label('prev_uptime'),lag(chosen.c.radio_state).label('prev_radio_state')]
    for field in ('tx_errors','rx_errors','retries','disconnects'):columns.append(lag(chosen.c[field]).label('prev_'+field))
    lagged=select(*columns).subquery()
    elapsed=func.extract('epoch',lagged.c.sampled_at-lagged.c.prev_at)
    continuity=and_(lagged.c.valid.is_(True),lagged.c.prev_valid.is_(True),lagged.c.mapping_digest==lagged.c.prev_mapping,
        lagged.c.uptime_seconds.is_not(None),lagged.c.prev_uptime.is_not(None),lagged.c.uptime_seconds>=lagged.c.prev_uptime,
        elapsed>0,elapsed<=max_gap_seconds)
    rates=[]
    for field in ('tx_errors','rx_errors','retries','disconnects'):
        current,previous=lagged.c[field],lagged.c['prev_'+field]
        rates.append(case((and_(continuity,current.is_not(None),previous.is_not(None),current>=previous),cast(current-previous,Float)/func.nullif(elapsed,0)),else_=None).label(field+'_per_second'))
    transition=and_(continuity,lagged.c.radio_state=='down',lagged.c.prev_radio_state=='up')
    return select(lagged,*rates,case((transition,1),else_=0).label('observed_down_transition'),
        case((and_(lagged.c.valid.is_(True),lagged.c.prev_valid.is_(True),lagged.c.mapping_digest==lagged.c.prev_mapping,
            lagged.c.uptime_seconds<lagged.c.prev_uptime),1),else_=0).label('observed_restart')).subquery()


def baseline_stats(db,frame,now,window,policy):
    duration,recent,_=WINDOWS[window];cutoff=now-timedelta(seconds=recent)
    keys=[frame.c.device_id,frame.c.series_key,frame.c.mapping_digest,frame.c.frequency_mhz,frame.c.bandwidth_mhz,frame.c.site_id,frame.c.link_id]
    columns=[*keys,func.count().label('rows'),func.count().filter(frame.c.valid.is_(True)).label('valid_rows')]
    for name in METRICS+RATES:
        value=frame.c[name];valid=and_(frame.c.valid.is_(True),value.is_not(None))
        columns.extend([func.count(value).filter(valid).label(name+'_n'),
            func.percentile_cont(.1).within_group(value).filter(valid).label(name+'_p10'),
            func.percentile_cont(.5).within_group(value).filter(valid).label(name+'_median'),
            func.percentile_cont(.9).within_group(value).filter(valid).label(name+'_p90'),
            func.min(frame.c.sampled_at).filter(valid).label(name+'_first'),func.max(frame.c.sampled_at).filter(valid).label(name+'_last')])
    records=db.execute(select(*columns).where(frame.c.sampled_at<cutoff).group_by(*keys)).mappings()
    output={}
    for row in records:
        values={}
        for name in METRICS+RATES:
            count=row[name+'_n'];first,last=row[name+'_first'],row[name+'_last']
            span=(last-first).total_seconds() if first and last else 0
            fraction=count/row['rows'] if row['rows'] else 0
            ready=count>=policy.minimum_baseline_samples and fraction>=policy.minimum_valid_fraction and span>=(duration-recent)*policy.minimum_span_fraction
            values[name]={'count':count,'valid_fraction':round(fraction,3),'span_seconds':span,'ready':ready,
                'median':float(row[name+'_median']) if row[name+'_median'] is not None else None,
                'p10':float(row[name+'_p10']) if row[name+'_p10'] is not None else None,'p90':float(row[name+'_p90']) if row[name+'_p90'] is not None else None,
                'reason':'available' if ready else 'missing_or_unsupported' if count==0 else 'insufficient_quality_or_history'}
        key=tuple(row[k] for k in ('device_id','series_key','mapping_digest','frequency_mhz','bandwidth_mhz','site_id','link_id'))
        output[key]=values
    return output


def context_key(point):return tuple(point.get(k) for k in ('device_id','series_key','mapping_digest','frequency_mhz','bandwidth_mhz','site_id','link_id'))


def current_history(db,frame,ids):
    ranked=select(frame,func.row_number().over(partition_by=(frame.c.device_id,frame.c.series_key),order_by=(frame.c.sampled_at.desc(),frame.c.id.desc())).label('rn')).subquery()
    rows=db.execute(select(ranked).where(ranked.c.rn<=128).order_by(ranked.c.device_id,ranked.c.series_key,ranked.c.sampled_at,ranked.c.id)).mappings().all()
    history={}
    for row in rows:
        item=dict(row);item['sampled_at']=utc(item['sampled_at'])
        history.setdefault((item['device_id'],item['series_key']),{})[item['sampled_at']]=item
    # Include future/stale latest data even when outside the selected window.
    latest=list(db.scalars(select(Point).where(Point.device_id.in_(ids)).distinct(Point.device_id,Point.series_key)
        .order_by(Point.device_id,Point.series_key,Point.sampled_at.desc(),Point.id.desc())))
    for point in latest:
        item={c.name:getattr(point,c.name) for c in Point.__table__.c};item['sampled_at']=utc(item['sampled_at'])
        values=history.setdefault((point.device_id,point.series_key),{})
        if item['sampled_at'] not in values:values[item['sampled_at']]=item|{r:None for r in RATES}
    return {key:[v[t] for t in sorted(v)] for key,v in history.items()}


def historical_buckets(db,frame,window):
    size=WINDOWS[window][2];bucket=(func.floor(func.extract('epoch',frame.c.sampled_at)/size)*size).label('bucket')
    keys=[frame.c.series_key,frame.c.mapping_digest,frame.c.frequency_mhz,frame.c.bandwidth_mhz,frame.c.site_id,frame.c.link_id,bucket]
    valid_count=func.count().filter(frame.c.valid.is_(True))
    known_count=func.count().filter(and_(frame.c.valid.is_(True),frame.c.radio_state.in_(['up','down','degraded'])))
    down_count=func.count().filter(and_(frame.c.valid.is_(True),frame.c.radio_state=='down'))
    up_count=func.count().filter(and_(frame.c.valid.is_(True),frame.c.radio_state=='up'))
    bucket_state=case((and_(known_count>0,known_count==valid_count),
        case((down_count==known_count,'down'),(up_count==known_count,'up'),else_='degraded')),else_=None)
    columns=[*keys,func.count().label('observations'),func.count().filter(frame.c.valid.is_(True)).label('valid_observations'),
        bucket_state.label('radio_state'),
        func.sum(case((frame.c.radio_state=='down',1),else_=0)).label('observed_down_samples'),
        func.sum(frame.c.observed_down_transition).label('observed_down_transitions'),func.sum(frame.c.observed_restart).label('observed_restarts')]
    for name in METRICS+RATES:columns.append(func.avg(frame.c[name]).filter(frame.c.valid.is_(True)).label(name))
    rows=db.execute(select(*columns).group_by(*keys).order_by(bucket,*keys[:2]).limit(2001)).mappings().all()
    output=[]
    for row in rows[:2000]:
        values=dict(row);values['sampled_at']=datetime.fromtimestamp(float(values.pop('bucket')),timezone.utc).isoformat()
        for name in METRICS+RATES:
            if values[name] is not None:values[name]=float(values[name])
        output.append(values)
    return output,len(rows)>2000


def recurring_periods(db,frame,policy):
    local=func.timezone(policy.analysis_timezone,frame.c.sampled_at);day=func.date(local);hour=cast(func.extract('hour',local),Float)
    eligible=and_(frame.c.valid.is_(True),(frame.c.radio_state.in_(['up','down']))|(frame.c.signal_dbm.is_not(None))|(frame.c.utilization_percent.is_not(None)))
    adverse=(frame.c.radio_state=='down')|(frame.c.signal_dbm<policy.signal_low)|(frame.c.utilization_percent>=policy.utilization_enter)
    daily=select(frame.c.device_id,frame.c.series_key,day.label('day'),hour.label('hour'),
        func.count().filter(eligible).label('observed'),func.count().filter(and_(eligible,adverse)).label('adverse'))
    daily=daily.group_by(frame.c.device_id,frame.c.series_key,day,hour).subquery()
    rows=db.execute(select(daily.c.device_id,daily.c.series_key,daily.c.hour,func.count().label('observed_days'),
        func.count().filter(daily.c.adverse>=policy.consecutive_samples).label('adverse_days'))
        .where(daily.c.observed>=policy.consecutive_samples).group_by(daily.c.device_id,daily.c.series_key,daily.c.hour)).mappings()
    return [dict(device_id=r['device_id'],series_key=r['series_key'],hour=int(r['hour']),timezone=policy.analysis_timezone,
        observed_days=r['observed_days'],adverse_days=r['adverse_days'],classification='recurring_adverse_observations',root_cause='unconfirmed')
        for r in rows if r['observed_days']>=policy.minimum_observed_days and r['adverse_days']>=policy.minimum_recurring_days and r['adverse_days']/r['observed_days']>=policy.recurring_fraction]


def series_reports(history,baselines,now,policy):
    output=[]
    for (device,series),points in history.items():
        latest=points[-1];baseline=baselines.get(context_key(latest),{})
        # A new/changed mapping or operating channel cannot borrow another baseline.
        current=[p for p in points if p['sampled_at']>=now-timedelta(seconds=3600)]
        if not current:current=[latest]
        report=analyze_history(current,baseline,now=now,policy=policy)
        output.append({'device_id':device,'series_key':series,'vendor':latest['vendor'],'link_id':latest['link_id'],
            'projected_site_id':latest['site_id'],
            'mapping_digest':latest['mapping_digest'],'source_profile':latest['source_profile'],
            'last_sampled_at':latest['sampled_at'].isoformat(),'latest':{k:latest.get(k) for k in METRICS+RATES}|{'radio_state':latest.get('radio_state')},
            'baseline':baseline,'analysis':report,'recent_points_evaluated':len(current),'recent_history_limit':128})
    return output


def analytics_detail(db,device_id,*,window='24h',now=None,policy=None):
    if window not in WINDOWS:raise ValueError('Unknown analytics window')
    cfg=policy or AnalyticsPolicy();now=now or datetime.now(timezone.utc)
    device=db.get(Device,device_id)
    if device is None:raise LookupError('device_not_found')
    frame=frame_for([device_id],now,WINDOWS[window][0],cfg.max_gap_seconds);history=current_history(db,frame,[device_id])
    comparisons={};active_baselines={}
    for key,(duration,_,_) in WINDOWS.items():
        selected=frame if key==window else frame_for([device_id],now,duration,cfg.max_gap_seconds)
        values=baseline_stats(db,selected,now,key,cfg)
        comparisons[key]=[{'series_key':series,'baseline':values.get(context_key(rows[-1]),{})} for (_,series),rows in history.items()]
        if key==window:active_baselines=values
    reports=series_reports(history,active_baselines,now,cfg);buckets,truncated=historical_buckets(db,frame,window)
    lookup={(r['series_key'],r['mapping_digest']):r['baseline'] for r in reports}
    for bucket in buckets:bucket['health_score']=score(bucket,lookup.get((bucket['series_key'],bucket['mapping_digest']),{}),cfg)
    recurrence=recurring_periods(db,frame,cfg) if window in {'7d','30d'} else []
    checkpoint=db.get(RFAnalyticsCheckpoint,'rf-v1')
    return {'device_id':device.id,'device_name':device.name,'window':window,'timezone':cfg.analysis_timezone,
        'generated_at':now.isoformat(),'series':reports,'comparisons':comparisons,'buckets':buckets,'buckets_truncated':truncated,
        'policy':cfg.model_dump(),
        'recurring_periods':recurrence,'projection':{'last_source_id':checkpoint.last_source_id,'updated_at':checkpoint.updated_at.isoformat()} if checkpoint else None,
        'baseline_method':'disjoint historical p10/median/p90; minimum count, valid fraction and time span; same mapping and channel context',
        'read_only':True,'execution_enabled':False,'accuracy':'not_validated_against_real_world_interference'}
