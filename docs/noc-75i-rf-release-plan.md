# NOC-75I — RF integration consolidation and release preparation

## CI evidence

The NOC-75H head `f0b8d24` passed GitHub CI #236: backend, PostgreSQL RF integration, database migrations, compose validation and frontend.

## Stacked PR order (do not merge automatically)

The RF UI integration is stacked on the previously developed ingestion/diagnostics foundation. Review the parent/base relationship in GitHub before changing any base; the following latest sequence is verified from the work history:

| PR | Scope | Base branch |
| --- | --- | --- |
| #36 | NOC-75A RF dashboard | preceding RF foundation |
| #37 | NOC-75B device RF health | NOC-75A |
| #38 | NOC-75C site RF health | NOC-75B |
| #39 | NOC-75D accuracy/PostgreSQL coverage | NOC-75C |
| #40 | NOC-75E batched future sample lookup | NOC-75D |
| #41 | NOC-75F query budget | NOC-75E |
| #42 | NOC-75G bulk RF history query | NOC-75F |
| #43 | NOC-75H final review | NOC-75G |
| #44 (this milestone) | NOC-75I release preparation | NOC-75H |

Earlier RF foundation PRs remain in the stack. Do not assume #36 can be merged directly into the default branch without reviewing all earlier parent PRs.

## Consolidation procedure (manual approval required)

1. Review the complete ancestry and parent branch of the oldest open RF PR. Identify the exact first PR that targets the default branch.
2. Verify each subsequent PR is based on the preceding PR head, or explicitly rebase after review. Preserve changes, commits and CI history.
3. Resolve review comments and any conflicts in a dedicated branch; do not rewrite history or force push without explicit operator approval.
4. Run full backend, PostgreSQL RF integration, migrations, compose and frontend CI on the exact consolidated head.
5. Obtain operator approval before any merge, branch deletion, staging, or deployment.
6. Once staging is authorized, validate live UISP RF sample schemas, site assignment, user permissions, query latency, 250-radio truncation warning, rollback and absence of automated incident creation.

## Release blockers and acceptance

- GitHub CI is synthetic/disposable-environment validation, not live RF validation.
- The site summary intentionally processes at most 250 assigned enabled UISP radios; partial data must be disclosed.
- RF degradation is informational only; it must never imply reachability outage.
- Production deployment remains blocked until staging validation and explicit operator approval.
- Current disposition: **review-ready documentation, not production-ready software**.

No merge, deployment, production poll, alert or network change is authorized by this document.
