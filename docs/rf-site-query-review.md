# NOC-75F — RF site query review

## Scope

The RF site summary processes at most 250 assigned enabled UISP devices and never writes to the database or generates outage alerts.

## Query review

- Site inventory and device inventory: two SELECTs.
- Per-device RF diagnostics: a bounded 24-row telemetry SELECT per radio.
- Device identity lookups: may hit the SQLAlchemy identity map; otherwise one SELECT per radio.
- Future-only RF timestamp checks: a single grouped SELECT, introduced in NOC-75E.

At 250 devices, the remaining diagnostic history query pattern is O(N) database round trips. NOC-75F adds a PostgreSQL query-budget regression test at 40 radios. This is **not** a bulk-query performance fix and does not establish a production latency benchmark.

## Next implementation gate

Replace per-device RF history reads with a single window-function query limited to the most recent 24 samples **per device**. Preserve the same 24-hour lookback, timestamp deduplication, future-only classification, degradation streak rules and read-only behavior. Compare new and existing results on disposable PostgreSQL fixtures before removing the per-device path.

Staging/live UISP validation remains deferred by operator choice. All PRs remain draft and unmerged.
