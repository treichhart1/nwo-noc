# NOC-74H — RF staging approval and acceptance record

**Status: NOT APPROVED.** This template is for an operator to complete after
review. CI success is not staging deployment approval.

## Pre-deployment approval (all required)

- [ ] Operator approves isolated Ubuntu 24.04 staging host and maintenance window.
- [ ] Exact immutable RF release commit and all stacked draft PRs reviewed.
- [ ] Latest full CI is green (baseline: CI #193 on NOC-74G).
- [ ] Staging PostgreSQL is isolated, backed up, and migration/restore plan reviewed.
- [ ] VPN-only access, loopback port binding and authenticated API verified.
- [ ] Unique staging secrets stored outside Git; read-only UISP access only after separate approval.
- [ ] RF_COLLECTION_ENABLED=false, RF_HISTORY_PRUNING_ENABLED=false,
      UISP_VERIFY_TLS=true; staging preflight passes.
- [ ] No live production UISP endpoint or credentials in initial staging.

## Acceptance evidence (record outcome and evidence link for each)

| Gate | Expected result | Result/evidence |
| --- | --- | --- |
| Database migrations | Upgrade and drift checks pass on isolated PostgreSQL | Pending |
| Disabled RF collection | Inventory sync works, zero RF telemetry/run writes | Pending |
| RF API authentication | Anonymous RF endpoints reject access | Pending |
| RF diagnostics UI | Empty/stale/fresh/future states presented correctly | Pending |
| Simulated UISP data | Explicit-unit metrics persist, ambiguous metrics skipped | Pending |
| Restart/failure recovery | Partial writes rollback, retry produces one sample | Pending |
| RF history/reliability | Run counts and retention limits correct | Pending |
| Performance | CPU, DB growth and latency observed within operator-approved limits | Pending |
| Rollback drill | RF disabled, staging service restored without data loss | Pending |
| Real UISP comparison | Approved read-only sample matches radio displayed units | Pending |

## Stop conditions

Stop staging if any public port exposure, credential leakage, unexpected UISP
network access, data corruption, wrong RF units, or inventory sync regression
is observed. Do not enable pruning or collection to work around a failure.

## Operator decision

- Staging host/VM: **Not selected**
- Approved commit SHA: **Not selected**
- Approved window: **Not scheduled**
- Reviewer/operator: **Not signed**
- Staging deployment: **Not authorized**
- Limited production observation: **Not authorized**

No automatic merge, deployment, radio commands, or production configuration
changes are authorized by this checklist.
