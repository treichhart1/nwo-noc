"""Explicit offline radio fixture. RFC 5612 documentation OIDs, never vendor OIDs."""
from datetime import datetime, timedelta, timezone
import json
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device
from app.integrations.radio_snmp import MIBMapping, RadioCollector, SNMPPolicy
from app.services.radio_history import stage_radio


def main():
    if not settings.offline_mode or settings.app_env != 'staging' or not settings.synthetic_monitoring_enabled:
        raise SystemExit('Radio rehearsal requires explicit offline synthetic staging')
    units = {'rssi_dbm':'dbm','remote_rssi_dbm':'dbm','snr_db':'db','noise_floor_dbm':'dbm',
        'frequency_mhz':'hz','channel_width_mhz':'hz','link_capacity_mbps':'bits_per_second',
        'tx_rate_mbps':'bits_per_second','rx_rate_mbps':'bits_per_second',
        'tx_throughput_mbps':'bits_per_second','rx_throughput_mbps':'bits_per_second',
        'ccq_percent':'percent','uptime_seconds':'centiseconds','radio_status':'enum','retries':'count'}
    now=datetime.now(timezone.utc);ids={}
    with SessionLocal() as db:
        devices=list(db.scalars(select(Device).order_by(Device.id)))
        if not devices or any(d.source != 'synthetic' for d in devices):raise SystemExit('Synthetic inventory required')
        for vendor,profile in [('ubiquiti','airmax-ac-mib-v1'),('cambium','epmp-4600-mib-v1')]:
            device=next((d for d in devices if (d.vendor or '').casefold()==vendor),None)
            if device is None:raise SystemExit('Expected the synthetic WISP fixture')
            fields={name:dict(oid=f'1.3.6.1.4.1.32473.87.{i+1}.0',unit=unit,**({'states':{'1':'up','2':'down'}} if name=='radio_status' else {})) for i,(name,unit) in enumerate(units.items())}
            m=MIBMapping(profile=profile,mib_sha256='b'*64,fields=fields)
            collector=RadioCollector(m,SNMPPolicy(address='192.0.2.87',allowed_addresses=()))
            for i in range(6):
                values=dict(rssi_dbm=-60 if i<3 else -85,remote_rssi_dbm=-63,snr_db=30 if i<3 else 5,
                    noise_floor_dbm=-95 if i<3 else -70,frequency_mhz=5800000000,channel_width_mhz=40000000,
                    link_capacity_mbps=500000000 if i<3 else 50000000,tx_rate_mbps=400000000,rx_rate_mbps=350000000,
                    tx_throughput_mbps=20000000,rx_throughput_mbps=10000000,ccq_percent=98 if i<3 else 40,
                    uptime_seconds=100000+i*200,radio_status=1,retries=10+i)
                when=now-timedelta(seconds=12-i*2)
                obs=collector.normalize({m.fields[k].oid:v for k,v in values.items()},observed_at=when,received_at=when)
                stage_radio(db,device,obs)
            ids[vendor]=device.id
        db.commit()
    print(json.dumps({'device_ids':ids,'samples':12,'network_requests':0,'simulation_only':True},sort_keys=True))


if __name__=='__main__':main()
