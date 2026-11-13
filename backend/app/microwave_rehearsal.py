"""Offline RFC 5612 documentation-OID backhaul fixture; no network requests."""
from datetime import datetime,timedelta,timezone
import json
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device,NetworkLink
from app.integrations.radio_snmp import SNMPPolicy
from app.integrations.microwave_snmp import MicrowaveMapping,MicrowaveCollector
from app.services.microwave_history import stage_microwave


def main():
    if not settings.offline_mode or settings.app_env!='staging' or not settings.synthetic_monitoring_enabled:
        raise SystemExit('Microwave rehearsal requires explicit offline synthetic staging')
    units={'local_rsl_dbm':'dbm','remote_rsl_dbm':'dbm','local_tx_power_dbm':'dbm','remote_tx_power_dbm':'dbm',
        'snr_db':'db','frequency_mhz':'khz','bandwidth_mhz':'khz','capacity_mbps':'bits_per_second',
        'tx_throughput_mbps':'bits_per_second','rx_throughput_mbps':'bits_per_second','temperature_c':'celsius',
        'availability_percent':'percent','radio_status':'enum','modulation':'enum','adaptive_modulation':'enum',
        'errors':'count','alarm_signal_low':'enum','alarm_link_loss':'enum'}
    states={'radio_status':{'1':'up','2':'down'},'modulation':{'8':'256QAM','4':'16QAM'},'adaptive_modulation':{'1':'enabled'},
        'alarm_signal_low':{'0':'clear','1':'active'},'alarm_link_loss':{'0':'clear','1':'active'},'ethernet_status':{'1':'up'}}
    link_units={'reported_capacity_mbps':'bits_per_second','tx_throughput_mbps':'bits_per_second','rx_throughput_mbps':'bits_per_second',
        'uptime_seconds':'centiseconds','ethernet_status':'enum','firmware_version':'version','hardware_id':'count'}
    now=datetime.now(timezone.utc);ids={}
    with SessionLocal() as db:
        devices=list(db.scalars(select(Device).order_by(Device.id)))
        if not devices or any(d.source!='synthetic' for d in devices):raise SystemExit('Synthetic inventory required')
        for vendor,profile,count in [('racom','racom-ray3-mib-v1',2),('aviat','aviat-multicarrier-mib-v1',4)]:
            device=next((d for d in devices if (d.vendor or '').casefold()==vendor),None)
            if device is None:raise SystemExit('Expected synthetic Racom/Aviat inventory')
            by_id={d.id:d for d in devices};ancestors={device.id};parent=device.parent_device_id
            while parent in by_id and parent not in ancestors:
                ancestors.add(parent);parent=by_id[parent].parent_device_id
            target=next(d for d in devices if d.role=='tower-router' and d.wisp_site_id!=device.wisp_site_id and d.id not in ancestors)
            name=f'Synthetic NOC88 {vendor} microwave'
            link=db.scalar(select(NetworkLink).where(NetworkLink.name==name))
            if link is None:
                link=NetworkLink(name=name,a_site_id=device.wisp_site_id,z_site_id=target.wisp_site_id,a_device_id=device.id,z_device_id=target.id,vendor=vendor,status='active')
                db.add(link);db.flush()
            n=0
            def field(name,unit):
                nonlocal n
                n+=1;return {'oid':f'1.3.6.1.4.1.32473.88.{n}.0','unit':unit,'states':states.get(name,{})}
            carriers={f'c{i}':{name:field(name,unit) for name,unit in units.items()} for i in range(1,count+1)}
            mapping=MicrowaveMapping(profile=profile,mib_sha256='d'*64,carriers=carriers,
                link_fields={name:field(name,unit) for name,unit in link_units.items()})
            collector=MicrowaveCollector(mapping,SNMPPolicy(address='192.0.2.88',allowed_addresses=()))
            for sample in range(6):
                values={}
                for carrier,fields in mapping.carriers.items():
                    failed=carrier=='c1' and sample>=3
                    metrics={'local_rsl_dbm':-85 if failed else -60,'remote_rsl_dbm':-62,'local_tx_power_dbm':20,'remote_tx_power_dbm':19,
                        'snr_db':8 if failed else 30,'frequency_mhz':11000000 if carrier in {'c1','c2'} else 18000000,'bandwidth_mhz':80000,
                        'capacity_mbps':50000000 if failed else 500000000,'tx_throughput_mbps':20000000,'rx_throughput_mbps':10000000,
                        'temperature_c':40,'availability_percent':95 if failed else 100,'radio_status':2 if failed else 1,
                        'modulation':4 if failed else 8,'adaptive_modulation':1,'errors':10+sample,'alarm_signal_low':int(failed),'alarm_link_loss':int(failed)}
                    values.update({spec.oid:metrics[name] for name,spec in fields.items()})
                metrics={'reported_capacity_mbps':900000000 if vendor=='racom' else 1800000000,
                    'tx_throughput_mbps':100000000,'rx_throughput_mbps':50000000,'uptime_seconds':100000+sample*200,
                    'ethernet_status':1,'firmware_version':'3.2.1','hardware_id':88}
                values.update({spec.oid:metrics[name] for name,spec in mapping.link_fields.items()})
                when=now-timedelta(seconds=12-sample*2)
                stage_microwave(db,device,link,collector.normalize(values,observed_at=when,received_at=when))
            ids[vendor]=device.id
        db.commit()
    print(json.dumps({'device_ids':ids,'samples':12,'network_requests':0,'simulation_only':True},sort_keys=True))


if __name__=='__main__':main()
