# NOC-73W — Operator-controlled RF collection rollout

**Status:** Pre-deployment only. No deployment or network device changes are
authorized by this document. RF collection remains disabled by default.

## Verified synthetic performance

GitHub Actions CI #153, disposable PostgreSQL:

| Synthetic UISP devices | RF samples | Median | Maximum |
| ---: | ---: | ---: | ---: |
| 250 | 5,000 | 4.84 ms | 13.71 ms |
| 1,000 | 20,000 | 9.76 ms | 19.65 ms |

Three measurements per size; shared-runner timing is informational, not an SLO.

## Before any deployment

1. Obtain explicit operator approval and a maintenance window.
2. Review stacked PRs, security controls, migrations, backup and restore plan.
3. Confirm dashboard is VPN-only and UISP API token is read-only with TLS
   certificate verification enabled.
4. Confirm RF_COLLECTION_ENABLED=false and RF_HISTORY_PRUNING_ENABLED=false.
5. Verify the production UISP payload uses explicit RF units; unknown values
   must be skipped rather than guessed.
6. Review database storage growth, retention and recovery procedures.
7. Validate recent successful CI, integration tests, and device freshness
   calculations on a separate staging environment.

## Proposed staged validation (requires separate approval)

- Start with an isolated staging UISP fixture; then limited production
  observation only after operator authorization.
- Keep max devices and minimum collection interval conservatively bounded.
- Observe RF collection run history, failed runs, freshness and database load.
- Compare persisted RF values against UISP's read-only displayed metrics.
- Confirm no radio configuration commands, automatic remediation or alerts
  are issued by this integration.

## Rollback

- Disable RF collection through RF_COLLECTION_ENABLED=false, restart the
  application using the approved change process, and confirm no new RF
  collection runs are staged.
- Leave collected data intact for diagnosis unless a separately approved
  retention action is needed.
- Restore the previous approved release if UISP inventory sync, database
  load or RF health accuracy regresses.
- Record outcome and operator signoff before expanding rollout.
