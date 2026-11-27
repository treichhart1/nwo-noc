# NOC-126 — native SMS transports (disabled)

Base: draft PR #128, `c2d8298fcca02f17580265ab7515e4d985ce51f9`, tree `d0875c8a70543b9b03c1e8645416ed57659c2718`. This additive child preserves the full unmerged stack. No upstream PR is merged. Final source revision, CI links, job counts, image scans and durable benchmark evidence are recorded in the draft PR acceptance ledger; do not substitute base run777 for this change's evidence.

## Implemented

The existing NotificationPlan/Recipient/Delivery/Attempt, CustomerAccount, NotificationSuppression, encrypted NotificationCredential vault and notification worker are authoritative. Twilio, Telnyx and the existing mock have a shared submission/reconciliation contract, explicit capabilities, normalized states and preserved native IDs/status/error codes. There is no second queue or consent database.

The administrator notification page provides provider configuration, write-only credentials, routing, separate provider and exact-message authorization, delivery review, attempts, reconciliation, inbound review and daily/monthly cost evidence. Outage, update, extended, maintenance, rescheduled, cancelled and restoration categories reuse canonical approved plans, verified targeting, consent, quiet hours, global budgets and emergency disable. Signup and installation records reuse their existing SMS drafts for provider eligibility/cost previews; prospect dispatch remains draft-only in this release. No fake incident or customer is created to bypass that boundary.

Migration `0029_multi_provider_sms` follows `0028_customer_status`. It adds four provider/routing/event/callback-budget tables and nullable native attempt/delivery metadata, receipt uniqueness and indexes. Existing data and migrations are retained. The one-way opt-out function can add negative consent evidence, never clear it. Downgrade intentionally refuses destructive loss of opt-out/replay/history evidence; use the matching encrypted backup and documented recovery procedure, not an automatic database rollback.

## Safety model

All native host flags, providers, callbacks and failover default off. Existing legacy live-delivery prohibition remains. The native worker uses the existing pool and bounded interval, not a duplicate scheduler. Before network I/O an attempt and budget reservation are committed. Timeout, disconnection, 5xx, malformed receipts or restart during submission are quarantined for reconciliation. No vendor submission-idempotency guarantee is assumed. Accepted, queued, unresolved, missing-callback and unknown submissions never fail over or retry. Safe rejection, bounded retry and optional failover require independently authorized secondary sender/recipient/category plus fresh consent, content, quiet-hour, budget and policy checks.

Verified STOP and provider opt-out errors (Twilio21610, Telnyx40300) block both providers immediately, including a future customer with the same contact. START/HELP never clear consent or send replies. Unknown senders are not turned into customer accounts. See [security/operations](SECURITY-AND-OPERATIONS.md), [Twilio](TWILIO.md) and [Telnyx](TELNYX.md).

## Executed local evidence

Python3.12 backend suite: **1264 passed,1001 skipped,3 warnings**. The skips require PostgreSQL/external disposable infrastructure; they are not passes. New provider unit cases:69; new PostgreSQL cases:31, explicitly included in the existing PostgreSQL CI gate. They cover native contracts, failures, signature validation, consent, budgets, orphan callbacks, committed pre-I/O claims, independent-connection crash/restart/concurrent workers and least-privilege gateway denial checks.

Frontend typecheck and production build passed locally on Node24.22.1; CI retains required Node22 and unchanged lockfile/Vite7.3.8. npm audit including development dependencies:zero vulnerabilities. Hash-locked Python dependency audit:zero known vulnerabilities. Final strict Bandit/Ruff, browser, PostgreSQL, actual Ubuntu installation/reboot/second-host recovery, exact-image scans and persistent-storage performance conclusions must be read from this PR's final CI ledger. No PostgreSQL/container/VM result is claimed from the storage-constrained local workspace. Existing monitoring workload, fsync,15-second target,1000 devices/40POPs and receipt/sample assertions are unchanged.

## Acceptance and remaining requirements

Code-level acceptance requires all ten existing jobs on the final source, including new PostgreSQL cases, browser regression, migrations, installation/recovery, security and strict durable benchmark. No advisory, development dependency or assertion is suppressed. No dependency versions changed. Installer initializes an additional private callback key and keeps all new live flags false; fresh/upgrade schema pins advance to0029 without rewriting historical supported upgrade mappings.

**Live-provider/production NO-GO pending separate operator acceptance.** Required: provider account/registration and sender ownership, independently approved egress, exact callback TLS/DNS/private DB isolation, provider pricing/fees and spend limits, tested provider native webhooks and reconciliation, actual consent/opt-out policy, quiet hours, secret custody/key recovery and approved live recipients. Existing vendor-native risks, Redis/dependency licensing, production artifact signing/publication, actual VPN/MFA, malware definitions, physical off-site recovery and real equipment acceptance remain. A zero scanner HIGH/CRITICAL count does not erase historical vendor risks. No real SMS, provider activation, public publishing or production deployment is authorized by these synthetic tests.

## Initial CI defects retained

Initial source ef2f1fe, runs779/780: the historical release test still asserted28 migrations; corrected to exactly29 plus exact0028 parent. PostgreSQL gate found the callback message-text local shadowed SQLAlchemy text(), preventing STOP function invocation; the SQL constructor now has an unambiguous alias. Two JWT test fixtures used a positional user ID instead of the existing keyword-only contract; corrected without changing authentication. The failing real STOP, gateway and database-role assertions remain. Initial PostgreSQL result:8 failed,1248 passed; this is historical failure evidence, not final acceptance. Subsequent exact-source job conclusions are in the final PR ledger.

Recovery review also identified that the existing no-privileges dump/restore omits function ACLs. The existing post-restore runtime bootstrap now idempotently revokes PUBLIC execute and grants only noc_app on the new one-way opt-out function, when present. A PostgreSQL regression models the lost ACL and checks two bootstrap executions restore the restriction. Optional callback-role grants must be separately reapplied by the approved DBA after restore; no gateway is automatically activated.
