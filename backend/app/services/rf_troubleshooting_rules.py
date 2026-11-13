"""Pure deterministic rules. No network clients, commands, metadata or model prose."""
from math import isfinite

CHECKS={
 'collection':'Verify collection timestamps, mapping, units and projection health before diagnosing RF.',
 'signal':'Compare both ends and historical signal; arrange a safe visual inspection for obstruction, antenna/feed damage or alignment drift. Do not adjust equipment here.',
 'noise':'Compare noise, SNR, modulation and errors on both ends over the same interval; review independently obtained spectrum evidence with an authorized technician.',
 'capacity':'Compare actual directional traffic, reported capacity and utilization; review capacity planning and known redundancy with engineering.',
 'carrier':'Inspect reported carrier alarms, modulation and per-carrier capacity against the engineered design and maintenance history.',
 'ethernet':'Review existing switch/interface counters, cable/power alarms and upstream reachability records; do not execute device commands.',
 'upstream':'Compare parent/backhaul timestamps with downstream failures and maintenance records; treat upstream correlation as probable, not proven.',
 'hardware':'Review reported temperature and hardware alarms against the approved model limits; arrange authorized inspection of cooling, power and hardware.',
 'history':'Compare disconnect/recovery history and independent observations; preserve logs before drawing conclusions.',
}
# summary, possible causes, checks, severity. Causes are never confirmed diagnoses.
RULES={
 'weak_signal':('Persistently weak or deteriorating signal',['path obstruction','alignment or feed degradation','measurement change'],['signal','collection'],2),
 'low_snr':('Sustained low SNR',['increased noise','weak wanted signal','measurement issue'],['noise','signal'],2),
 'interference':('Joint RF evidence suggests possible interference',['external interference','equipment or measurement issue'],['noise','collection'],2),
 'modulation':('Modulation or capacity has declined',['RF impairment','carrier loss','firmware or measurement change'],['carrier','noise'],2),
 'congestion':('Measured load suggests capacity pressure',['traffic demand approaching available capacity'],['capacity'],2),
 'disconnect':('Wireless disconnects or intermittent instability observed',['RF interruption','power or hardware interruption','upstream loss'],['history','signal','ethernet'],3),
 'carrier':('Microwave carrier degradation observed',['carrier-specific RF impairment','hardware or power fault'],['carrier','hardware'],3),
 'outage':('All observed microwave carriers are down',['RF outage','power or equipment failure'],['carrier','upstream'],4),
 'ethernet':('Ethernet or routing health evidence needs investigation',['local Ethernet fault','upstream connectivity issue'],['ethernet','upstream'],3),
 'upstream':('Concurrent downstream failures correlate with an offline parent',['probable upstream outage','shared power or monitoring failure'],['upstream','ethernet'],4),
 'hardware':('High temperature, resource load or hardware alarm observed',['cooling or power issue','resource pressure','hardware fault'],['hardware','history'],3),
 'data_quality':('Insufficient, stale or conflicting diagnostic evidence',['collection delay','unsupported measurement','conflicting sources'],['collection'],1),
}
PATTERNS={
 'weak_signal':'weak_signal','signal_decline':'weak_signal','sudden_signal_change':'weak_signal',
 'low_snr':'low_snr','noise_snr_pattern':'interference','modulation_fallback':'modulation',
 'capacity_reduction':'modulation','low_capacity':'modulation','mcs_index_change':'modulation',
 'radio_disconnected':'disconnect','recurring_disconnects':'disconnect',
 'low_quality':'modulation','high_error_retry_rate':'modulation','increasing_error_retry_rate':'modulation',
}
MICRO_RULES={'weak_rsl':'weak_signal','capacity_low':'modulation','modulation_low':'modulation','capacity_reduction':'modulation',
 'signal_drop':'weak_signal','signal_decline':'weak_signal','modulation_fallback':'modulation','high_error_rate':'carrier','carrier_down':'carrier','temperature_high':'hardware','alarm_temperature_high':'hardware',
 'alarm_hardware_warning':'hardware','alarm_ethernet_fault':'ethernet','alarm_link_loss':'carrier',
 'alarm_signal_low':'weak_signal','availability_low':'disconnect','link_instability':'disconnect','low_fade_margin':'carrier'}


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value)


def series_rules(series,*,temperature_warning_c=80):
    a=series['analysis'];result=set()
    if a['freshness']!='fresh':return {'data_quality'}
    for p in a['patterns']:
        if p in PATTERNS:result.add(PATTERNS[p])
    if a['classification']=='suspected_interference':result.add('interference')
    if a['classification']=='likely_congestion':result.add('congestion')
    if a['state'] in {'insufficient_baseline','partial_telemetry','unknown'}:result.add('data_quality')
    if finite(series['latest'].get('temperature_c')) and series['latest']['temperature_c']>temperature_warning_c:result.add('hardware')
    return result


def priority(rule,*,duration_seconds=0,recurrences=0,downstream_devices=0,confidence='low',incident_ids=()):
    severity=RULES[rule][3]
    components={'severity':severity*15,'observed_duration':min(10,int(max(0,duration_seconds)//1800)),
        'recurrence':min(10,max(0,recurrences)*2),'potential_downstream_devices':min(15,max(0,downstream_devices)),
        'evidence_quality':{'high':10,'medium':5,'low':0}.get(confidence,0),'existing_incident':5 if incident_ids else 0}
    score=min(100,sum(components.values()))
    return {'score':score,'level':'urgent' if score>=75 else 'high' if score>=50 else 'normal',
        'components':components,'redundancy':'unknown; parallel inventory links do not establish working failover',
        'duration_meaning':'time since first evaluation of this episode, not measured physical fault duration',
        'impact_meaning':'potential downstream devices, not customers; current inventory relationships only'}


def candidate(rule,*,scope,facts,history=None,confidence='low',missing=None):
    summary,causes,checks,severity=RULES[rule]
    return {'rule':rule,'scope':scope,'summary':summary,'facts':facts,'history':history or {},
        'possible_causes':causes,'root_cause':'unconfirmed','confidence':confidence,
        'confidence_meaning':'evidence support, not field-validated diagnostic accuracy',
        'check_ids':checks,'technician_checks':[{'id':key,'text':CHECKS[key]} for key in checks],
        'missing_information':missing or ['Independent technician confirmation and model-specific engineered limits'],
        'safety':['Human review required. No device commands or remediation are executed.',
            'Do not change RF, routing or firewall settings through this workflow.'],
        'execution_enabled':False,'synthetic_accuracy_validated':False}
