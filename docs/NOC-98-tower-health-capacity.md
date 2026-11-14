# NOC-98 — Tower health, power and capacity

## Baseline and release scope

Branch `feature/noc-98-tower-health-capacity` starts at NOC-97 PR #101 head
`cc4a9b79177a1bffa300a15c0f14184731cf1bdb`. PR #101 was open, draft and unmerged;
Actions #508 passed all eight jobs. Preserve its complete parent chain. Integrate
in the order documented by the upstream release reports, through NOC-95 #99,
NOC-96 #100, NOC-97 #101, then this PR. No upstream PR was merged for this work.
The application version remains `3.2.1-rc.3`; this module is not a production release.

This adds an operator dashboard, PostgreSQL health history and local evidence-based
maintenance analysis. It reuses Site, Device, Sector, NetworkLink, customer mappings,
shared Incidents and NOC-96 notification drafts. It creates no replacement inventory.
It adds no device transport, scanning, equipment writes or automatic remediation.

## Supported inputs and remaining hardware validation

| Input | Reused recorded data | Important limitations |
|---|---|---|
| MikroTik | Normalized RouterSnapshot identity, CPU, memory, uptime, temperature and enabled-interface errors/state | Counter-rate comparisons require stable interface scope and known non-reset uptime. No inferred throughput from arbitrary router ports. Existing RouterOS read-only allowlist is unchanged. |
| Ubiquiti / Cambium | Existing RadioObservation RSSI, SNR, noise/quality diagnostics, uptime, traffic and supported explicit capacities | PHY rates remain distinct from measured traffic. A sector is associated automatically only when unambiguous. Existing collectors' firmware limitations remain. |
| Racom / Aviat | Existing MicrowaveObservation, validated endpoint association, traffic and reported capacity, observed carrier RSL/SNR | Lowest observed carrier RSL/SNR is shown; no invented capacity sum. Modulation trend uses single-carrier observations; multicarrier detail remains in original RF history. |
| Legacy normalized RF | Supported scalar telemetry columns | Unsupported fields remain unavailable. Malformed recorded input is isolated and marked invalid. |
| Registered UPS / power device | Closed `tower_health_v1` recorded payload: AC, DC/battery voltage, UPS/charging state, energy/load, measured/rated full capacity | No native UPS probe or vendor MIB is introduced. Actual model, units, thresholds, polarity and calibration require later approved validation. |

Ubiquiti 11 GHz equipment can be represented when an existing validated adapter supplies
the required normalized measurements. This does not establish native AF11 support or
compatibility with any physical radio firmware. No real equipment was contacted.
Original vendor payloads and unsupported metric details remain in the existing RF system;
this derived module stores only closed, credential-free normalized fields.

## Operation and access

The **Tower Health** navigation entry is restricted to operators/admins at the API.
It lists tower observed state, freshness, known mapped customers, observed utilization
and active findings. Inspect a tower for equipment, power, sectors/backhauls and
customer exposure. Load 30/60/90-day history; unavailable metrics and insufficient
history are explicit. Select a finding to record a technician note, acknowledge it,
complete operator review, prepare a technician notification draft or open the shared
incident investigation. Completion is an audited operator decision, not measured recovery.

The recorded-input worker is **disabled by default**:

```
TOWER_HEALTH_ENABLED=false
TOWER_HEALTH_INTERVAL_SECONDS=15
TOWER_HEALTH_POLICY={}
```

Enabling it only processes already stored NOC telemetry. It does not enable native
collectors or external delivery. The interval is bounded to 15–300 seconds. One dedicated
worker thread processes at most 1,000 recorded rows per tick in 200-row batches. PostgreSQL
transaction advisory locking, unique source receipts and event hashes serialize consumers.
A failed transaction rolls back derived samples, receipts and findings together; replay
is safe after worker/database restart. Errors are static, without raw telemetry/secrets.
Collector failures remain collection failures and do not establish equipment outages.
Existing monitoring health, database/Redis recovery, collector and queue dashboards remain
available alongside this derived module; it does not replace the monitoring scheduler.

Public health data contains no customer contact information. Admin-only actions are
synthetic ingestion, reassessment, policy updates and bounded derived retention. Synthetic
ingestion requires explicit offline staging plus synthetic-monitoring permission. API
responses and validation use existing private error handling. Technician text uses the
shared credential filter and is audit logged. Connection restrictions, TLS checks,
read-only allowlists, offline defaults and simulation-only RF healing are unchanged.

## Evidence and health methodology

A reading has value plus `available`, `missing`, `unsupported`, `invalid` or `stale` state.
Explicit null/missing invalidates that field; unrelated partial frames do not erase it.
Same-time contradictory fields become invalid and the affected daily bucket cannot
qualify a forecast. The default freshness limit is 300 seconds, configurable 60–3600.
Reassessment does not extend source freshness. A valid negative collection result may
still be a successful telemetry collection; it is not healthy equipment.

Default sustained warning confirmation is three fresh observations over at least 60
seconds. Each rule clears only on its own confirmed normal observations, with hysteresis.
A single concerning observation is pending, not green. Missing data never clears an
existing warning. Persistent patterns (reboots, rising temperatures, modulation changes,
error rates and recurring congestion) recommend investigation; they do not guarantee a
future failure or assign a failure probability. Late/stale evidence cannot assert recovery.

Defaults: temperature warning 75 C / critical 90 C, 5 C hysteresis; utilization 85%,
10 percentage-point hysteresis; CPU/memory 90%; RSSI -75 dBm; SNR 15 dB. Three observed
uptime decreases suggest repeated resets, not their cause. Rising temperature requires
an older baseline separated by an hour; errors require stable counter scope, known
uptime and nondecreasing counters. Battery deterioration uses measured full capacity /
rated full capacity below 0.8, never remaining charge as a deterioration estimate.
Global and per-registered-device voltage limits default to **unconfigured**. Operators
must set calibrated limits appropriate to the actual DC system before low-voltage alerts.
Local threshold controls never write equipment configuration.

The optional 0–100 **known observed-check score** applies bounded penalties to known
checks, with thermal/RF family penalties capped per device. Missing coverage or pending
warnings suppress the score. Confirmed whole-site Down Detector failure affects the
score. It is not measured availability, a weighted power budget or a future failure risk.
Priorities/confidence classify technician investigation evidence only.

Only explicit fresh AC evidence is sent to NOC-97 power correlation. Missing AC never
means commercial power loss; battery-only frames do not overwrite site AC evidence.
Power-only episodes may close after confirmed restoration as `power_restored`, without
claiming network liveness. Episodes with failed network checks still require network
recovery. This state cannot authorize a customer "network restored" notification.
Existing topology/redundant-path uncertainty is preserved. Technical maintenance incidents
are scoped per finding, not automatically equated with customer-impacting outage episodes.

## Power calculations

Prefer reported runtime, including a legitimate zero. Otherwise derive minutes only
while discharging and with both remaining energy (Wh) and positive measured load (W)
in the **same fresh frame**: `60 * remaining_Wh / load_W`. Unbounded/nonfinite estimates
are withheld. Charging state, unknown load or isolated voltage cannot justify runtime.
Reported runtime is not guaranteed service duration. A measured full-capacity/rated ratio
is shown separately from remaining charge; inconsistent ratios prompt calibration review.

## Capacity and customer calculations

* Actual TX/RX Mbps divided by explicit current corresponding directional capacity Mbps
  gives utilization and capacity minus demand gives headroom. Both must be in the same
  fresh observation. Implausible traffic exceeding the supplied capacity marks the pair
  inconsistent; no fabricated clamping or denominator is used.
* Generic link capacity is displayed separately. Nominal configured capacity, PHY rate
  and interface speed never become current usable directional RF capacity. Adaptive
  modulation capacity changes are preserved. TX and RX percentages are not summed.
* Vendor utilization, airtime, subscribers, packet loss and latency retain their own
  meanings. Missing airtime is not estimated from interface traffic. Provisioned plans
  do not establish RF demand or congestion.
* Customer counts require active, fresh, verified mappings with consistent tower/device/
  sector relationships. Unverified, stale, conflicting, future-dated or incorrect mappings
  are counted as unknown/excluded, not silently reassigned. Counts distinguish unique
  accounts from services. Plan distribution is by service. Provisioned totals are sums
  of known values with coverage, not guaranteed total demand.
* Oversubscription review requires explicit subscriber-down capacity orientation and
  compares known provisioned down bandwidth to it (default ratio 10). It is a planning
  indicator, not an actual congestion alert. Backhaul exposure is conditional topology
  analysis; known alternative paths are excluded and ambiguous paths remain uncertain.

Daily UTC buckets preserve observed TX/RX peaks, capacity range and local peak-hour
observations using policy timezone `America/New_York` by default. No interpolation over
missing hours/days occurs. Weekly peaks are observed, not complete accounting. A changed
policy timezone excludes incompatible/mixed buckets from forecasting. Daylight-saving
hours retain local clock-hour identity; there is no claim of continuous sampling coverage.

30/60/90-day trends use completed days only. A forecast needs at least 80% qualifying
observed days, 12 matched traffic/capacity hours/day, almost the full requested span, recent
history, fresh current directional capacity, stable sampled capacity within 10%, no
conflicting timestamps and a linear fit R-squared >=0.5. Otherwise it explicitly withholds
the forecast. The 30-day projection is a conditional linear demand scenario, not guaranteed
load or a failure prediction. Upgrade priority considers positive growth and projected
pressure against configured utilization. Customer growth uses actual captured verified
assignment snapshots; current assignments never invent previous customer enrollment.

## Storage, migration, backup and rollback

Additive Alembic **0017_tower_health**, parent **0016_down_detector**, creates nine tables:
event dedupe ledger, normalized samples, source receipts, daily aggregates, current
snapshot, hourly compact history, maintenance findings, notes and policy. It changes no
existing inventory or telemetry schema. Findings are unique by tower/equipment/resource/
rule. A cleared/reviewed finding can reopen on genuinely newer warning evidence, creating
a new technical investigation if requested; audit records preserve previous incident IDs.

Back up the database and application configuration with the existing
[release procedures](NOC-84-installation-and-recovery.md) before upgrading; retain a matching
application image and database backup. Run migrations under the existing isolated release
workflow, not directly against any production database. Run `alembic check` after upgrade.
For rollback use the documented matching pre-upgrade database restore and application
image. Downgrading 0017 removes its derived records and technician notes, so do not use a
destructive downgrade as an operational rollback substitute. Original NOC telemetry and
inventory are untouched by the new migration.

Default derived raw retention is 30 days (configurable 7–90), daily/hourly summaries 400
(configurable 180–730). Pruning is bounded, admin-confirmed or at most 500 rows/background
tick **per derived table** (four tables maximum). Dedupe ledgers outlive the accepted history window; source receipts remain while
the original telemetry row exists, preventing replay after raw retention. Findings/notes
and original source telemetry are not automatically deleted. Retention does not promise
whole-database compaction or bound the upstream telemetry store; use existing source
retention procedures. Maintain PostgreSQL indexes/vacuum and monitor source backlog.

## Validation and limitations

Automated scenarios cover healthy/partial towers, temperature, resets, measured power
loss/restoration, low configured battery voltage, unavailable UPS, current-capacity
congestion/modulation reduction, sector provisioning, customer mapping changes, collection
failure, staleness, concurrent warnings, missing capacity, authorization and replay.
The PostgreSQL suite includes a 30-day/390-observation actual ingestion/aggregation forecast
fixture and insufficient-history/capacity/timezone gates. The browser rehearsal checks
tower navigation, unknown power, capacity distinctions, history, technician acknowledgement
and draft-only notification. The restart rehearsal commits queued source rows, restarts
the isolated PostgreSQL service, processes them and verifies no duplicate derived samples.
All existing RF, outage, Powercode and notification suites are retained in CI. Ubuntu24.04
CI retains clean Compose installation, startup, health, restart, backup/restore, immutable
old-release upgrade and failed-upgrade rollback acceptance.

Final local measurements (40 POPs each):

| Devices | Source/normalized frames | Normalize ms | Assess ms | Repeat ms | Peak process RSS KiB |
|---:|---:|---:|---:|---:|---:|
| 250 | 750 | 7,780.82 | 771.51 | 920.99 | 94,272 |
| 500 | 1,500 | 12,683.65 | 1,169.10 | 949.12 | 122,148 |
| 1,000 | 3,000 | 26,398.62 | 2,505.12 | 2,113.06 | 171,700 |

The 1,000-device assessment used 93 SELECTs / 674 statements, produced 289 deduplicated
maintenance findings and counted 1,670 mapped synthetic customer services. All scales
had zero duplicate findings and zero external calls. This measures stored-data processing,
not native polling or continuous large-network throughput. The first expanded-IN query
hit the ten-second budget at1,000 devices; a single PostgreSQL array parameter fixed it
without raising the timeout. Final CI logs contain repeatable measurements at250/500/1000.
Local validation: full backend run 1,042 passed in 316.54s before ten additional cases
were added; final NOC-98 suite 73 passed in 19.81s. TypeScript/React build passed; new
Chromium workflow 1 passed in 7.5s with zero external requests. Migration drift check
passed. PostgreSQL restart recovered three queued observations exactly once and one
finding. Backup/restore matched content hashes for eight tables and passed queue replay.
Exact final GitHub counts, SHA and CI status are recorded in the draft PR acceptance report.

**Go/no-go: synthetic development acceptance only; NO-GO for production or live pilot.**
A future pilot still requires explicit approval of one device, minimum-privilege credentials,
validated interfaces/MIBs/units/voltage thresholds/counter direction, TLS and endpoint/network
allowlists, emergency disable verification and independent measured network recovery.
Real firmware, UPS energy/runtime calibration, AF11 support, actual sector airtime/capacity,
long-duration/seasonal forecasting, sustained1000-device queue budgets and production sizing
remain unverified. External Slack/SMS remains disabled; internal technical warnings cannot
send customer SMS unless a separate confirmed customer-impact incident and existing policy
explicitly permit it. No automatic equipment changes or RF remediation are introduced.
