"""Evidence-based, nonexecuting RF analysis. Synthetic accuracy is not field accuracy."""
import math
from datetime import datetime,timezone
from app.services.rf_analytics_policy import AnalyticsPolicy


def number(point,key):
    value=point.get(key)
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None


def score(point,baseline,policy=None):
    cfg=policy or AnalyticsPolicy();contributions=[]
    def add(name,value,penalty,measurement):
        if name in cfg.weights and value is not None:
            contributions.append({'factor':name,'value':value,'measurement':measurement,'penalty':round(penalty,4),'weight':cfg.weights[name]})
    signal=number(point,'signal_dbm');snr=number(point,'snr_db');quality=number(point,'quality_percent')
    capacity=number(point,'capacity_mbps');util=number(point,'utilization_percent')
    add('signal',signal,.7 if signal is not None and signal<cfg.signal_low else 0,'signal_dbm')
    add('snr',snr,.7 if snr is not None and snr<cfg.score_snr_min else 0,'snr_db')
    add('quality',quality,.7 if quality is not None and quality<cfg.score_quality_min else 0,'quality_percent')
    normal=baseline.get('capacity_mbps',{});reference=normal.get('median') if normal.get('ready') else None
    add('capacity',capacity,.7 if capacity is not None and ((reference and capacity<reference*(1-cfg.capacity_drop_percent/100)) or reference is None and capacity<cfg.score_capacity_min) else 0,'capacity_mbps')
    add('utilization',util,.3 if util is not None and util>=cfg.utilization_enter else 0,'utilization_percent')
    rates=[number(point,k) for k in ('tx_errors_per_second','rx_errors_per_second')]
    rates=[v for v in rates if v is not None];error=max(rates) if rates else None
    retry=number(point,'retries_per_second')
    add('errors',error,.7 if error is not None and error>cfg.rate_enter else 0,'maximum known error events/second')
    add('retries',retry,.5 if retry is not None and retry>cfg.rate_enter else 0,'retries_per_second')
    state=point.get('radio_state')
    add('connection',state if state in {'up','down','degraded'} else None,1 if state=='down' else .5 if state=='degraded' else 0,'reported radio state')
    weight=sum(c['weight'] for c in contributions);possible=sum(cfg.weights.values())
    value=round(100*(1-sum(c['weight']*c['penalty'] for c in contributions)/weight),1) if weight else None
    caps=[]
    if state=='down' and value is not None and value>cfg.disconnected_score_cap:
        value=cfg.disconnected_score_cap;caps=[{'reason':'reported_radio_down','maximum':cfg.disconnected_score_cap}]
    return {'value':value,'metric_coverage':round(weight/possible,3),'contributions':contributions,
        'caps_applied':caps,
        'missing_factors':sorted(set(cfg.weights)-{c['factor'] for c in contributions}),
        'meaning':'Relative condition of available measurements, not failure probability or root-cause certainty'}


def analyze_history(points,baseline,*,now=None,policy=None):
    cfg=policy or AnalyticsPolicy();now=now or datetime.now(timezone.utc)
    if len(points)>1000:raise ValueError('Recent analysis history is bounded')
    states={};previous=None;sequence=[];valid_streak=0;evidence={};last=None
    def base(metric):
        b=baseline.get(metric,{})
        return b.get('median') if b.get('ready') else None
    def delta(p,key):
        value=number(p,key);normal=base(key)
        return value-normal if value is not None and normal is not None else None
    def check(name,value,enter,clear):
        item=states.setdefault(name,{'state':'unknown','enter':0,'clear':0})
        if value is None:item.update(state='unknown',enter=0,clear=0);return
        if enter(value):
            item['enter']+=1;item['clear']=0
            if item['enter']>=cfg.consecutive_samples:item['state']='active'
        elif clear(value):
            item['clear']+=1;item['enter']=0
            if item['clear']>=cfg.consecutive_samples:item['state']='clear'
        else:item['enter']=item['clear']=0
    for point in points:
        at=point['sampled_at']
        if at.tzinfo is None:raise ValueError('Analysis timestamps must be aware')
        gap=(at-previous['sampled_at']).total_seconds() if previous else None
        if gap is not None and gap<=0:raise ValueError('Analysis history must be chronological')
        changed=previous and (point.get('mapping_digest')!=previous.get('mapping_digest') or any(point.get(k)!=previous.get(k) for k in ('frequency_mhz','bandwidth_mhz','site_id','link_id')))
        restarted=previous and number(point,'uptime_seconds') is not None and number(previous,'uptime_seconds') is not None and point['uptime_seconds']<previous['uptime_seconds']
        fresh=0<=(now-at).total_seconds()<=cfg.stale_after_seconds
        if not point.get('valid',True) or not fresh:
            states={};sequence=[];valid_streak=0;evidence={};previous=point;last=point;continue
        if previous is None or gap>cfg.max_gap_seconds or changed or restarted:
            states={};sequence=[];valid_streak=0
        valid_streak+=1
        signal=number(point,'signal_dbm');noise=delta(point,'noise_floor_dbm');snr=delta(point,'snr_db');signal_change=delta(point,'signal_dbm')
        cap=number(point,'capacity_mbps');normal_cap=base('capacity_mbps')
        cap_drop=(normal_cap-cap)/normal_cap*100 if normal_cap is not None and normal_cap>0 and cap is not None else None
        util=number(point,'utilization_percent');throughput=[number(point,k) for k in ('tx_throughput_mbps','rx_throughput_mbps')]
        throughput=[v for v in throughput if v is not None]
        ratio=max(throughput)/cap if throughput and cap is not None and cap>0 else None
        check('weak_signal',signal,lambda v:v<cfg.signal_low,lambda v:v>=cfg.signal_clear)
        check('low_snr',number(point,'snr_db'),lambda v:v<cfg.score_snr_min,lambda v:v>=cfg.snr_clear)
        check('low_quality',number(point,'quality_percent'),lambda v:v<cfg.score_quality_min,lambda v:v>=cfg.quality_clear)
        check('low_capacity',cap,lambda v:v<cfg.score_capacity_min,lambda v:v>=cfg.capacity_clear)
        check('signal_decline',signal_change,lambda v:v<=-cfg.signal_drop_db,lambda v:v>-cfg.signal_drop_db/2)
        sudden=signal-number(previous,'signal_dbm') if previous and signal is not None and number(previous,'signal_dbm') is not None and gap<=cfg.max_gap_seconds and not changed and not restarted else None
        # A step persists relative to the pre-step level for the streak, rather
        # than demanding an impossible sequence of three successive large drops.
        step=states.setdefault('_step',{'reference':None})
        if sudden is not None and abs(sudden)>=cfg.signal_drop_db:step['reference']=previous['signal_dbm']
        step_delta=signal-step['reference'] if signal is not None and step['reference'] is not None else None
        check('sudden_signal_change',step_delta,lambda v:abs(v)>=cfg.signal_drop_db,lambda v:abs(v)<cfg.signal_drop_db/2)
        check('capacity_reduction',cap_drop,lambda v:v>=cfg.capacity_drop_percent,lambda v:v<cfg.capacity_drop_percent/2)
        mod=delta(point,'modulation_bits');mcs=delta(point,'mcs_index')
        fallback=mod
        check('modulation_fallback',fallback,lambda v:v<=-cfg.modulation_drop,lambda v:v>-cfg.modulation_drop/2)
        check('mcs_index_change',mcs,lambda v:abs(v)>=cfg.modulation_drop,lambda v:abs(v)<cfg.modulation_drop/2)
        joint=(noise,snr) if noise is not None and snr is not None else None
        check('noise_snr_pattern',joint,lambda v:v[0]>=cfg.noise_rise_db and v[1]<=-cfg.snr_drop_db,
              lambda v:v[0]<cfg.noise_rise_db/2 or v[1]>-cfg.snr_drop_db/2)
        stable=(fallback,signal_change) if fallback is not None and signal_change is not None else None
        check('stable_signal_fallback',stable,lambda v:v[0]<=-cfg.modulation_drop and abs(v[1])<cfg.signal_drop_db/2,
              lambda v:v[0]>-cfg.modulation_drop/2 or abs(v[1])>=cfg.signal_drop_db)
        check('high_utilization',util,lambda v:v>=cfg.utilization_enter,lambda v:v<=cfg.utilization_clear)
        check('throughput_saturation',ratio,lambda v:v>=cfg.saturation_enter,lambda v:v<=cfg.saturation_clear)
        rates=[number(point,k) for k in ('tx_errors_per_second','rx_errors_per_second','retries_per_second')]
        rates=[v for v in rates if v is not None];rate=max(rates) if rates else None
        increases=[delta(point,k) for k in ('tx_errors_per_second','rx_errors_per_second','retries_per_second')]
        increases=[v for v in increases if v is not None];increase=max(increases) if increases else None
        check('high_error_retry_rate',rate,lambda v:v>cfg.rate_enter,lambda v:v<=cfg.rate_clear)
        check('increasing_error_retry_rate',increase,lambda v:v>cfg.rate_enter,lambda v:v<=cfg.rate_clear)
        state=point.get('radio_state')
        check('radio_disconnected',state if state in {'up','down'} else None,lambda v:v=='down',lambda v:v=='up')
        if state in {'up','down'}:sequence.append(state);sequence=sequence[-(2*cfg.consecutive_samples+1):]
        else:sequence=[]
        evidence={'signal_change_db':signal_change,'noise_change_db':noise,'snr_change_db':snr,
            'modulation_bits_change':fallback,'mcs_index_change':mcs,'capacity_reduction_percent':cap_drop,'utilization_percent':util,
            'directional_throughput_capacity_ratio':ratio,'maximum_known_error_retry_rate':rate,
            'rate_increase_per_second':increase,'channel_context_known':point.get('frequency_mhz') is not None and point.get('bandwidth_mhz') is not None}
        previous=point;last=point
    active=sorted(k for k,v in states.items() if not k.startswith('_') and v['state']=='active')
    transitions=sum(a=='up' and b=='down' for a,b in zip(sequence,sequence[1:]))
    if transitions>=cfg.consecutive_samples:active.append('recurring_disconnects')
    freshness='missing' if last is None else 'future' if last['sampled_at']>now else 'stale' if (now-last['sampled_at']).total_seconds()>cfg.stale_after_seconds else 'invalid' if not last.get('valid',True) else 'fresh'
    suspected='noise_snr_pattern' in active or 'stable_signal_fallback' in active and 'high_error_retry_rate' in active
    stable_rf=all(evidence.get(k) is not None and abs(evidence[k])<3 for k in ('signal_change_db','snr_change_db'))
    congested='high_utilization' in active and 'throughput_saturation' in active and stable_rf and not suspected
    classification='suspected_interference' if suspected else 'likely_congestion' if congested else 'rf_degradation' if any(k in active for k in ('weak_signal','low_snr','low_quality','low_capacity','signal_decline','sudden_signal_change','modulation_fallback','capacity_reduction','radio_disconnected','recurring_disconnects','high_error_retry_rate')) else 'performance_change_unclassified' if 'mcs_index_change' in active else 'utilization_unclassified' if active else 'no_sustained_pattern'
    baseline_ready=sum(bool(v.get('ready')) for v in baseline.values())
    scoring=score(last or {},baseline,cfg)
    support=valid_streak>=cfg.consecutive_samples
    confidence='high' if scoring['metric_coverage']>=.75 and baseline_ready>=6 and support else 'medium' if scoring['metric_coverage']>=.5 and baseline_ready>=3 and support else 'low'
    state='degraded' if active else 'observed_stable' if support and baseline_ready>=3 and scoring['metric_coverage']>=.5 else 'insufficient_baseline'
    expected=('signal_dbm','snr_db','noise_floor_dbm','capacity_mbps','quality_percent','modulation_bits','mcs_index','utilization_percent')
    missing_expected=[k for k in expected if baseline.get(k,{}).get('ready') and number(last or {},k) is None]
    if missing_expected:
        if not active:state='partial_telemetry'
        confidence='medium' if confidence=='high' else confidence
    if freshness!='fresh':state='unknown';active=[];classification='insufficient_evidence';scoring['value']=None;scoring['contributions']=[];confidence='low'
    return {'state':state,'classification':classification,'interference_status':'suspected' if suspected and freshness=='fresh' else 'not_established',
        'root_cause':'unconfirmed','patterns':active,'evidence':evidence,'freshness':freshness,
        'score':scoring,'confidence':{'level':confidence,'baseline_metrics_ready':baseline_ready,'valid_recent_streak':valid_streak,
            'meaning':'Data support and coverage, not diagnostic accuracy'},
        'checks':{k:v for k,v in states.items() if not k.startswith('_')},'observed_disconnect_transitions':transitions,
        'missing_expected_metrics':missing_expected,
        'alternatives':['path loss','congestion','configuration or firmware change','equipment or measurement issue'],
        'execution_enabled':False,'mode':'read_only'}
