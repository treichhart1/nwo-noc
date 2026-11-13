# NOC-85 operational readiness and monitoring validation

## Baseline and scope

Direct base: NOC-84 `a0161602cd013ce76048b799631dd5e2ba746310`, draft #88;
NOC-83 #87 remains upstream. GitHub CI #428 / run 43359572682 was confirmed
successful before this work. All source dependencies are preserved; no upstream
PRs are merged, closed, or rewritten. Follow the NOC-84 guide for isolation,
configuration, installation and recovery. This work uses synthetic inventory,
local disposable PostgreSQL/Redis and offline Docker application services only.
No production credentials, equipment, databases, or integration endpoints are used.

The request ends at “Record measurable”; this report records query counts,
processing timings, controlled detection/recovery intervals and worker restart
latency. These are measured simulation results, not physical network acceptance.

## Reproducible WISP fixture

`app.services.synthetic_wisp.seed_network` defaults to 400 devices across 40
locations: 4 POPs and 36 towers, 39 backhaul records, 120 access sectors at the
400-device size, routers, switches, access points and subscriber radios.
Vendors are MikroTik, Ubiquiti, Cambium, Aviat and Racom. Device targets are unique
addresses in RFC 2544's 198.18.0.0/15 benchmark range; none are probed by test
callbacks. Parent IDs define directed dependency relationships. POP interconnect
links are not automatically treated as exclusive dependencies; redundant paths
must not be guessed from a physical link alone. All telemetry is generated.

For a fresh, migrated, explicitly selected **loopback disposable** database:

```bash
cd backend
# Set SECRET_KEY/ADMIN_PASSWORD to generated test values and DATABASE_URL to
# the disposable database, never a production environment file.
# RF_INTEGRATION_TEST_DATABASE_URL must explicitly select that test database.
APP_ENV=staging OFFLINE_MODE=true PYTHONPATH=. python scripts/seed_synthetic_wisp.py --devices 400
```

Seeding refuses an inventory that already contains devices or sites. For live
worker-process rehearsal, SYNTHETIC_MONITORING_ENABLED=true is accepted only in
offline staging. In that mode benchmark-range device targets return a deterministic
1 ms synthetic success without opening a socket. Other targets retain the offline
Redis-only restriction. The mode defaults to false and cannot run in production.
Failure scenarios use injected deterministic probes; they never execute commands
on devices or call vendor APIs. Do not interpret simulated success as real
reachability. Keep the fixture and this mode confined to disposable test projects.

## Monitoring, alert and incident behavior

- Worker batches use up to 16 concurrent probe threads. Threads share no SQLAlchemy
  Session. Each batch commits state/events atomically; PostgreSQL transaction
  advisory locks serialize overlapping workers and manual checks. Overlapping
  worker batches skip rather than duplicate events.
- Monitoring uses two consecutive failures before declaring down. Initial success
  is not recovery; recovery requires a previous confirmed down state. Repeated
  failures preserve the original outage timestamp. Real probe timestamps are
  recorded when results are consumed, not backdated to the start of the batch.
- Timeouts are bounded to 1–10 seconds. Missing targets fail closed. Probe errors
  and interrupted batches do not publish partial state transitions. Worker
  heartbeats renew during successful batch progress and are withheld after DB
  failure; Redis/DB restart recovery is exercised by runtime acceptance.
- `/api/operations/readiness` reports fresh check counts, stale/missing checks,
  last successful check, tower/POP and backhaul states, sustained RF degradation,
  missing/stale RF telemetry, active incidents and correlated root outages.
  It requires operator/admin privileges and exposes no raw payloads or secrets.
- `/api/alerts` uses fresh monitoring correlation for monitored devices. Root
  outages suppress downstream notifications. Missing/future/stale data does not
  prove an outage or recovery. A topology cycle cannot suppress every outage.
  Imported offline observations for unmonitored devices remain warning-level
  inventory information, not confirmed critical reachability alerts. Authorization
  warnings remain visible during maintenance.
- Active scheduled/active maintenance uses starts_at <= now < ends_at. Site and
  global windows suppress notifications; a directed link window affects its Z
  endpoint/descendants and never hides a shared upstream POP failure. Raw outage
  counts remain visible. Suppression does not fabricate recovery or close incidents.
- RF warnings require three consecutive fresh samples for RSSI/SNR/CCQ degradation.
  Capacity warnings require three PHY rates below 50% of configured Mbps capacity;
  this is a capacity proxy, not a throughput test. Invalid/missing readings break
  the streak. RF telemetry is independent from TCP reachability.
- `incident_simulation.reconcile` is an explicit, offline-staging, synthetic-only
  rehearsal using real Incident/AuditLog tables. It is not wired to the normal
  worker or an HTTP mutation endpoint. Stable correlation keys and a transaction
  lock deduplicate active incidents; confirmed recovery resolves them. Stale,
  suppressed, missing or still-failing readings do not close incidents. Operator
  create/acknowledge/resolve/reopen actions are tested with audit records; invalid
  lifecycle statuses are rejected. No automatic production incident policy or
  notification delivery is enabled by this work.
- The dashboard labels legacy status as imported inventory and presents fresh
  operational monitoring separately. It lists affected sites/backhauls, root
  alerts and operator actions. Failed readiness requests clear prior displayed
  readiness counts rather than presenting them as current.

## Commands and automated gates

```bash
cd backend
python -m pytest -q -rs
# Supply the explicit disposable PostgreSQL integration URL to run all DB tests.
APP_ENV=staging OFFLINE_MODE=true PYTHONPATH=. python scripts/benchmark_operational_readiness.py
# Separate disposable loopback Redis; creates and retains new noc85_worker_* DBs.
APP_ENV=staging OFFLINE_MODE=true NOC85_TEST_REDIS_URL=redis://127.0.0.1:6379/0 \
  PYTHONPATH=. python scripts/validate_worker_scale.py
```

The operational PostgreSQL test module covers inventory, retries/timeouts, false
recovery prevention, outage timestamp stability, root/child correlation, site/link
maintenance, stale/future telemetry, RF/capacity invalid-sample handling, topology
cycles, concurrent workers, synthetic-mode guards, lifecycle/audit behavior and
constant query budgets at 250/500/1000 devices. CI runs it in the PostgreSQL gate,
then runs both scale scripts. The existing isolated runtime suite additionally
restarts PostgreSQL and checks API/worker recovery. Browser acceptance verifies
that the operational section appears alongside the existing monitoring dashboard.
RF self-healing remains simulation-only and has no execution route.

## Measured cloud results

A shared Debian cloud execution host, Python 3.12, disposable PostgreSQL 16 and
Redis 7 were used. Snapshot measurements use five repetitions; the “p95” output
is the nearest-rank maximum of five, not a statistically strong tail estimate.
Concurrent image builds add noise. Do not use these numbers as a production SLA.

| Devices | Snapshot median / max (ms) | Snapshot SELECTs | Two poll batches (ms) | Poll SELECTs (both batches) | 25/50/100 independent incidents (ms) |
| --- | --- | --- | --- | --- | --- |
| 250 | 54.78 / 123.20 | 6 | 337.22 | 4 | 114.09 |
| 500 | 85.50 / 145.49 | 6 | 412.00 | 4 | 207.28 |
| 1000 | 130.57 / 202.51 | 6 | 1198.38 | 4 | 271.37 |

Each all-down simulation produced four POP-root incidents, zero duplicates on
repeat reconciliation, and four confirmed resolutions. Independent failure cases
produced 25, 50 and 100 incidents respectively. Probes were injected 1 ms delays
and performed **zero device network calls**. Detection was 30 seconds and recovery
15 seconds on the controlled simulation clock (two-failure/one-success policy).
Wall processing time and scheduler intervals must be added for actual deployment.

| Actual worker process | First completed poll (ms) | Completed poll after process restart (ms) |
| --- | --- | --- |
| 250 devices | 1023.11 | 15794.93 |
| 500 devices | 1015.57 | 15818.11 |
| 1000 devices | 1531.40 | 16249.76 |

These process tests used real database persistence, Redis heartbeat and six worker
starts, with deterministic socket-free synthetic success. Restart deliberately
respects the persisted 15-second due interval. These numbers do not measure
MikroTik/UISP or RF hardware responsiveness.

## Remaining release gates and operational limits

- Review the stacked PRs and rerun all gates at the approved consolidated SHA;
  this branch is not an independent production release or deployment approval.
- Synthetic tests validate policy and application flow, not RF physics, vendor
  adapter accuracy, power redundancy or actual notification delivery. No live
  hardware/production acceptance is claimed. Automatic incident reconciliation
  remains an explicit rehearsal, not an enabled production worker feature.
- With 16 probe threads and 10-second timeouts, a 1,000-device timeout storm can
  take about 630 seconds per batch before state commits. Real outage latency may
  therefore substantially exceed the controlled-clock figures. Priority queues,
  worker sharding and deployment-specific timeout/poll budgets require separate
  review before asserting a production monitoring SLA. Manual checks serialize
  behind the batch. No latency SLA is claimed by the tests.
- Correlation follows configured parents, not inferred root cause. Redundant paths,
  incomplete topology, cyclic configuration, isolated devices and missing RF data
  require operator review. A tower is down only when all enabled monitored members
  have fresh confirmed failures; partial failures are degraded, missing data unknown.
- NOC-84's bare Ubuntu VM boot/systemd/reboot/firewall rehearsal, immutable image
  archival, JWT revocation and separately approved production egress policy remain.
- Exact final test counts, CI run and candidate SHA are recorded in the draft PR.
  Existing dependency deprecations are not treated as passed physical acceptance.

## Isolated 400-device operator rehearsal

The CI installation job also creates a separate `noc84-wisp-test` project with
private generated credentials and SYNTHETIC_MONITORING_ENABLED=true. It stops
that project's worker before changing the controlled scenario, then runs:

```bash
# Through scripts/release/manage.sh STATE exec -T backend:
python -m app.wisp_rehearsal seed
python -m app.wisp_rehearsal outage
# Point the loopback access bridge at this project's state and set NOC_PROJECT.
# With NOC_ACCEPTANCE_STATE and NOC85_WISP_BROWSER=true:
# (cd frontend && npx playwright test operational.spec.ts)
python -m app.wisp_rehearsal recover
```

The scenario seeds 400 devices and creates 11 confirmed device failures, one
root outage, 10 correlated downstream failures, one tower outage, one sustained
RF warning, one capacity warning and three simulated incidents. The dedicated
browser check verifies the actual dashboard and incident pages for that fixture.
Recovery must restore all 400 fresh-up devices and resolve all three incidents.
The command refuses non-synthetic inventory and requires explicit offline staging
plus synthetic monitoring. Never run it against a production environment. Regular
10-test browser acceptance uses a different, small fixture; a skipped dedicated
WISP browser test is not reported as a passed 400-device acceptance.

Resilience fixes also make nginx resolve the backend through Docker DNS after
container recreation, and make the local frontend health probe explicitly bypass
inherited HTTP proxies for its loopback-only request. Runtime acceptance covers
PostgreSQL restart plus backend container recreation. One topology-cycle
regression exposed nondeterministic suppression-map mutation; the implementation
now handles removed cycle roots safely and chooses roots in deterministic order.
