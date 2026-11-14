"""Recorded-signal state machine and conservative topology correlation; no IO probes."""

import hashlib, json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text, func
from app.core.config import settings
from app.models.entities import Device, Site, NetworkLink, Sector, Incident, AuditLog
from app.customers.impact import reachable, Target, analyze
from app.customers.models import CustomerService
from .models import OutageObservation, OutageState, OutageEpisode, OutageTimeline
from .contracts import DetectionPolicy, Observation

MODELS = {"device": Device, "site": Site, "link": NetworkLink, "sector": Sector}
AVAILABILITY = {
    "icmp",
    "snmp",
    "telemetry",
    "interface",
    "ospf",
    "wireless",
    "microwave",
    "fiber",
}


def utc(d):
    return d.astimezone(timezone.utc)


def clock():
    return datetime.now(timezone.utc)


def policy():
    return DetectionPolicy(**settings.down_detector_policy)


def lock(db):
    db.execute(text("SET LOCAL statement_timeout = '10000ms'"))
    db.execute(text("SELECT pg_advisory_xact_lock(970097)"))


def audit(db, action, actor=None, episode=None, **evidence):
    db.add(
        AuditLog(
            user_id=actor,
            action="down_detector." + action,
            entity_type="outage_episode",
            entity_id=str(episode.id) if episode else None,
            details=evidence,
        )
    )
    if episode:
        db.add(
            OutageTimeline(
                episode_id=episode.id, action=action, actor_id=actor, evidence=evidence
            )
        )


def classify(o, p):
    s = o.signals
    if s.get("collector") is False:
        return "degraded", "collector_failure", "unknown"
    bad = {k for k in AVAILABILITY if s.get(k) is False}
    good = {k for k in AVAILABILITY if s.get(k) is True}
    if s.get("commercial_power") is False:
        # Confirms commercial power loss, not necessarily loss of customer service.
        return (
            ("degraded" if good else "bad"),
            "confirmed_commercial_power_outage",
            "confirmed",
        )
    if s.get("telemetry") is True and bad & {"icmp", "snmp"}:
        return "degraded", "management_connectivity_failure", "probable"
    if bad and good:
        return "degraded", "partial_service_degradation", "probable"
    if len(bad) >= p.independent_signals:
        name = {
            "device": "device_offline",
            "site": "unknown_tower_connectivity_loss",
            "sector": "sector_outage",
            "link": "fiber_outage" if "fiber" in bad else "backhaul_outage",
        }[o.kind]
        if o.kind == "device" and "ospf" in bad and not (bad & {"icmp", "snmp"}):
            name = "network_path_unavailable"
        if o.kind == "site" and any(
            s.get(k) is False for k in ["ups", "battery", "power_supply"]
        ):
            name = "suspected_power_failure"
        return "bad", name, "probable"
    if bad:
        return "unknown", "insufficient_independent_signals", "unknown"
    if o.utilization_percent is not None and o.utilization_percent >= 90:
        return "degraded", "high_utilization", "probable"
    if (
        len(good) >= p.independent_signals
        and s.get("collector") is True
        and s.get("commercial_power") is not False
    ):
        return "good", "healthy", "observed"
    if o.utilization_percent is not None and o.utilization_percent >= 90:
        return "degraded", "high_utilization", "probable"
    return "unknown", "missing_signals", "unknown"


def ingest(db, o: Observation, *, now=None):
    now = now or clock()
    p = policy()
    lock(db)
    if not now - timedelta(seconds=p.stale_seconds) <= o.observed_at <= now:
        raise ValueError(
            "Observation is stale or in the future; no outage/recovery inferred"
        )
    target = db.get(MODELS[o.kind], o.target_id)
    if target is None:
        raise LookupError("Inventory target not found")
    key = f"{o.kind}:{o.target_id}"
    data = o.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    prior = db.scalar(
        select(OutageObservation).where(OutageObservation.event_id == o.event_id)
    )
    if prior:
        if prior.body_hash != digest:
            raise ValueError("Conflicting event identifier")
        return {"duplicate": True, "key": key}
    duplicate = db.scalar(
        select(OutageObservation.id).where(
            OutageObservation.key == key,
            OutageObservation.source == o.source,
            OutageObservation.observed_at == o.observed_at,
        )
    )
    row = OutageObservation(
        event_id=o.event_id,
        key=key,
        source=o.source,
        observed_at=o.observed_at,
        data=data,
        body_hash=digest,
    )
    db.add(row)
    if duplicate:
        original = db.get(OutageObservation, duplicate)
        prior_data = {k: v for k, v in original.data.items() if k != "event_id"}
        current_data = {k: v for k, v in data.items() if k != "event_id"}
        if prior_data != current_data:
            current = db.get(OutageState, key)
            if current and current.last_seen == o.observed_at:
                current.state = "unknown"
                current.classification = "conflicting_observations"
                current.bad_count = current.good_count = 0
                current.bad_since = current.good_since = None
                active = db.scalar(
                    select(OutageEpisode).where(
                        OutageEpisode.key == key, OutageEpisode.status == "active"
                    )
                )
                if active:
                    active.confidence = "unknown"
                audit(
                    db,
                    "conflicting_observations",
                    episode=active,
                    event_id=o.event_id,
                    key=key,
                )
        db.flush()
        return {"duplicate_time": True, "key": key}
    st = db.get(OutageState, key)
    if not st:
        site_id = (
            o.target_id
            if o.kind == "site"
            else (
                target.wisp_site_id
                if o.kind == "device"
                else target.z_site_id if o.kind == "link" else target.site_id
            )
        )
        st = OutageState(
            key=key,
            kind=o.kind,
            target_id=o.target_id,
            site_id=site_id,
            first_seen=o.observed_at,
            last_seen=o.observed_at - timedelta(microseconds=1),
            bad_count=0,
            good_count=0,
            evidence={},
        )
        db.add(st)
        db.flush()
    if o.observed_at <= st.last_seen:
        return {
            "late": True,
            "key": key,
        }  # retain original event, never advance counters
    previous = st.last_seen
    st.last_seen = o.observed_at
    state, classification, confidence = classify(o, p)
    reboot = (
        o.uptime_seconds is not None
        and st.last_uptime is not None
        and o.uptime_seconds < st.last_uptime
    )
    st.last_uptime = (
        o.uptime_seconds if o.uptime_seconds is not None else st.last_uptime
    )
    st.evidence = {
        "signals": o.signals,
        "event_id": o.event_id,
        "observed_at": o.observed_at.isoformat(),
        "reboot_detected": reboot,
        "packet_loss_percent": o.packet_loss_percent,
        "latency_ms": o.latency_ms,
        "utilization_percent": o.utilization_percent,
        "voltage": o.voltage,
    }
    if (o.observed_at - previous).total_seconds() > p.stale_seconds:
        st.bad_count = st.good_count = 0
        st.bad_since = st.good_since = None
    if st.classification != classification:
        st.bad_count = 0
        st.bad_since = None
    st.classification = classification
    ep = db.scalar(
        select(OutageEpisode).where(
            OutageEpisode.key == key, OutageEpisode.status == "active"
        )
    )
    if state == "good" and ep:
        failed = {k for k, v in ep.evidence.get("signals", {}).items() if v is False}
        if any(o.signals.get(k) is not True for k in failed):
            state = "unknown"
            classification = "incomplete_recovery_evidence"
            st.classification = classification
    if state in {"bad", "degraded"}:
        st.good_count = 0
        st.good_since = None
        st.bad_count += 1
        st.bad_since = st.bad_since or o.observed_at
        st.state = "suspect"
        if (
            st.bad_count >= p.confirmation_samples
            and (o.observed_at - st.bad_since).total_seconds() >= p.confirmation_seconds
        ):
            st.state = "down" if state == "bad" else "degraded"
            if not ep:
                ep = OutageEpisode(
                    key=key,
                    classification=classification,
                    confidence=confidence,
                    started_at=st.bad_since,
                    confirmed_at=o.observed_at,
                    evidence=st.evidence,
                )
                db.add(ep)
                db.flush()
                audit(
                    db,
                    "first_detection",
                    episode=ep,
                    observed_at=st.bad_since.isoformat(),
                )
                audit(
                    db,
                    "confirmed",
                    episode=ep,
                    classification=classification,
                    event_id=o.event_id,
                )
            elif ep.classification != classification and not ep.override_reason:
                ep.classification = classification
                ep.confidence = confidence
                ep.evidence = st.evidence
                audit(
                    db,
                    "cause_changed",
                    episode=ep,
                    classification=classification,
                    event_id=o.event_id,
                )
    elif state == "good":
        st.bad_count = 0
        st.bad_since = None
        st.good_count += 1
        st.good_since = st.good_since or o.observed_at
        st.state = "recovering" if ep else "up"
        if (
            ep
            and st.good_count >= p.recovery_samples
            and (o.observed_at - st.good_since).total_seconds() >= p.recovery_seconds
        ):
            st.state = "up"
            ep.status = "resolved"
            ep.resolved_at = o.observed_at
            audit(
                db,
                "stable_recovery",
                episode=ep,
                samples=st.good_count,
                event_id=o.event_id,
            )
    else:
        st.state = "unknown"
        st.bad_count = st.good_count = 0
        st.bad_since = st.good_since = None
        if ep:
            audit(
                db,
                "evidence_unavailable",
                episode=ep,
                classification=classification,
                event_id=o.event_id,
            )
    if reboot:
        audit(db, "reboot_observed", episode=ep, key=key, event_id=o.event_id)
    db.flush()
    return {
        "key": key,
        "state": st.state,
        "classification": classification,
        "episode_id": ep.id if ep else None,
    }


def reconcile(db, *, now=None):
    now = now or clock()
    p = policy()
    lock(db)
    states = {s.key: s for s in db.scalars(select(OutageState).limit(5001))}
    if len(states) > 5000:
        raise ValueError("Detector scope exceeds validated 5000-component bound")
    episodes = list(
        db.scalars(
            select(OutageEpisode)
            .where(OutageEpisode.status == "active")
            .order_by(OutageEpisode.id)
        )
    )
    links = list(db.scalars(select(NetworkLink)))
    sites = list(db.scalars(select(Site)))
    devices = {d.id: d for d in db.scalars(select(Device))}
    roots = {s.id for s in sites if s.site_type == "core-pop"}
    baseline = reachable(roots, links)
    by_key = {e.key: e for e in episodes}
    failed_links = {
        states[e.key].target_id
        for e in episodes
        if states[e.key].kind == "link"
        and states[e.key].state == "down"
        and now - states[e.key].last_seen <= timedelta(seconds=p.stale_seconds)
    }
    for ep in episodes:
        st = states[ep.key]
        parent = None
        if ep.parent_id:
            prior_parent = db.get(OutageEpisode, ep.parent_id)
            if prior_parent and (
                prior_parent.status == "resolved"
                or states[prior_parent.key].state not in {"down", "degraded"}
                or now - states[prior_parent.key].last_seen
                > timedelta(seconds=p.stale_seconds)
            ):
                continue  # missing upstream evidence must not create independent downstream campaigns
        if st.kind in {"device", "sector", "site"} and st.site_id in baseline:
            candidates = []
            for root in episodes:
                rs = states[root.key]
                if (
                    rs.kind == "link"
                    and rs.target_id in failed_links
                    and abs((ep.started_at - root.started_at).total_seconds())
                    <= p.correlation_seconds
                ):
                    if st.site_id not in reachable(
                        roots, links, excluded_links={rs.target_id}
                    ):
                        candidates.append(root)
                elif (
                    rs.kind == "site"
                    and st.kind in {"device", "sector"}
                    and st.site_id == rs.target_id
                    and rs.state == "down"
                    and abs((ep.started_at - root.started_at).total_seconds())
                    <= p.correlation_seconds
                ):
                    candidates.append(root)
            # Ambiguous multiple possible causes are retained as independent uncertain episodes.
            link_candidates = [c for c in candidates if states[c.key].kind == "link"]
            if len(link_candidates) == 1:
                parent = link_candidates[0]
            elif len(candidates) == 1:
                parent = candidates[0]
        if parent is None and st.kind == "device":
            d = devices.get(st.target_id)
            seen = {st.target_id}
            ancestor = d.parent_device_id if d else None
            while ancestor in devices and ancestor not in seen:
                seen.add(ancestor)
                candidate = by_key.get(f"device:{ancestor}")
                if (
                    candidate
                    and states[candidate.key].state == "down"
                    and now - states[candidate.key].last_seen
                    <= timedelta(seconds=p.stale_seconds)
                    and abs((ep.started_at - candidate.started_at).total_seconds())
                    <= p.correlation_seconds
                ):
                    # Cyclic dependency metadata must not suppress every member.
                    probe = devices[ancestor].parent_device_id
                    chain = set(seen)
                    while probe in devices and probe not in chain:
                        chain.add(probe)
                        probe = devices[probe].parent_device_id
                    if probe not in chain:
                        parent = candidate
                    break
                ancestor = devices[ancestor].parent_device_id
        if ep.parent_id != (parent.id if parent else None):
            ep.parent_id = parent.id if parent else None
            audit(
                db,
                "correlation_changed",
                episode=ep,
                parent_episode_id=ep.parent_id,
                confidence="probable" if parent else "unknown",
            )
    # Create primary incidents first. Symptoms link to the same existing incident, not a new campaign.
    for ep in sorted(episodes, key=lambda e: e.parent_id is not None):
        st = states[ep.key]
        if ep.parent_id:
            par = db.get(OutageEpisode, ep.parent_id)
            if not ep.incident_id:
                ep.incident_id = par.incident_id
            continue
        if not ep.incident_id:
            inc = Incident(
                title=f"Down Detector {ep.classification} ({ep.key})",
                severity=(
                    "warning"
                    if st.state == "degraded"
                    or ep.classification == "confirmed_commercial_power_outage"
                    and st.evidence.get("signals", {}).get("telemetry") is True
                    else "critical"
                ),
                started_at=ep.started_at,
                summary="Evidence-based correlation; probable cause is not field-confirmed",
                root_cause=ep.classification,
                **{f"{st.kind}_id": st.target_id},
            )
            db.add(inc)
            db.flush()
            ep.incident_id = inc.id
            audit(db, "incident_created", episode=ep, incident_id=inc.id)
    # Do not close a root while any known correlated symptom is still unrecovered.
    for ep in db.scalars(
        select(OutageEpisode)
        .join(Incident, Incident.id == OutageEpisode.incident_id)
        .where(
            Incident.status.not_in(["closed", "resolved"]),
            OutageEpisode.status == "resolved",
            OutageEpisode.incident_id.is_not(None),
            OutageEpisode.parent_id.is_(None),
        )
    ):
        inc = db.get(Incident, ep.incident_id)
        children = [e for e in episodes if e.parent_id == ep.id]
        if inc and not children and inc.status not in {"closed", "resolved"}:
            inc.status = "resolved"
            downstream_at = db.scalar(
                select(func.max(OutageEpisode.resolved_at)).where(
                    OutageEpisode.parent_id == ep.id
                )
            )
            inc.resolved_at = (
                max(ep.resolved_at, downstream_at) if downstream_at else ep.resolved_at
            )
            inc.revision += 1
            audit(db, "incident_recovered", episode=ep, incident_id=inc.id)
    db.flush()
    return {
        "active_episodes": len(episodes),
        "primary_incidents": sum(not e.parent_id for e in episodes),
        "symptoms": sum(bool(e.parent_id) for e in episodes),
        "external_calls": 0,
    }


def impact(db, ep):
    st = db.get(OutageState, ep.key)
    services = list(
        db.scalars(select(CustomerService).order_by(CustomerService.id).limit(20001))
    )
    if len(services) > 20000:
        raise ValueError("Impact scope exceeds validated service bound")
    results = analyze(db, Target(**{f"{st.kind}_id": st.target_id}), services)
    counts = {
        k: sum(r["classification"] == k for r in results)
        for k in ["confirmed", "probable", "unknown", "not_affected"]
    }
    types = {s.id: s.service_type for s in services}
    affected_ids = {
        r["service_id"]
        for r in results
        if r["classification"] in {"confirmed", "probable"}
    }
    affected = [s for s in services if s.id in affected_ids]
    return {
        "service_counts": counts,
        "affected_site_ids": sorted({s.site_id for s in affected if s.site_id}),
        "affected_sector_ids": sorted({s.sector_id for s in affected if s.sector_id}),
        "affected_fiber_segment_ids": sorted(
            {s.fiber_link_id for s in affected if s.fiber_link_id}
        ),
        "services_with_remaining_recorded_path": sum(
            r["reason"] == "remaining_recorded_path" for r in results
        ),
        "potential_customer_count": len(
            {
                r["customer_id"]
                for r in results
                if r["classification"] in {"confirmed", "probable"}
            }
        ),
        "services_by_technology": {
            k: sum(
                r["classification"] in {"confirmed", "probable"}
                and types.get(r["service_id"]) == k
                for r in results
            )
            for k in {"wireless", "fiber", "hybrid"}
        },
        "evidence": "Recorded topology/mapping dependencies; not end-user service verification",
    }
