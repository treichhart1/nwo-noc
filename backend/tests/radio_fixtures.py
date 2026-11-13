"""Synthetic contracts in RFC 5612 documentation PEN 32473. NOT vendor OIDs."""
from datetime import datetime, timedelta, timezone
from app.integrations.radio_snmp import MIBMapping, RadioCollector, SNMPPolicy, SNMPCredentials, CAPABILITIES
NOW = datetime(2026,11,13,15,tzinfo=timezone.utc)
VALUES = dict(rssi_dbm=-60, remote_rssi_dbm=-62, snr_db=30, noise_floor_dbm=-95,
    frequency_mhz=5800000000, channel_width_mhz=40000000, tx_rate_mbps=400000000,
    rx_rate_mbps=350000000, link_capacity_mbps=500000000, uptime_seconds=100000,
    ccq_percent=98, tx_errors=5, rx_errors=2, retries=10, disconnects=0,
    radio_status=1, gps_sync=1, modulation=6, mcs_index=9, subscriber_count=15,
    temperature_c=40, tx_throughput_mbps=100000000, rx_throughput_mbps=50000000)

def mapping(profile='airmax-ac-mib-v1', omit=()):
    fields={}
    for i,key in enumerate(sorted(CAPABILITIES[profile])):
        if key in omit: continue
        unit='dbm' if key.endswith('_dbm') else 'db' if key.endswith('_db') else 'hz' if key.endswith('_mhz') else 'bits_per_second' if key.endswith('_mbps') else 'percent' if key.endswith('_percent') else 'centiseconds' if key=='uptime_seconds' else 'celsius' if key=='temperature_c' else 'enum' if key in {'radio_status','gps_sync','modulation'} else 'count'
        field={'oid':f'1.3.6.1.4.1.32473.87.{i+1}.0','unit':unit}
        if key=='radio_status':field['states']={'1':'up','2':'down','3':'degraded','4':'unknown'}
        if key=='gps_sync':field['states']={'1':'locked','2':'unlocked'}
        if key=='modulation':field['states']={'6':'1024QAM','1':'BPSK'}
        fields[key]=field
    return MIBMapping(profile=profile,mib_sha256='a'*64,fields=fields)

def policy(**changes):
    return SNMPPolicy(**(dict(address='192.0.2.87',allowed_addresses=('192.0.2.87',),enabled=True,
        admin_approved=True,mapping_validated=True)|changes))

def credentials():return SNMPCredentials(username='synthetic-user',auth_key='synthetic-auth-key',priv_key='synthetic-private-key')

def raw(m,**changes):
    v=VALUES|changes
    return {spec.oid:v[name] for name,spec in m.fields.items() if name in v}

def observation(profile='airmax-ac-mib-v1',at=NOW,**changes):
    m=mapping(profile)
    return RadioCollector(m,policy()).normalize(raw(m,**changes),observed_at=at,received_at=at)
