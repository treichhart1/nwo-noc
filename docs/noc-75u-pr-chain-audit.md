# NOC-75U — Draft PR chain and staging blockers

Audit date: 2026-11-12. This is a repository-only review, not staging acceptance or deployment authorization.

## Verified stacked chain

| PR | Milestone | Base branch | Head branch | State |
| --- | --- | --- | --- | --- |
| #48 | NOC-75M RF authorization | feature/noc-75l-consolidated-security-review | feature/noc-75m-rf-authorization | Draft, open |
| #49 | NOC-75N RF role regression | feature/noc-75m-rf-authorization | feature/noc-75n-rf-security-regression | Draft, open |
| #50 | NOC-75O API docs default-off | feature/noc-75n-rf-security-regression | feature/noc-75o-deployment-security | Draft, open |
| #51 | NOC-75P production config guards | feature/noc-75o-deployment-security | feature/noc-75p-config-safeguards | Draft, open |
| #52 | NOC-75Q Compose network exposure | feature/noc-75p-config-safeguards | feature/noc-75q-compose-network-guard | Draft, open |
| #53 | NOC-75R offline env preflight | feature/noc-75q-compose-network-guard | feature/noc-75r-deployment-preflight | Draft, open |
| #54 | NOC-75S consolidated offline audit | feature/noc-75r-deployment-preflight | feature/noc-75s-readiness-audit | Draft, open |
| #55 | NOC-75T staging evidence gate | feature/noc-75s-readiness-audit | feature/noc-75t-staging-readiness-gate | Draft, open |

All eight PRs were reported by GitHub as mergeable and remain draft/unmerged at audit time. This does not mean they have been reviewed, merged, deployed, or tested in staging.

## Verified CI history

- NOC-75M: CI #262 success
- NOC-75N: CI #265 success
- NOC-75O: CI #271 success
- NOC-75P: CI #277 success
- NOC-75Q: CI #282 success
- NOC-75R: CI #286 success
- NOC-75S: CI #290 success
- NOC-75T: CI #295 success

## Outstanding blockers

1. **Staging not performed.** No isolated staging environment or live runtime acceptance evidence was provided.
2. **Backup/restore and migrations not validated in staging.** CI's disposable database migration tests are not a staging restore test.
3. **UISP integration isolation not confirmed.** Do not use live UISP or MikroTik for acceptance without a separately approved plan.
4. **RF collection/pruning and alerting must remain disabled** until staged review and operator approval.
5. **Operator security review outstanding.** Confirm RF admin/operator role policy, credentials, CORS, VPN access, and deployment architecture.
6. **Production readiness not established.** The offline preflight and evidence gate do not grant production deployment approval.

## Next controlled action

Prepare an isolated staging environment and collect actual evidence against `docs/noc-75t-staging-evidence-template.json`. Do not change the evidence flags merely because CI is green. Keep the PR chain unmerged until the operator approves a merge and staging plan.
