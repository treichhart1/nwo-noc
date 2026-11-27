# NOC-127 — Native Slack and technician alert management

This branch starts at NOC-126 PR129 commit `59300dc4bca881e0d09c5aa7397342d6b3f1f26c`, tree `51000d9daee88e50d7b2ddc0b942c418296069f3`. That draft's ten-job run788 is baseline evidence only. The final commit, new draft PR, all final CI conclusions, exact test counts, image scans and durable benchmark results belong in this PR's final acceptance ledger. No existing PR is merged.

## Implementation

Native incoming webhooks and bot `chat.postMessage` reuse canonical NotificationPlan/Delivery/Attempt records, the encrypted NotificationCredential vault, shared emergency controls, global advisory locks and the existing notification worker pool. Native SMS metadata remains separate; SMS claim recovery now explicitly excludes Slack. No duplicate queue, incidents, technicians or customers are introduced.

The administrator Notifications screen adds native Slack configuration, explicit live authorization, channel and severity routing, write-only credentials, exact-source preview and approval, delivery/attempt/thread histories, receipt reconciliation and explicit Slack-to-NOC user mapping. The shared emergency latch stops all notification processing. Native provider disable revokes sending and interaction acceptance. Configuration or credential changes revoke authorization; pending/unknown attempts must be reconciled first.

Only existing incident, monitoring alert, tower finding, field work order, maintenance schedule, signup application, worker heartbeat, canonical notification plan and allowlisted operational audit records can produce drafts. Optional automatic drafting is bounded and never approves sending. Restoration uses verified canonical recovery plans (minimum three recorded samples), or the existing monitoring engine's cleared findings; a resolved Incident timestamp alone is insufficient. Sector/backhaul/fiber/core classification uses documented source relationships. Unsupported rules fail closed. RF interference and OSPF events require actual matching source evidence; there is no synthetic metric producer. Host backup/recovery JSONL logs do not automatically become database AuditLog rows: automated host-failure alert ingestion requires a separately reviewed bridge. Manual arbitrary text cannot masquerade as an incident.

Payloads allowlist event/severity enums, source and infrastructure IDs, timestamps, supported numeric evidence, potential impact counts and safe fixed investigation steps. Names, addresses, contacts, notes, credentials and financial data are excluded. Plain-text blocks prevent mentions or arbitrary markup. Private dashboard links still require NOC authentication. Missing telemetry is unavailable.

## Migration and recovery

Additive `0030_native_slack` follows `0029_multi_provider_sms`. Six Slack metadata/configuration/replay/source/budget tables and nullable canonical delivery/attempt fields preserve all prior data. Receipt uniqueness is workspace/channel/timestamp; replay identity is durable. A restricted SECURITY DEFINER permission function returns only a current user/site authorization boolean. PUBLIC execution is revoked, including after no-ACL recovery bootstrap. No historical migration is removed. Destructive downgrade is refused; use a matching encrypted backup and reviewed recovery.

The installer generates a separate sixth `slack-callback` key and defaults all four Slack flags off. Back up **all six keys**, configuration, database and documents together. Existing systems obtain a separate protected callback key only during separately authorized setup; upgrades never replace existing keys. Optional callback deployment is not automatically installed or published.

## Verification and limits

Local mocked Slack contracts: 33 passed before expanded PostgreSQL tests. The unchanged locked frontend installed and passed type checking and production build. Exact Bandit gate: backend83 reviewed findings, host21 reviewed findings, zero new/unreviewed/HIGH; the single changed fixed-command context is individually re-reviewed. Final PostgreSQL, browser, installation, independent VM recovery, image/dependency scan and strict durable benchmark evidence must be read from the final PR ledger, never inferred from baseline CI. Local SQLite skips are not PostgreSQL passes. No local RAM-backed result is presented as durable performance.

See [Slack setup](SLACK-SETUP.md) and [security and operations](SECURITY-OPERATIONS.md) for activation, capabilities, failure handling and rollback. All HTTP transports in acceptance tests are mocked. No Slack workspace, real messages, production credentials, SMS, Powercode, network equipment, production deployment or public incident publication is used.

## Release decision

Code-level acceptance is conditional on final CI. **NO-GO for production or live Slack activation** until separately authorized workspace testing, channel membership/privacy review, user mapping, egress, signing, security/licensing and operational controls are accepted. Existing native-vendor SQLite/Expat/ACL/util-linux risks, Redis/dependency licensing, production signing/registry approval, VPN/MFA, scanner definitions, off-site recovery and equipment acceptance requirements remain; zero scanner HIGH/CRITICAL is not evidence that every vendor issue is patched.
