"""Bounded local history, counter reset handling and read-only RF diagnostics."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text
from app.models.entities import Device, TelemetrySample
from app.services.radio_observation import RadioObservation, RadioCounters
from app.services.rf_freshness import evaluate_rf_freshness
from app.services.rf_metrics import normalize_rf_metrics


from app.services.radio_policy import RadioThresholds


def stage_radio(db, device, observation):
    if not device.id or not isinstance(observation, RadioObservation): raise ValueError('Saved device and validated RF observation required')
    vendor = (device.vendor or '').casefold()
    aliases = {'ubiquiti': {'ubiquiti', 'ubiquiti networks', 'ubnt'}, 'cambium': {'cambium', 'cambium networks'}}
    if vendor not in aliases[observation.vendor]: raise ValueError('Inventory vendor mismatch')
    if not observation.measurements.model_dump(exclude_none=True) and not observation.counters.model_dump(exclude_none=True) and all(getattr(observation, k) is None for k in ('radio_status', 'gps_sync', 'modulation')):
        return None
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': 861000 + device.id})
    existing = db.scalar(select(TelemetrySample).where(TelemetrySample.device_id == device.id,
        TelemetrySample.sampled_at == observation.observed_at,
        TelemetrySample.raw_data['rf_radio_v2']['profile'].as_string() == observation.profile).limit(1))
    payload = observation.model_dump(mode='json')
    if existing:
        comparable = lambda p: {k: v for k, v in p.items() if k != 'received_at'}
        if comparable(existing.raw_data['rf_radio_v2']) != comparable(payload): raise ValueError('Conflicting RF sample')
        return existing
    projection = normalize_rf_metrics(observation.measurements.model_dump(exclude_none=True))
    raw = {'rf_radio_v2': payload}
    if projection: raw['rf_metrics_v1'] = projection
    row = TelemetrySample(device_id=device.id, sampled_at=observation.observed_at,
        signal_dbm=projection.get('rssi_dbm'), temperature_c=projection.get('temperature_c'),
        tx_mbps=observation.measurements.tx_throughput_mbps, rx_mbps=observation.measurements.rx_throughput_mbps, raw_data=raw)
    db.add(row); db.flush()
    return row


def counter_delta(previous, current):
    if previous is None: return {'state': 'baseline', 'deltas': {}}
    if previous.profile != current.profile or previous.mapping_digest != current.mapping_digest: return {'state': 'mapping_changed', 'deltas': {}}
    elapsed = (current.observed_at - previous.observed_at).total_seconds()
    if elapsed <= 0: return {'state': 'out_of_order', 'deltas': {}}
    old, new = previous.measurements.uptime_seconds, current.measurements.uptime_seconds
    if old is None or new is None: return {'state': 'uptime_unknown', 'deltas': {}}
    if new < old: return {'state': 'device_restart', 'deltas': {}}
    deltas, resets = {}, []
    for field in RadioCounters.model_fields:
        a, b = getattr(previous.counters, field), getattr(current.counters, field)
        if a is None or b is None: continue
        if b < a: resets.append(field)  # Counter wrap and reset are indistinguishable; never invent a delta.
        else: deltas[field] = b - a
    return {'state': 'counter_reset' if resets else 'continuous', 'deltas': deltas, 'reset_fields': resets, 'elapsed_seconds': elapsed}


def analyze_radio(observations, *, now=None, thresholds=None):
    cfg = thresholds or RadioThresholds()
    now = now or datetime.now(timezone.utc)
    checks = {'weak_rssi': ('rssi_dbm', lambda v: v < cfg.minimum_rssi_dbm),
              'low_snr': ('snr_db', lambda v: v < cfg.minimum_snr_db),
              'high_noise': ('noise_floor_dbm', lambda v: v > cfg.maximum_noise_dbm),
              'low_capacity': ('link_capacity_mbps', lambda v: v < cfg.minimum_capacity_mbps),
              'poor_quality': ('ccq_percent', lambda v: v < cfg.minimum_ccq_percent)}
    streaks = {k: 0 for k in (*checks, 'disconnected', 'signal_degradation', 'increasing_noise', 'capacity_degradation')}
    previous, baseline, disconnect_events = None, None, 0
    segments = []
    for observation in observations:
        gap = (observation.observed_at - previous.observed_at).total_seconds() if previous else None
        delta = counter_delta(previous, observation)
        if gap is not None and gap <= 0: raise ValueError('RF history must be strictly chronological')
        boundary = previous is None or gap > cfg.max_gap_seconds or delta['state'] in {'device_restart', 'mapping_changed'}
        if boundary:
            streaks = dict.fromkeys(streaks, 0); baseline = observation; disconnect_events = 0; segments = []
        fresh = evaluate_rf_freshness(observation.observed_at, now=now, stale_after_seconds=cfg.stale_after_seconds)['status'] == 'fresh'
        for key, (field, test) in checks.items():
            value = getattr(observation.measurements, field)
            streaks[key] = streaks[key] + 1 if fresh and value is not None and test(value) else 0
        streaks['disconnected'] = streaks['disconnected'] + 1 if fresh and observation.radio_status == 'down' else 0
        for key, field, direction, change in [('signal_degradation', 'rssi_dbm', -1, cfg.signal_drop_db),
                                               ('increasing_noise', 'noise_floor_dbm', 1, cfg.noise_rise_db),
                                               ('capacity_degradation', 'link_capacity_mbps', -1, None)]:
            value, base = getattr(observation.measurements, field), getattr(baseline.measurements, field)
            limit = base * cfg.capacity_drop_percent / 100 if field == 'link_capacity_mbps' and base is not None and base > 0 else change
            failing = fresh and value is not None and base is not None and limit is not None and direction * (value - base) >= limit
            streaks[key] = streaks[key] + 1 if failing else 0
        if not fresh or observation.radio_status is None or observation.radio_status == 'unknown':
            disconnect_events = 0; segments = []
        else:
            segments.append(observation.radio_status)
            segments = segments[-(2 * cfg.consecutive_samples + 1):]
            # Only observed up->down transitions count; counter resets don't create events.
            disconnect_events = sum(a == 'up' and b == 'down' for a, b in zip(segments, segments[1:]))
        previous = observation
    reasons = sorted(k for k, count in streaks.items() if count >= cfg.consecutive_samples)
    if disconnect_events >= cfg.consecutive_samples: reasons.append('intermittent_link')
    latest = observations[-1] if observations else None
    freshness = evaluate_rf_freshness(latest.observed_at if latest else None, now=now, stale_after_seconds=cfg.stale_after_seconds)
    if freshness['status'] != 'fresh': reasons = []
    # Healthy requires all monitored signal/quality metrics and confirmed link state.
    required = ('rssi_dbm', 'snr_db', 'noise_floor_dbm', 'link_capacity_mbps', 'ccq_percent')
    tail = observations[-cfg.consecutive_samples:]
    healthy = len(tail) == cfg.consecutive_samples and all(
        o.radio_status == 'up' and all(getattr(o.measurements, f) is not None for f in required) and
        evaluate_rf_freshness(o.observed_at, now=now, stale_after_seconds=cfg.stale_after_seconds)['status'] == 'fresh' for o in tail
    ) and not reasons and all(v == 0 for v in streaks.values()) and all(
        0 < (b.observed_at-a.observed_at).total_seconds() <= cfg.max_gap_seconds and counter_delta(a,b)['state'] not in {'device_restart','mapping_changed'} for a,b in zip(tail,tail[1:]))
    return {'state': 'degraded' if reasons else 'healthy' if healthy else 'unknown', 'reasons': reasons,
            'freshness': freshness, 'consecutive_counts': streaks, 'disconnect_transitions': disconnect_events,
            'counter_status': counter_delta(observations[-2] if len(observations)>1 else None, latest) if latest else None,
            'thresholds': cfg.model_dump(), 'execution_enabled': False}


def radio_history(db, device_id, *, now=None, limit=288, thresholds=None):
    if not 1 <= limit <= 1000: raise ValueError('Invalid RF history limit')
    if db.get(Device, device_id) is None: raise LookupError('device_not_found')
    now = now or datetime.now(timezone.utc)
    rows = list(db.scalars(select(TelemetrySample).where(TelemetrySample.device_id == device_id,
        TelemetrySample.sampled_at >= now-timedelta(days=7), TelemetrySample.raw_data['rf_radio_v2'].is_not(None))
        .order_by(TelemetrySample.sampled_at.desc(), TelemetrySample.id.desc()).limit(limit)))
    unique, invalid = {}, 0
    for row in reversed(rows):
        try:
            observation = RadioObservation.model_validate(row.raw_data['rf_radio_v2'])
            when = row.sampled_at.replace(tzinfo=timezone.utc) if row.sampled_at.tzinfo is None else row.sampled_at
            if observation.observed_at != when: raise ValueError()
            unique[observation.observed_at] = observation
        except (ValueError, TypeError, KeyError): invalid += 1
    observations = [unique[t] for t in sorted(unique)]
    report = analyze_radio(observations, now=now, thresholds=thresholds)
    if invalid: report.update(state='unknown', reasons=[])
    from app.models.entities import AuditLog
    from app.integrations.read_only import ERROR_CODES
    attempt = db.scalar(select(AuditLog).where(AuditLog.action == 'rf.read_only_poll', AuditLog.entity_id == str(device_id))
                        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1))
    result = (attempt.details or {}).get('result') if attempt else None
    last_attempt = {'at': attempt.created_at.isoformat(), 'result': result if result in ERROR_CODES | {'success'} else 'sync_failed'} if attempt else None
    return {'device_id': device_id, 'read_only': True, 'invalid_samples': invalid, 'last_attempt': last_attempt,
            'samples': [o.model_dump(mode='json') for o in observations], 'diagnostics': report}
