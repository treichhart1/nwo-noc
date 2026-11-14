"""Bounded recorded-monitoring bridge; never probes a network endpoint."""

from datetime import timedelta
from sqlalchemy import select, cast, String
from app.core.database import SessionLocal
from app.core.config import settings
from app.models.entities import Device, TelemetrySample, NetworkLink
from .contracts import Observation
from .models import OutageObservation
from .engine import ingest, reconcile, clock, policy, lock


def consume(db, *, now=None, limit=200):
    if not 1 <= limit <= 200:
        raise ValueError("Bounded recorded-event batch required")
    now = now or clock()
    lock(db)
    rows = list(
        db.scalars(
            select(TelemetrySample)
            .outerjoin(
                OutageObservation,
                OutageObservation.event_id
                == ("rf-sample:" + cast(TelemetrySample.id, String)),
            )
            .where(
                OutageObservation.id.is_(None),
                TelemetrySample.sampled_at
                >= now - timedelta(seconds=policy().stale_seconds),
                TelemetrySample.sampled_at <= now,
            )
            .order_by(TelemetrySample.sampled_at, TelemetrySample.id)
            .limit(limit)
        )
    )
    for row in rows:
        raw = row.raw_data or {}
        signals = {}
        kind = "device"
        ident = row.device_id
        data = raw.get("down_detector_v1")
        if isinstance(data, dict):
            body = {
                **data,
                "event_id": f"rf-sample:{row.id}",
                "source": "monitoring",
                "kind": kind,
                "target_id": ident,
                "observed_at": row.sampled_at,
            }
        else:
            try:
                if "mikrotik_v1" in raw:
                    from app.services.monitoring_collectors import RouterSnapshot

                    values = raw["mikrotik_v1"]
                    r = RouterSnapshot.model_validate(
                        {
                            k: v
                            for k, v in values.items()
                            if k in RouterSnapshot.model_fields
                        }
                    )
                    running = [i.running for i in r.interfaces if i.disabled is False]
                    signals = {
                        "collector": True,
                        "telemetry": True,
                        "ospf": (
                            True
                            if "full" in r.ospf_states
                            else (
                                False
                                if r.ospf_states
                                and all(v == "down" for v in r.ospf_states)
                                else None
                            )
                        ),
                        "interface": (
                            True
                            if running and all(v is True for v in running)
                            else (
                                False
                                if running and all(v is False for v in running)
                                else None
                            )
                        ),
                    }
                elif "rf_observation_v1" in raw:
                    from app.services.rf_observation import NormalizedRFTelemetry

                    r = NormalizedRFTelemetry.model_validate(raw["rf_observation_v1"])
                    if r.observed_at != row.sampled_at:
                        raise ValueError()
                    signals = {
                        "collector": True,
                        "wireless": (
                            True
                            if r.radio_status == "up"
                            else False if r.radio_status == "down" else None
                        ),
                    }
                elif "rf_radio_v2" in raw:
                    from app.services.radio_observation import RadioObservation

                    r = RadioObservation.model_validate(raw["rf_radio_v2"])
                    if r.observed_at != row.sampled_at:
                        raise ValueError()
                    signals = {
                        "wireless": (
                            True
                            if r.radio_status == "up"
                            else False if r.radio_status == "down" else None
                        ),
                        "collector": True,
                    }
                elif "rf_microwave_v1" in raw:
                    from app.services.microwave_model import MicrowaveObservation
                    from app.services.microwave_history import validate_association

                    r = MicrowaveObservation.model_validate(raw["rf_microwave_v1"])
                    link = db.get(NetworkLink, raw.get("microwave_link_id"))
                    device = db.get(Device, row.device_id)
                    validate_association(device, link, r.profile)
                    if r.observed_at != row.sampled_at:
                        raise ValueError()
                    kind = "link"
                    ident = link.id
                    statuses = [c.radio_status for c in r.carriers.values()]
                    signals = {
                        "microwave": (
                            False
                            if all(s == "down" for s in statuses)
                            else True if all(s == "up" for s in statuses) else None
                        ),
                        "interface": (
                            True
                            if r.ethernet_status == "up"
                            else False if r.ethernet_status == "down" else None
                        ),
                        "collector": True,
                    }
            except (ValueError, TypeError, KeyError, AttributeError):
                signals = {}
                kind = "device"
                ident = row.device_id
            body = {
                "event_id": f"rf-sample:{row.id}",
                "source": "rf",
                "kind": kind,
                "target_id": ident,
                "observed_at": row.sampled_at,
                "signals": signals,
            }
        if "mikrotik_v1" in raw and isinstance(raw["mikrotik_v1"], dict):
            body["uptime_seconds"] = raw["mikrotik_v1"].get("uptime_seconds")
        try:
            o = Observation.model_validate(body)
        except ValueError:
            o = Observation(
                event_id=f"rf-sample:{row.id}",
                source="rf",
                kind="device",
                target_id=row.device_id,
                observed_at=row.sampled_at,
                signals={},
            )
        ingest(db, o, now=now)
    return {"processed": len(rows), **reconcile(db, now=now)}


def tick():
    if not settings.down_detector_enabled:
        return {"enabled": False}
    with SessionLocal() as db:
        result = consume(db)
        db.commit()
        return result
