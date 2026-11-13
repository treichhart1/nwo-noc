"""RFC 5612 PEN 32473 synthetic instance OIDs. Not a vendor MIB catalog."""
from datetime import datetime,timezone
from app.integrations.microwave_snmp import MicrowaveMapping,MicrowaveCollector,units_for
from app.integrations.radio_snmp import SNMPPolicy

NOW=datetime(2026,11,13,16,tzinfo=timezone.utc)
CARRIER_VALUES={'local_rsl_dbm':-60,'remote_rsl_dbm':-62,'local_tx_power_dbm':20,'remote_tx_power_dbm':19,
    'snr_db':30,'frequency_mhz':18000000,'bandwidth_mhz':80000,'capacity_mbps':500000000,
    'tx_throughput_mbps':100000000,'rx_throughput_mbps':50000000,'utilization_percent':30,'temperature_c':40,
    'availability_percent':100,'radio_status':1,'modulation':8,'adaptive_modulation':1,'errors':10,
    'alarm_signal_low':0,'alarm_temperature_high':0,'alarm_link_loss':0,'alarm_ethernet_fault':0,'alarm_hardware_warning':0}
LINK_VALUES={'reported_capacity_mbps':950000000,'tx_throughput_mbps':200000000,'rx_throughput_mbps':100000000,
    'utilization_percent':40,'uptime_seconds':100000,'ethernet_status':1,'firmware_version':'3.2.1','hardware_id':88}
STATES={'radio_status':{'1':'up','2':'down','3':'degraded','4':'unknown'},'ethernet_status':{'1':'up','2':'down','3':'unknown'},
    'modulation':{'8':'256QAM','6':'64QAM','4':'16QAM','2':'QPSK'},'adaptive_modulation':{'1':'enabled','2':'disabled','3':'unknown'}}

def mapping(profile='racom-ray3-mib-v1',carriers=2,omit=(),reference=False):
    n=0
    def field(name):
        nonlocal n
        n+=1;units=units_for(name)
        unit='khz' if 'khz' in units else 'bits_per_second' if 'bits_per_second' in units else 'centiseconds' if 'centiseconds' in units else next(iter(units))
        return {'oid':f'1.3.6.1.4.1.32473.88.{n}.0','unit':unit,
            'states':{'0':'clear','1':'active','2':'unknown'} if name.startswith('alarm_') else STATES.get(name,{})}
    c={f'c{i}':{name:field(name) for name in CARRIER_VALUES if name not in omit} for i in range(1,carriers+1)}
    refs={key:{'receiver_threshold_dbm':-90,'reference_sha256':'e'*64,'modulation':'256QAM','bandwidth_mhz':80} for key in c} if reference else {}
    return MicrowaveMapping(profile=profile,mib_sha256='c'*64,carriers=c,
        link_fields={name:field(name) for name in LINK_VALUES if name not in omit},engineered_references=refs)

def raw(m,*,changes=None,link_changes=None):
    changes=changes or {};values={}
    for key,fields in m.carriers.items():
        data=CARRIER_VALUES|({'frequency_mhz':11000000} if m.profile=='aviat-11ghz-mib-v1' else {})|changes.get(key,{})
        values.update({spec.oid:data[name] for name,spec in fields.items()})
    data=LINK_VALUES|(link_changes or {})
    values.update({spec.oid:data[name] for name,spec in m.link_fields.items()});return values

def observation(at=NOW,*,m=None,changes=None,link_changes=None):
    m=m or mapping();policy=SNMPPolicy(address='192.0.2.88',allowed_addresses=())
    return MicrowaveCollector(m,policy).normalize(raw(m,changes=changes,link_changes=link_changes),observed_at=at,received_at=at)
