# NOC-128A — durable monitoring acceptance

Continue draft PR131 on exact NOC128 source `26ef26c06b00ab9072a3bd670efce132538e4c0c`. No new milestone branch, WordPress implementation, schema migration or application dependency change. No AGENTS.md was present in applicable locations. Local source was clean and the remote PR head was verified before modification.

## Reproduced evidence and cause

[PERFORMANCE-BASELINE.json](PERFORMANCE-BASELINE.json) retains every 1,000-device round from both executions of NOC121/124/126/127, plus both NOC128 executions and their SQL/phase/resource evidence. The complete original profiles remain in GitHub artifacts. The original failure is run815/job140658132780, artifact15220008578, ZIP SHA256 `a91b71e703db09763288b2f5db1b3965c18b94426201261a72edde44ffcb1246` (22,900 bytes).

All eight earlier executions passed. Same-source run814 passed18/18 (min8.912s, median9.736s, max10.327s); run815 failed5/18 (min10.778s, median12.312s, max20.780s). This is a real failed acceptance result, not discarded noise. The downstream RF offline aggregate correctly failed.

Git blob identities for scheduler, alerts, outbox and strict benchmark are identical between NOC124,126,127 and the failing NOC128 candidate. The NOC121 final scheduler is also unchanged. No monitoring schema/index/lock/durability/resource change or new device polling was introduced by WordPress. The WordPress job runs on a separate GitHub runner; it is not a workload running inside the host-package benchmark runner. Provider-level neighbor contention cannot be established from the retained data.

The measured proximal cause is durable-I/O wait amplification, not increased query count or alert history processing. Every 1,000-device round still has8,834 SELECTs,1,000 SAVEPOINTs/RELEASEs and1,033 commits. The worst round (repeat3/round3) used12.733 CPU seconds over20.780 wall seconds (61.28% of one core), RSS129,272KiB. Its concurrent inclusive file/directory fsync time was23,082.203ms (3,000 calls,579.667ms maximum), commit time33,274.798ms (1,033 calls,426.292ms maximum), alert processing14,179.307ms and collection4,388.818ms. Same-source pass profiles show materially lower flush/commit waits with the same SQL shape. Concurrent inclusive totals overlap and must not be added to reconstruct elapsed time.

Historical evidence does **not** contain cgroup throttle counters, OOM counters, host pressure or PostgreSQL WAL timing sufficient to attribute the physical cause to a particular disk, checkpoint or neighbor. Do not claim that distinction was proved. Original directory/file fsync and transactional WAL waits remain mandatory; disabling them would conceal the problem.

Local root filesystem is full, Docker daemon/PostgreSQL unavailable, and an attempted artifact binary download was refused by the managed network proxy (HTTP403). The complete JSON printed in the original workflow log was read instead. The new CI reuses the existing bounded ZIP inspector and existing actions:read permission to independently download, hash and read the original artifact; it asserts the five failed rounds remain recorded, rather than treating that historical FAIL as a pass.

## Small correction and additional evidence

The locked job/attempt/receipt snapshot now also loads the existing Device by its primary key. Collectors and staging reuse that transaction-local ORM identity. This removes one redundant inventory database round trip per polled device, without caching across polls or changing inventory authority. The strengthened real-PostgreSQL regression requires the device to occur in the original joined SELECT and forbids a separate inventory SELECT; it still requires one job advancement, one attempt read, only `FOR UPDATE OF monitoring_jobs SKIP LOCKED`, one sample/receipt and an unacknowledged outbox until commit. Existing fencing, advisory locks, duplicate handling, rollback and recovery tests remain enabled.

The expected deterministic SELECT reduction is8,834→7,834 per1,000-device round. Any elapsed improvement beyond avoided round trips must be distinguished from runner I/O variation; this correction does not guarantee a physical disk latency bound.

Additive evidence captures per-round polling-container cgroup CPU throttling, memory/OOM events, I/O counters and host-scoped pressure totals. Missing/reset counters are null, not healthy zeros; memory gauges are retained as start/end values. PostgreSQL WAL/database counters are read before/after each complete invocation outside measured rounds. Disabled I/O timing is explicitly null; no server settings are changed. Counters are cumulative, may lag, and describe only the disposable synthetic cluster. Existing phase profiles retain SQL categories, savepoint timings, commits, fsync calls and CPU/RSS. No SQL text, parameters, credentials or device data enter diagnostics.

## Invariants and final acceptance

Unchanged:1,000devices,40POPs,synthetic2ms transport,18 measured rounds per independent execution,2CPU/768MiB backend,2CPU/1GiB PostgreSQL,8threads,pool10; named persistent PostgreSQL/outbox volumes; fsync/full_page_writes/synchronous_commit on; at least3,000 file/directory fsync calls per1,000-device round; all samples/unique receipts;15-second maximum. No RAM-backed substitution, dropped rounds, timeout/target increase, retry-to-select-a-fast-sample or security weakening.

Local final correction: **1,325 passed / 1,034 skipped / 3 warnings**,15.14s;39 targeted strict-gate/resource/hardening tests passed. PostgreSQL skips are not successes. The exact final local count and all eleven final-commit CI conclusions belong in the PR131 acceptance ledger. At least two independent complete strict runs are required. If any measured round misses15seconds, retain the failure and continue investigation; do not claim performance acceptance from one favorable run.

WordPress ZIP and source, Twilio/Telnyx/Slack, publication approvals, migrations, image pins, resource limits and production release controls remain unchanged. Real VPN/MFA, vendor-native security/licensing, production signing, website deployment/privacy/theme/CDN and off-site/live-device/provider acceptance remain independently blocked. No production GO, live connection, notification, publication or PR merge is authorized.
