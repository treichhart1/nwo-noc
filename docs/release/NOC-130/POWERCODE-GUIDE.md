# Powercode operator guide

## Deployment and configuration

Use the existing signed immutable installation/upgrade process and all existing security gates. Migration head is0033_powercode_integration. Back up PostgreSQL, configuration, documents, spool, application encryption and vault keys before upgrade. Never use production data for acceptance.

In the private NOC, open Customer Impact → Powercode integration and provisioning. Operators retain the existing customer mapping/impact screens and status history. The new Powercode administration section and recurring-charge observations require the existing financial administrator permission; only administrators can configure credentials, map packages, prepare/review approvals or inspect exact applicant previews. Anonymous, viewer and field-only technicians have no access. Exact previews use no-store responses. Generic logs and audit events must not contain contacts, credentials, source payloads or applicant addresses.

Connection preparation records one explicit HTTPS origin (443 or444), matching allowlist origin, documentation status and emergency disable. Do not put a credential, path, query or fragment in the origin. Private routed endpoints are legitimate only after operator-reviewed egress/TLS/DNS restrictions. No configured origin is contacted by this release; future native transport must verify DNS/addresses against the independently approved deployment allowlist and installed contract. There is no automatic discovery, separate required gateway or native health probe. Status stays blocked_contract_verification, not healthy.

Store a new credential using the password control. The existing `NOTIFICATIONS_VAULT_KEY_FILE` must be an owned private0600 Fernet key mount; no default key is generated here. Preserve that key separately in encrypted recovery storage. The API only returns configured/encrypted flags. Rotate through the same private interface; no credential can enable native connectivity. Keep emergency disable on outside explicit disposable simulation.

## Synthetic synchronization and mappings

The existing `/api/customers/fixture-sync` is available only when APP_ENV=staging, OFFLINE_MODE=true and POWERCODE_FIXTURE_ENABLED=true. It accepts the documented NOC export contract; it is not a production import endpoint or vendor API. No scheduler connects to Powercode. Obtain sanitized vendor mapping evidence before building a native adapter.

Import package observations with customer/service batches. In the private screen review package technology/audience, availability, speeds, currency and both recurring/upfront charges against a current signup plan revision. Administrator approval binds exact package digest and plan revision. This updates the reviewed reference only; it never copies prices or publishes a portal package. A separate existing signup-plan administrator decision controls public plans/prices. Changed source content or signup plan revision requires new mapping review.

Sync failures retain the last completed cursor and prior materialization. Investigate incomplete_snapshot, cursor_conflict, stale_snapshot, conflicting_duplicate or invalid_reference using sanitized data. Never reset cursors, mark a partial page complete or delete existing customers to force success. Changed phones invalidate prior grants; local opt-outs and shared provider suppression remain enforced. Review source status history for disconnections; it is evidence, not a cancellation command.

## Provisioning preparation

1. Complete existing signup identity/address verification, service qualification, accepted approved quote, installation prerequisites and documented duplicate review. Prepare the existing approved signup provisioning document. Use public quote response evidence or the existing private verified acceptance record; no payment is collected.
2. Complete the canonical installation work order and confirm its inventory/site references. Application must be installed, activation_pending or completed, non-redacted, with current plan/package mapping.
3. Enter application ID/current revision and private evidence-reference identifier. Explicitly attest each verification. Potential duplicate customers require the existing documented match; unresolved active duplicate applications block preparation.
4. Review the exact encrypted preview privately. It includes applicant/address/contact, reviewed package, accepted quote, work/inventory/site IDs and duplicate evidence. These are never sent to Slack, public status or the WordPress plugin.
5. Approve the current fingerprint, then independently queue it. A changed application/quote/work/plan/package/match invalidates authorization. Queueing does **not** contact Powercode; native connectivity remains blocked and no production worker consumes it.
6. In tests only, inject FixtureProvisioner with httpx.MockTransport into simulated_worker. One durable stage executes per invocation. Activation cannot execute. Do not connect a live HTTP transport or production credentials.

## Reliability and reconciliation

Pending review → approved → queued → submitted → account_created → service_assigned → activation_pending are distinct evidence stages. Completed is reserved for a future verified external workflow, not inferred from assignment. Failed, reconciliation_required and cancelled remain explicit states. Activation authorization is separately recorded through `/api/customers/powercode/provisions/{id}/activation-authorization`; it never executes activation, invoices or network writes.

Submission intent/unique operation key commits before mock transport. Accepted IDs commit before the next step. A30-second in-flight lease prevents another worker claiming an active submission; expired intent quarantines. Transport is bounded5seconds/connect3seconds and performs zero automatic write retries. A timeout, malformed success, unexpected redirect/status or missing ID is uncertain. Unknown acceptance must never trigger a second create or automatic provider retry. Accepted account + failed assignment retains its ID; cancellation cannot erase submitted/partial history, and no compensating account deletion exists.

For reconciliation_required: emergency-disable processing, retain attempt/event/preview/source evidence, obtain separately authorized source-side read-only verification with the verified vendor contract, compare account/service IDs and exact intended package, and document the result. This release has no native reconciliation request or automatic resume; do not alter database records or reset a submitted state to queued. Native resumption remains blocked pending the verified adapter/contract and explicit operator authorization. Public APIs cannot release this queue.

## Financial, communications and recovery

Recurring observations group active service charges by documented currency/basis; unavailable amounts stay unavailable. They are contracted or estimated recurring revenue, never invoiced revenue/collected cash. Existing NOC finance records remain authoritative for their own actual/estimated reporting; this release does not create a second ledger or automatically book charges. No bank/card/payment/invoice source fields are accepted.

Customer synchronization never grants SMS consent or sends messages. Existing Twilio/Telnyx prospect/customer approvals, shared opt-outs, budgets, quiet hours and reconciliation remain unchanged. Slack remains separately authorized and PII-free. No provider is activated.

Recovery must restore database and keys from the same backup to a new isolated target, never over newer data. Verify canonical account/service IDs, local/network references, package mappings, immutable previews, accepted external IDs, attempts/events, consent/opt-outs and source cursor. Submitted records require reconciliation after a crash; never assume a restored queue is safe to replay. Keep emergency disable and all live integrations off. Restore/signup/vault key failures must fail closed. Use existing INSTALL/UPGRADE/BACKUP_RESTORE/VERIFY procedures; no destructive automatic migration downgrade is provided.
