# NOC-89: historical RF analytics and suspected interference

NOC-89 stacks on draft NOC-88 #92 at
`47ec94159ffb2e41ea8b75720566301e2d196358` (CI #440 passed). Preserve the complete
NOC-83–88 dependency chain; do not merge upstream as part of acceptance. This
release adds local historical analysis, not live spectrum collection or equipment
control. Synthetic fixtures establish software behavior, not real-world accuracy.

## Architecture and migration

Migration **0008_rf_analytics** adds a rebuildable `rf_analytics_points` projection
and an explicit-job checkpoint. Source telemetry remains authoritative. Each
point references a source sample/device and records a radio or microwave carrier
series, source profile/mapping digest, UTC measurement time, projection time,
normalized nullable metrics, exact counters and a validity marker. Only known
numeric/enum data is copied. No raw vendor responses, passwords or tokens enter
analytics tables or responses.

The projector supports NOC-86 typed RF observations and explicitly unit-tagged
legacy RF samples, NOC-87 radio observations, and NOC-88 microwave carriers. It
never collapses carriers into an invented aggregate, infers capacity from PHY
rates, or replaces unavailable data with zero. Reported utilization is now an
optional field in the prepared airMAX AC/ePMP 4600 radio contracts; it still needs
an explicitly approved vendor mapping, and is not derived as a substitute for
an unavailable utilization measurement. Existing firmware/MIB compatibility gates
remain unchanged.

Projection runs in a **separate process/transaction**, never in collectors, the
monitoring worker, or a GET endpoint:

```bash
# Only against the intended isolated environment; default is disabled.
RF_ANALYTICS_PROJECTION_ENABLED=true python -m app.rf_analytics_projector --limit 1000
```

The bounded batch is 1–5,000 source records. A PostgreSQL try-lock prevents
projection/retention overlap. The checkpoint advances by source ID, so late-arriving
old timestamps are processed without full scans. Unknown non-RF schemas are
skipped with progress recorded. Malformed recognized RF becomes invalid markers,
including known microwave carriers; it is never treated as healthy absence.
Unique source-sample/series keys make retries idempotent. A failed transaction
rolls back its checkpoint and points, allowing retry. No collection code calls
analytics, so analytics exceptions cannot propagate through collection. Shared
production database/resource contention still requires operational capacity tests.

Source records are assumed immutable after commit. Historical inventory site is
captured **as projected**, not claimed to be the original site at measurement time.
An explicit microwave link is retained; generic radio samples do not invent a
link association. Mapping/channel/site/link changes establish separate baseline
contexts. Deleted topology objects may clear nullable foreign keys; retain source
backups and review/rebuild projections when correcting historical associations.

Indexes support device/series/mapping/time queries, time retention, site/time and
link/time lookup. Exact 64-bit counters use NUMERIC(20,0). Source/device foreign
keys cascade only when those objects are explicitly deleted; no such production
action was performed. Index and schema drift/idempotency checks remain in CI.

## Historical queries, data quality and baselines

Authenticated operator/admin endpoints (GET only):

- `/api/rf/analytics/options`: credential-free filters, up to 1,000 inventory items.
- `/api/rf/analytics/overview`: window, site_id, vendor, device_id, link_id, offset,
  limit (1–100); current inventory filters and paged evidence.
- `/api/rf/analytics/devices/{id}`: historical buckets, comparisons, baselines,
  scores and evidence for one device.

All queries require PostgreSQL. Each API statement has a five-second timeout;
query failures return a static 503 explanation and do not trigger collection.
The API does not project, prune, poll, write incidents or activate integrations.
Pagination explicitly limits correlation coverage: upstream findings outside the
selected page/filters may be absent. Inventory filters describe current inventory;
point site/link context is projection-time metadata, not reconstructed history.

Supported comparisons:

| Window | Excluded recent evaluation period | Chart bucket |
| --- | --- | --- |
| 1h | 15 minutes | 1 minute |
| 24h | 1 hour | 15 minutes |
| 7d | 6 hours | 1 hour |
| 30d | 24 hours | 4 hours |

Baselines use valid values in the **earlier, disjoint** interval. Defaults require
12 observations per metric, at least 80% valid/nonmissing values, and a valid span
covering 25% of that baseline interval. PostgreSQL computes p10/median/p90, counts
and time span for the same mapping, channel frequency/width, site and explicit
link context. These are empirical reference ranges, not proof the historical
link was healthy or an engineered performance target. A median from insufficient
data is labelled unready and is not used by detection. Unsupported metrics cannot
acquire fabricated baselines.

Raw duplicate timestamps are deterministically deduplicated to the last projected
point before SQL lag/rate/baseline calculations. A retry cannot manufacture a
three-sample streak. Historical buckets use valid-sample means and observed
counts, not interpolated readings. Missing buckets/values are not zero-filled.
The detail response is capped at 2,000 bucket/context rows with a truncation flag;
current history is bounded to 128 unique samples per series. Current pattern
replay uses at most the most recent hour and ignores stale/future data.

SQL lag calculations require ordered valid samples, an unchanged mapping, known
nondecreasing uptime, nondecreasing counters and the configured maximum interval.
Counter decreases/wrap uncertainty, reboots, invalid records, missing uptime and
gaps produce no guessed rate. Integer subtraction occurs before conversion to a
rate, preserving 64-bit deltas. TX/RX errors and retries remain separate inputs;
maximum known rates are labelled explicitly, never silently called packet loss
or BER. Observed down samples, verified up→down transitions and observed restarts
are included in historical buckets. They are sampling evidence, not precise
outage duration, device availability SLA or proof of recovery during gaps.

## Detection, uncertainty and hysteresis

The engine is deterministic, configurable and read-only. By default a condition
enters or clears after three valid observations. Hysteresis bands retain an
established event without creating a new one from a temporary fluctuation.
Missing measurements reset that check to unknown; stale/future/invalid samples,
restarts, mapping/channel/site/link changes and long gaps break continuity.

Implemented patterns include:

- Weak signal, low SNR/quality/capacity; gradual decline relative to a valid baseline.
- A sudden signal step that persists relative to the pre-step reading.
- Sustained modulation-efficiency fallback, capacity reduction and descriptive MCS
  index changes. MCS index movement is **not** assumed to equal spectral-efficiency
  loss across spatial-stream encodings or different vendor mappings.
- Rising noise floor **and** declining SNR; or stable-signal modulation loss with
  additional high error/retry evidence. These can indicate **suspected interference**,
  never confirmed interference. Path loss, firmware/configuration changes,
  congestion and measurement/hardware issues remain alternatives.
- Sustained high reported utilization and directional measured-throughput/capacity
  saturation. **Likely congestion** additionally requires stable baseline signal/SNR
  and no stronger interference-like pattern. PHY rates are not throughput; TX/RX
  throughput is not summed into an invented duplex capacity. High utilization with
  insufficient RF/throughput evidence stays unclassified.
- High/increasing error/retry rates, recurring observed disconnect transitions,
  and carrier-level microwave failures.

Default signal enter/clear is -75/-72 dBm; SNR 20/23 dB; quality 80/85%; absolute
capacity 100/130 Mbps. Baseline signal/noise/SNR changes use 6 dB; modulation
changes use 2 bits/symbol; capacity reduction uses 40%. Utilization enter/clear is
85/75%; directional saturation ratio 0.90/0.75; error/retry rate 10/5 events/s.
Relative-pattern clearing uses half its entry threshold. Defaults allow a 900 s
sample gap and 900 s freshness; both are configurable. Exact ranges, baseline
quality, score thresholds/weights and recurrence policy live in
`rf_analytics_policy.py` and are returned with the API evidence. Unknown,
nonfinite or inverted policy values fail startup. Configure JSON through
`RF_ANALYTICS_POLICY`; no browser equipment or threshold writes are provided.

Recurring periods require at least three adverse observations in a local hour
on at least three distinct days, at least five sufficiently observed days, and
an adverse-day fraction of at least 60%. Missing days are not called healthy.
Default reporting timezone is **America/New_York**, configurable by valid IANA
name; storage remains UTC. Repeated daylight-saving hours share the same local
day/hour group. Recurrence is descriptive adverse-observation context, not proof
of recurring interference or a forecast. A tower pattern requires two distinct
devices, not two carriers on one device.

## Transparent scores and operator workflow

The available-metric score is 100 minus the weighted mean of explicit penalties.
Weights default to one for signal/SNR/quality/capacity/utilization/errors/retries,
and two for reported connection state. Unavailable factors are excluded from the
denominator, **not scored as zero**. Penalties are reported per factor (0.7 for
poor RF/error factors, 0.3 high utilization, 0.5 high retries/reported degraded,
1.0 reported down). A reported down radio caps the score at 20 by default; this
cap and reason are explicit and configurable. Capacity scoring uses a reliable
baseline when available, otherwise a configured absolute threshold.

Confidence is a separate low/medium/high assessment of metric coverage, number
of supported baselines and recent valid sample support. A 100 score with one
available factor or a cold baseline can still have low confidence. The score is
not a failure probability, interference certainty or service-availability claim.
Missing expected measurements are labelled partial telemetry, even if other
available metrics look stable. Stale/future/invalid current data has no current
score. Bucket scores summarize available mean measurements; they are not replayed
historical incident decisions or outage-duration estimates.

The RF Analytics page provides site/vendor/device/link filters, paging, 1h/24h/
7d/30d comparisons, signal/SNR/noise/modulation/MCS/capacity/utilization/rate/score
charts, confidence/coverage, score contributions/caps, recurrence and expandable
alert evidence. Graphs show the selected current operating context, retain gaps,
and never invent readings. Times and recurring hours explicitly use the returned
timezone. No integration activation, radio setting or remediation control exists.

## Topology, incidents and collection isolation

Analytics groups all radio reasons under one device key and all microwave carrier
reasons under one explicit link key. Current A→Z topology/parent traversal is
cycle-safe; potentially affected downstream inventory is probable context, not a
confirmed outage count. Secondary evidence is grouped under a sufficiently
supported upstream RF finding; mutually uncertain relationships retain evidence.
Existing active device/link incidents are linked by ID. Analytics does **not**
automatically create incidents or notifications, so repeated reads cannot create
incident storms or overwrite operator decisions. Existing NOC-85/88 synthetic
incident workflows remain intact. There is no automatic self-healing execution.

Projection/checkpoint transactions, read-only query transactions and explicit
retention transactions are independent of collection. Failures return static
codes/503 responses. No analytics exception is raised from a collector hook
because no such hook was added. Background scheduling, service-level resource
isolation and production database capacity still require operator design/testing.

## Retention, recovery and rollback

Projection retention defaults to **90 days**, configurable from 31–365 days;
31 is the minimum to preserve the complete 30-day analysis interval. Raw telemetry
is untouched. Automatic pruning is disabled. Preview:

```bash
python -m app.rf_analytics_retention
```

`--apply` requires `RF_ANALYTICS_RETENTION_ENABLED=true`, offline staging and
exclusively synthetic inventory. It deletes only a bounded analytics batch
(default 1,000, maximum 5,000), takes the projector lock and writes a sanitized
audit entry. It never deletes source telemetry or production data. Production
retention approval/raw-telemetry retention are separate remaining release gates.

Back up PostgreSQL and private configuration using the NOC-84 guide. Migration
0008 downgrade removes analytics tables/checkpoints only; source telemetry and
previous RF features remain. Stop the projector/retention process, disable their
flags, retain backups and egress isolation, then use the reviewed older image and
schema procedure. After restoring/upgrading, projection can rebuild from retained
source samples/checkpoint; do not reset/delete production projection state as an
acceptance test. Migration down/up checks in this task use only the disposable DB.
No production rollback or deployment was performed.

## Tests, performance and remaining gates

Synthetic fixtures cover healthy/interference-like/congested links, gradual and
sudden signal changes, fallback/MCS distinctions, microwave carrier failure,
short outages, missing/stale/future/invalid data, resets/restarts, low-quality and
cold baselines, deduplication, API roles/bounds, retention/source preservation,
projection failure isolation, topology and recurrence. All existing NOC-83–88
tests remain. The offline browser fixture stages synthetic export observations,
projects them explicitly and validates filters/evidence/charts/comparisons.

```bash
cd backend
python -m pytest -q
# Disposable migrated DB, offline staging, empty synthetic inventory only:
python -m app.rf_analytics_benchmark
```

The query benchmark and parametrized PostgreSQL tests exercise 250/500/1,000
devices with **96 synthetic projected points per device** over 30 days (24k/48k/
96k points). They query every 100-device page plus a detail report, checking at
most nine SELECTs/page and ten/detail, with each page/detail under five seconds.
Fixtures directly populate synthetic query points; these measurements do not
benchmark collection/projector throughput, dense 30-second retention at fleet
scale, or real-device accuracy. Exact timings, final SHA, test counts and CI URL
are recorded in the draft PR. Cold-cache and production latency/SLA certification,
rollup/partition planning for dense fleets and independent field accuracy tests
remain necessary. Current timings are cloud/disposable PostgreSQL observations.

Before operational deployment: review all upstream PRs; validate vendor-specific
units, mapping/firmware support and clock/cadence semantics; obtain representative
labelled field data and tune thresholds without claiming synthetic accuracy;
size indexes/retention/query budgets for actual fleet density; design approved
projection scheduling/resource isolation and incident policy; complete inherited
bare-VM, artifact, credential/JWT and monitoring-SLA gates. No production access,
real-world interference accuracy, deployments, merges, equipment changes or
changes to AI Fiber Designer/MikroTik Management are claimed. All vendor
integrations stay read-only/disabled by default and RF self-healing stays
simulation-only.
