"""Stage four vendor-shaped synthetic observations in the offline WISP fixture."""
from datetime import datetime,timezone
import json
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device
from app.integrations.vendor_rf import PROFILES,STATUS_FIELDS,VendorRFCollector
from app.services.vendor_rf_observations import stage_observation


def main():
    if not settings.offline_mode or settings.app_env!='staging' or not settings.synthetic_monitoring_enabled:
        raise SystemExit('Vendor RF rehearsal requires explicit offline synthetic staging')
    canonical=dict(rssi_dbm=-65,snr_db=30,noise_floor_dbm=-95,frequency_mhz=5800,
                   channel_width_mhz=40,link_capacity_mbps=500)
    now=datetime.now(timezone.utc);ids={}
    with SessionLocal() as db:
        devices=list(db.scalars(select(Device).order_by(Device.id)))
        if not devices or any(d.source!='synthetic' for d in devices):raise SystemExit('Synthetic inventory required')
        for vendor,profile in PROFILES.items():
            device=next((d for d in devices if (d.vendor or '').casefold()==vendor),None)
            if device is None:raise SystemExit('Expected the four-vendor synthetic WISP fixture')
            raw={source:canonical[target]/scale for target,(source,scale) in profile.fields.items() if target in canonical}
            raw[STATUS_FIELDS[vendor]]=next(state for state,value in profile.states.items() if value=='up')
            observation=VendorRFCollector(vendor).normalize({profile.node:raw},observed_at=now,received_at=now)
            stage_observation(db,device,observation);ids[vendor]=device.id
        db.commit()
    print(json.dumps({'device_ids':ids,'count':4,'read_only':True,'network_requests':0},sort_keys=True))


if __name__=='__main__':main()
