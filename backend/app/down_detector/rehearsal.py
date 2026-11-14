"""Synthetic customer-backed outage and durable recorded-input recovery rehearsal."""

import json, sys
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device, NetworkLink, TelemetrySample
from app.customers.models import CustomerAccount, CustomerService
from .engine import ingest, reconcile
from .models import OutageEpisode, OutageObservation
from .contracts import Observation


def run(action):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit isolated synthetic rehearsal required")
    with SessionLocal() as db:
        account = db.scalar(
            select(CustomerAccount).where(CustomerAccount.external_id == "NOC95-C0001")
        )
        if not account:
            raise ValueError("NOC95 synthetic customer fixture required")
        service = db.scalar(
            select(CustomerService).where(CustomerService.customer_id == account.id)
        )
        link = db.scalar(
            select(NetworkLink).where(NetworkLink.z_site_id == service.site_id)
        )
        now = datetime.now(timezone.utc)
        if action == "seed":
            if db.scalar(select(OutageEpisode.id)):
                raise ValueError("Fresh detector fixture required")
            for second in [-90, -60, -30]:
                for kind, ident in [("link", link.id), ("device", service.device_id)]:
                    ingest(
                        db,
                        Observation(
                            event_id=f"SYN-NOC97:{kind}:{ident}:{second}",
                            source="synthetic",
                            kind=kind,
                            target_id=ident,
                            observed_at=now + timedelta(seconds=second),
                            signals={"icmp": False, "snmp": False, "collector": True},
                        ),
                        now=now,
                    )
            reconcile(db, now=now)
            root = db.scalar(
                select(OutageEpisode).where(OutageEpisode.key == f"link:{link.id}")
            )
            result = {
                "episode_id": root.id,
                "incident_id": root.incident_id,
                "site_id": service.site_id,
                "link_id": link.id,
                "simulation_only": True,
            }
        elif action == "queue":
            d = Device(
                source="synthetic",
                external_id="noc97-restart",
                name="NOC97 Synthetic recorded-input recovery",
                wisp_site_id=service.site_id,
            )
            db.add(d)
            db.flush()
            for second in [-90, -60, -30]:
                db.add(
                    TelemetrySample(
                        device_id=d.id,
                        sampled_at=now + timedelta(seconds=second),
                        raw_data={
                            "down_detector_v1": {
                                "signals": {
                                    "icmp": False,
                                    "snmp": False,
                                    "collector": True,
                                }
                            }
                        },
                    )
                )
            result = {
                "recorded_events_queued": 3,
                "device_id": d.id,
                "external_calls": 0,
            }
        elif action == "verify":
            from .worker import consume

            for _ in range(10):
                if consume(db, now=now)["processed"] == 0:
                    break
            d = db.scalar(select(Device).where(Device.external_id == "noc97-restart"))
            assert d
            eps = list(
                db.scalars(
                    select(OutageEpisode).where(OutageEpisode.key == f"device:{d.id}")
                )
            )
            assert len(eps) == 1
            n = db.scalar(
                select(func.count(OutageObservation.id)).where(
                    OutageObservation.key == f"device:{d.id}"
                )
            )
            assert n == 3
            consume(db, now=now)
            assert (
                db.scalar(
                    select(func.count(OutageEpisode.id)).where(
                        OutageEpisode.key == f"device:{d.id}"
                    )
                )
                == 1
            )
            result = {
                "observations_after_database_restart": n,
                "episodes": 1,
                "duplicate_episodes": 0,
                "external_calls": 0,
            }
        else:
            raise ValueError("Use seed, queue, verify")
        db.commit()
        return result


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1])))
