# NOC-73V — RF collection health performance baseline

This is a **synthetic, disposable PostgreSQL CI** benchmark, not a production
performance guarantee. RF collection remains opt-in and disabled by default.

## Verified NOC-73U baseline

GitHub Actions CI #149, PostgreSQL integration job, 2026-11-12:

| Enabled synthetic UISP radios | RF samples per radio | Total RF samples | Repetitions | Median | Maximum |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 250 | 20 | 5,000 | 3 | 3.58 ms | 10.74 ms |

These numbers are from one shared GitHub Actions runner and do not constitute
a service-level objective or statistically robust latency distribution.

## NOC-73V validation

CI now runs the same health query against both 250 and 1,000 synthetic radios,
20 samples per radio, 3 repetitions per scenario. The benchmark reports median
and maximum elapsed milliseconds. Results should be compared across scales
and repeat runs before deciding whether another index or SQL rewrite is needed.
Do not set a fragile wall-clock CI threshold on shared runners.

The harness rejects unexpected database names, uses a dedicated disposable
PostgreSQL database, and rolls back its synthetic data. It does not contact UISP
or change live network devices. Do not run it with production credentials.

## Rollout decision gates

- CI integration tests and both benchmark scenarios pass.
- Compare measured 250-radio and 1,000-radio timings for disproportionate growth.
- Review PostgreSQL EXPLAIN ANALYZE on a safe, non-production dataset if query
  growth is unexpectedly high; optimize only with supporting measurements.
- Validate auth, freshness accuracy, telemetry retention, and opt-in collection
  configuration before any staged deployment.
- Keep all stacked pull requests draft and unmerged pending operator review.
