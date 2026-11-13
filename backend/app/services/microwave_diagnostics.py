"""Replayable hysteresis from bounded history. No actions or root-cause claims."""
from datetime import datetime, timezone
from app.services.rf_freshness import evaluate_rf_freshness
from app.services.microwave_model import MODULATIONS
from app.services.microwave_policy import MicrowaveThresholds


def continuity(previous,current,carrier):
    if previous is None:return {'state':'baseline','errors_per_second':None}
    elapsed=(current.observed_at-previous.observed_at).total_seconds()
    if elapsed<=0:return {'state':'out_of_order','errors_per_second':None}
    if previous.mapping_digest!=current.mapping_digest or set(previous.carriers)!=set(current.carriers):return {'state':'mapping_changed','errors_per_second':None}
    old,new=previous.measurements.uptime_seconds,current.measurements.uptime_seconds
    if old is None or new is None:return {'state':'uptime_unknown','errors_per_second':None}
    if new<old:return {'state':'device_restart','errors_per_second':None}
    a,b=previous.carriers[carrier].errors,current.carriers[carrier].errors
    if a is None or b is None:return {'state':'counter_missing','errors_per_second':None}
    if b<a:return {'state':'counter_reset','errors_per_second':None}
    return {'state':'continuous','errors_per_second':(b-a)/elapsed,'delta_errors':b-a,'elapsed_seconds':elapsed}


def analyze_microwave(observations,*,now=None,thresholds=None):
    cfg=thresholds or MicrowaveThresholds();now=now or datetime.now(timezone.utc)
    if len(observations)>1000:raise ValueError('Microwave history is bounded')
    states={};previous=None;baseline=None;transitions={};rate_baselines={};counter_status={};carrier_reports={}
    def check(key,value,enter,clear,*,supported=True):
        if not supported:states[key]={'state':'unsupported','enter':0,'clear':0};return 'unsupported'
        item=states.setdefault(key,{'state':'unknown','enter':0,'clear':0})
        if value is None:
            item.update(state='unknown',enter=0,clear=0);return 'unknown'
        if enter(value):
            item['enter']+=1;item['clear']=0
            if item['enter']>=cfg.consecutive_samples:item['state']='active'
        elif clear(value):
            item['clear']+=1;item['enter']=0
            if item['clear']>=cfg.consecutive_samples:item['state']='clear'
        else:item['enter']=item['clear']=0
        return item['state']
    for obs in observations:
        gap=(obs.observed_at-previous.observed_at).total_seconds() if previous else None
        if gap is not None and gap<=0:raise ValueError('Microwave history must be chronological')
        if evaluate_rf_freshness(obs.observed_at,now=now,stale_after_seconds=cfg.stale_after_seconds)['status']!='fresh':
            states={};transitions={};rate_baselines={};carrier_reports={};baseline=None;previous=obs;continue
        restart=previous and previous.measurements.uptime_seconds is not None and obs.measurements.uptime_seconds is not None and obs.measurements.uptime_seconds<previous.measurements.uptime_seconds
        boundary=baseline is None or previous is None or gap>cfg.max_gap_seconds or restart or obs.mapping_digest!=previous.mapping_digest or set(obs.carriers)!=set(previous.carriers)
        if boundary:states={};transitions={};rate_baselines={};baseline=obs
        carrier_reports={}
        for key,carrier in obs.carriers.items():
            m=carrier.measurements;b=baseline.carriers[key].measurements
            specs=[('weak_rsl',m.local_rsl_dbm,lambda v:v<cfg.rsl_enter_dbm,lambda v:v>=cfg.rsl_clear_dbm,'local_rsl_dbm'),
                ('capacity_low',m.capacity_mbps,lambda v:v<cfg.capacity_enter_mbps,lambda v:v>=cfg.capacity_clear_mbps,'capacity_mbps'),
                ('temperature_high',m.temperature_c,lambda v:v>cfg.temperature_enter_c,lambda v:v<=cfg.temperature_clear_c,'temperature_c'),
                ('availability_low',m.availability_percent,lambda v:v<cfg.availability_enter_percent,lambda v:v>=cfg.availability_clear_percent,'availability_percent'),
                ('modulation_low',MODULATIONS.get(carrier.modulation),lambda v:v<cfg.modulation_enter_bits,lambda v:v>=cfg.modulation_clear_bits,'modulation'),
                ('carrier_down',carrier.radio_status if carrier.radio_status!='unknown' else None,lambda v:v=='down',lambda v:v=='up','radio_status')]
            conditions={}
            for reason,value,enter,clear,field in specs:
                conditions[reason]=check(f'{key}.{reason}',value,enter,clear,supported=carrier.unavailable.get(field)!='unsupported')
            reference=carrier.engineered_reference
            reference_valid=reference is not None and carrier.modulation==reference.modulation and m.bandwidth_mhz==reference.bandwidth_mhz
            fade=m.local_rsl_dbm-reference.receiver_threshold_dbm if reference_valid and m.local_rsl_dbm is not None else None
            conditions['low_fade_margin']=check(f'{key}.fade',fade,lambda v:v<cfg.fade_enter_db,lambda v:v>=cfg.fade_clear_db,supported=reference is not None)
            signal_drop=b.local_rsl_dbm-m.local_rsl_dbm if b.local_rsl_dbm is not None and m.local_rsl_dbm is not None else None
            capacity_drop=(b.capacity_mbps-m.capacity_mbps)/b.capacity_mbps*100 if b.capacity_mbps and m.capacity_mbps is not None else None
            conditions['signal_decline']=check(f'{key}.signal_decline',signal_drop,lambda v:v>=cfg.signal_decline_db,lambda v:v<cfg.signal_decline_db/2,supported=carrier.unavailable.get('local_rsl_dbm')!='unsupported')
            conditions['capacity_reduction']=check(f'{key}.capacity_reduction',capacity_drop,lambda v:v>=cfg.capacity_drop_percent,lambda v:v<cfg.capacity_drop_percent/2,supported=carrier.unavailable.get('capacity_mbps')!='unsupported')
            base_mod=MODULATIONS.get(baseline.carriers[key].modulation);current_mod=MODULATIONS.get(carrier.modulation)
            mod_drop=base_mod-current_mod if base_mod is not None and current_mod is not None else None
            conditions['modulation_fallback']=check(f'{key}.modulation_fallback',mod_drop,lambda v:v>=cfg.modulation_drop_bits,lambda v:v<cfg.modulation_drop_bits/2,supported=carrier.unavailable.get('modulation')!='unsupported')
            count=continuity(previous,obs,key);counter_status[key]=count
            rate=count['errors_per_second']
            if count['state'] in {'counter_reset','device_restart','mapping_changed','uptime_unknown','counter_missing'}:rate_baselines.pop(key,None)
            if rate is not None:rate_baselines.setdefault(key,rate)
            rate_rise=rate-rate_baselines[key] if rate is not None and key in rate_baselines else None
            conditions['high_error_rate']=check(f'{key}.errors',rate,lambda v:v>cfg.errors_enter_per_second,lambda v:v<=cfg.errors_clear_per_second,supported=carrier.unavailable.get('errors')!='unsupported')
            conditions['errors_increasing']=check(f'{key}.error_rise',rate_rise,lambda v:v>cfg.error_rate_rise_enter,lambda v:v<=cfg.error_rate_rise_clear,supported=carrier.unavailable.get('errors')!='unsupported')
            for alarm,state in carrier.alarms.items():
                conditions[f'alarm_{alarm}']=check(f'{key}.alarm_{alarm}',None if state=='unknown' else state,lambda v:v=='active',lambda v:v=='clear')
            for alarm in ['signal_low','temperature_high','link_loss','ethernet_fault','hardware_warning']:
                if alarm not in carrier.alarms and carrier.unavailable.get(f'alarm_{alarm}')!='unsupported':
                    conditions[f'alarm_{alarm}']=check(f'{key}.alarm_{alarm}',None,lambda v:False,lambda v:False)
            sequence=transitions.setdefault(key,[])
            if carrier.radio_status not in {'up','down'}:sequence.clear()
            else:sequence.append(carrier.radio_status);sequence[:]=sequence[-(2*cfg.consecutive_samples+1):]
            unstable=sum(a=='up' and b=='down' for a,b in zip(sequence,sequence[1:]))>=cfg.consecutive_samples
            reasons=sorted(k for k,v in conditions.items() if v=='active')
            if unstable:reasons.append('link_instability')
            core={'weak_rsl','capacity_low','modulation_low','carrier_down'}
            healthy=all(conditions.get(k)=='clear' for k in core) and all(v in {'clear','unsupported'} for v in conditions.values()) and not unstable
            rf_conditions={k:v for k,v in conditions.items() if k not in {'alarm_ethernet_fault','alarm_hardware_warning'}}
            rf_active=any(v=='active' for v in rf_conditions.values()) or unstable
            rf_healthy=all(conditions.get(k)=='clear' for k in core) and all(v in {'clear','unsupported'} for v in rf_conditions.values()) and not unstable
            carrier_reports[key]={'state':'down' if conditions['carrier_down']=='active' else 'degraded' if reasons else 'healthy' if healthy else 'unknown',
                'rf_state':'down' if conditions['carrier_down']=='active' else 'degraded' if rf_active else 'healthy' if rf_healthy else 'unknown',
                'reasons':reasons,'conditions':conditions,'fade_margin_db':fade,'reference_valid':reference_valid,
                'counter_status':count,'reported_alarms':carrier.alarms}
        eth=check('link.ethernet',None if obs.ethernet_status=='unknown' else obs.ethernet_status,lambda v:v=='down',lambda v:v=='up',supported=obs.unavailable.get('ethernet_status')!='unsupported')
        previous=obs
    latest=observations[-1] if observations else None
    freshness=evaluate_rf_freshness(latest.observed_at if latest else None,now=now,stale_after_seconds=cfg.stale_after_seconds)
    rf='down' if carrier_reports and all(c['rf_state']=='down' for c in carrier_reports.values()) else 'degraded' if any(c['rf_state'] in {'down','degraded'} for c in carrier_reports.values()) else 'healthy' if carrier_reports and all(c['rf_state']=='healthy' for c in carrier_reports.values()) else 'unknown'
    ethernet=states.get('link.ethernet',{}).get('state','unknown')
    eth_alarm=any(c['conditions'].get('alarm_ethernet_fault')=='active' for c in carrier_reports.values())
    state='outage' if rf=='down' else 'degraded' if rf=='degraded' or ethernet=='active' or any(c['state']=='degraded' for c in carrier_reports.values()) else 'healthy' if rf=='healthy' and ethernet=='clear' and all(c['state']=='healthy' for c in carrier_reports.values()) else 'unknown'
    reasons=sorted({f'{key}:{reason}' for key,r in carrier_reports.items() for reason in r['reasons']}|({'ethernet_failure'} if ethernet=='active' else set()))
    if freshness['status']!='fresh':state=rf='unknown';reasons=[]
    return {'state':state,'rf_state':rf,'ethernet_state':'down' if ethernet=='active' else 'conflicting' if eth_alarm and ethernet=='clear' else 'up' if ethernet=='clear' else 'unknown',
        'freshness':freshness,'reasons':reasons,'carriers':carrier_reports,'thresholds':cfg.model_dump(),
        'root_cause':'unconfirmed','execution_enabled':False,'mode':'read_only'}
