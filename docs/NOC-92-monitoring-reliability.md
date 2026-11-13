# NOC-92 monitoring reliability

Builds on draft PR #95 / `bf642bdb467b696c9a3778d51bf4ce816d0f82f7` (CI #462 passed). Preserve the NOC-83–91 stack; no upstream pull request is merged by this work.

## Durable scheduling and safety

`RF_SCHEDULER_ENABLED=false` by default. PostgreSQL owns canonical `(kind, device)` jobs, scheduled slots, fenced UUID attempts, leases and unique `(job, slot)` delivery receipts. Workers claim with `FOR UPDATE SKIP LOCKED`; a persistent bounded executor claims only available capacity. Separate sessions isolate collectors. Expired leases are reclaimed and audited. Advisory device locks coordinate manual and scheduled RF polls; MikroTik retains its global read-only collection lock. Collection exceptions become static error codes, never source exception strings.

Each accepted typed observation is atomically written and fsynced to a private mode-0700 outbox before database staging. Sample, receipt, alert evidence and schedule advancement commit together. Delete the outbox record only after commit; reconcile an uncertain commit by finding its exact committed receipt/sample. Crash recovery reuses the same slot and payload, rather than collecting a replacement. Corrupt entries remain for operator review and do not block other job reconciliation. The job owning a corrupt entry backs off with a data-quality error.

This provides replay after the durable handoff, not exactly-once network reads: a crash before fsync may require another read, and filesystem loss can lose uncommitted observations. Back up PostgreSQL and the private outbox together; retain outbox files across image upgrades/restarts. Never delete a pending delivery to silence an alarm. Do not downgrade schema 0011 while active jobs or undelivered outbox records remain. Disable scheduling, stop workers, archive the outbox and database first. The additive downgrade removes NOC-92 service-state tables only; use the existing [release recovery guide](NOC-84-installation-and-recovery.md) for verified backup/restore procedures.

Redis is a health signal, not the durable queue. Its loss cannot discard jobs or abort a PostgreSQL delivery. Workers publish per-process heartbeats only following a successful database probe, and expose Redis degradation separately. Database failure retains leases/outbox; reconnect, reclaim and replay when service returns. Worker starts, database/Redis recovery, lease reclamation, replay, stable collector recovery and alert transitions are audited. No recovery procedure reboots equipment or changes configurations.

## Configuration

Use `.env.example` defaults. `RF_SCHEDULER_CONCURRENCY` is 1–16 (default 8); batch size is 1–100 (default 32). Intervals are 15–86400 seconds. Leases must exceed collector deadlines by at least five seconds. MikroTik must allow its bounded 30-second GET budget (job timeout at least 40 seconds). Live retries never run sooner than the configured interval; exponential backoff is capped. SNMP additionally enforces its approved mapping's minimum interval, endpoint, vendor/link association and exact IP allowlist. All existing TLS/SNMPv3 and administrator-approval gates remain mandatory.

For a future separately approved read-only pilot, an administrator can provide `RF_SCHEDULER_MANIFEST_FILE`, a private owned mode-0600 JSON file with explicit entries, for example:

```json
[{"kind":"radio","device_id":123,"config_file":"/run/secrets/approved-radio.json","interval_seconds":60,"timeout_seconds":10,"lease_seconds":120}]
```

The existing radio/microwave configuration supplies validated GET-only OID mappings, endpoint allowlists and a separate private credential file. No endpoints or credentials are supplied by the dashboard. Never use example device IDs against actual inventory without review. Offline mode rejects live enrollment/transport. Isolated synthetic staging additionally requires `APP_ENV=staging`, `OFFLINE_MODE=true`, `SYNTHETIC_MONITORING_ENABLED=true`; only `Device.source=synthetic` jobs are accepted. The administrator-only synthetic enrollment API cannot activate live collectors.

## Alert accuracy and correlation

`RF_RELIABILITY_POLICY` is validated JSON with configurable enter/clear thresholds, consecutive counts (default 3 each), stale age/max gap (900 seconds), and exponential-backoff bounds (5–300 seconds). Defaults: signal enter below -75 dBm / clear at -72; SNR below 20 / clear 23 dB; capacity below 100 / clear 130 Mbps; utilization enter 85 / clear 75%; temperature above 80 / clear 75 C. These are configurable examples, not universal vendor engineering limits. Review them per pilot link before activation.

Rules cover signal, SNR, rising noise, modulation fallback, reduced capacity, utilization, overheating, wireless outages, individual microwave carrier failure, Ethernet failures and collection failures. Noise/modulation changes require a disjoint preceding four-sample observed reference in a bounded rolling history (100 source rows / 24 hours), never a fabricated engineering baseline. Context/channel changes, gaps and observed uptime resets break continuity. Missing/invalid/stale/future evidence resets confirmation and becomes unknown; it neither opens an RF outage nor clears a known condition. Recovery requires distinct consecutive usable samples; replayed sample identities cannot count as fresh collection successes. One durable alert row per device/series/rule tracks recurrence episodes and transitions, preventing an alert storm from creating duplicate incidents.

A single failed carrier is a warning, not an aggregate full-link outage. Full microwave outage requires all observed carriers down. Ethernet and collection evidence remain separate from RF evidence. Inventory parent chains identify *probable*, unconfirmed upstream RF failures. Alternate incoming inventory links retain `multiple_paths_uncertain`; they do not prove successful failover. Operator-opened investigations reuse active shared incidents, including a probable parent incident where justified. No automatic incident closure, customer-count inference, remediation or definitive root cause is performed.

## Operator view and troubleshooting

RF Operations → **monitoring health** provides manually refreshed, paginated jobs, next-due backlog/expired leases, stale/missing jobs, last successful collection, sanitized error codes, recent polling success rate, collection timings, source-age processing delay, worker freshness, Redis state, outbox counts and recovery events. Unknown means evidence is insufficient. The source-age delay is not a scheduler SLA. At most 100 alerts are displayed and topology correlation is withheld when bounded inventory coverage is incomplete. Open an investigation through the existing technician workflow; resolving an investigation does not pretend to recover telemetry.

- No jobs: scheduler is disabled or no explicit approved jobs are configured.
- Growing due backlog: check worker heartbeat, database access, leases and configured concurrency/deadlines.
- `spool_unavailable`: check owner/mode/disk capacity and retained delivery validation; preserve files for recovery.
- `stale_telemetry` / `duplicate_telemetry`: inspect source timestamps/capability; do not substitute receipt time for observation time.
- `collection_failure`: inspect static error and approved connection configuration; missing data is not an RF outage.
- Redis unavailable: PostgreSQL polling can continue; restore the isolated Redis service and inspect recovery audit.
- Database unavailable: restore connectivity; verify retained receipts/outbox and reclaimed lease audit before judging gaps.

## Reproduce validation

On a disposable migrated PostgreSQL `nwo_noc`, run the complete backend suite with `RF_INTEGRATION_TEST_DATABASE_URL` set. New cases are in `tests/test_monitoring_reliability.py`. Run `python -m app.monitoring_benchmark` with the four synthetic staging/scheduler flags above. It creates a uniquely owned temporary schema, seeds 40 sites and 250/500/1000 five-vendor devices, performs three degraded plus three recovery rounds through the actual concurrent PostgreSQL scheduler, asserts sample/receipt uniqueness and stable recovery, then removes only its own schema. Scheduling time is virtualized in 30-second steps; reported wall times/throughput and transaction collection/alert-processing durations are measured. Synthetic 2ms collectors do not establish real firmware compatibility or production capacity.

The Ubuntu 24.04 isolated Compose rehearsal uses the existing release installation workflow. After prior browser cases, enable only the synthetic scheduler in its generated environment, recreate backend/worker, and run `NOC92_RECOVERY=true python3 tests/release/test_monitoring_recovery.py`. It verifies continued polling during an actual Redis stop, recovery after an actual PostgreSQL stop/start, and a new worker process resuming the same job. Unit/integration tests separately interrupt between fsync and commit, and inject database errors to exercise replay. These are distinct checks, not a claim that every crash timing or disk failure is covered. The final Playwright case validates the monitoring health view with the actual recovered synthetic stack.

Before a controlled pilot: upstream stack review/merge sequencing, per-firmware validated MIB/REST capabilities, approved read-only credentials/endpoint allowlists, operational threshold tuning, disk/outbox retention and off-host backup monitoring, longer real-duration soak and failure/load tests, and explicit operator approval remain required. No production access or firmware compatibility is established by these synthetic checks.
