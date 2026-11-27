# Slack security, failure handling and rollback

## Authorization boundaries

Private `/api/notifications/slack` routes require an active administrator. Stored credentials are never returned. Callback ASGI contains only its health endpoint and exact interaction route, with no private routers/docs/login. Native signing verification uses a separate encryption key; it cannot decrypt outbound Slack/SMS tokens. Gateway DB grants exclude credentials, private users/customers, financial records, incident updates and outbound queue creation. The permission function is fixed-search-path, read-only and nonpublic. Reapply reviewed optional gateway grants after database recovery; never grant runtime DB owner credentials to the gateway.

Channel/workspace approval, provider activation, exact-event administrator release, fresh event fingerprint and global emergency control are independently checked before sending. The canonical attempt claim commits before HTTP I/O. Existing advisory locks and row locks prevent concurrent claims; a worker restart after claim cannot safely infer nonacceptance. No provider idempotency guarantee is assumed. A narrow disable race after an authorized in-flight request cannot revoke a request already handed to Slack; operator egress isolation remains the emergency containment procedure.

## Failure dispositions

| Evidence | Action |
| --- | --- |
|429 with Retry-After|Proven rejection; provider-wide bounded backoff1–3600s; bounded safe retry|
|Explicit documented authentication/channel/payload rejection|Dead letter; sanitized code only; manual retry requires fresh authorization and remaining attempt budget|
|Timeout/disconnection/5xx/internal_error/fatal_error/malformed success|Unknown acceptance; quarantine, no automatic resend|
|Committed claim expires after worker interruption|Quarantine; never hand it to SMS recovery|
|Accepted bot reply|Persist workspace/channel/message/thread IDs and acceptance evidence|
|Webhook200ok|Persist acceptance with no fabricated message ID|
|Missing callback/history/not-found|Not proof of failure; do not retry|
|Source/provider/actor/channel changes before sending|Suppress stale approval; operator reviews new evidence|
|Emergency latch/paused processing|No processing or new submissions|

Native deliveries show `accepted`, not human-read/delivered. General queued/retry/dead-letter/suppressed/reconciliation-required states remain canonical. Provider-specific attempt history is retained. Manual known-rejection retry and queued/retry/dead-letter cancellation APIs require explicit confirmation/reference; accepted/in-flight/unknown messages cannot be cancelled as unsent. Fresh source approval is required after changed source/configuration. There is no cross-provider Slack failover.

## Safe troubleshooting

1. Inspect the private Slack dashboard for host flags, provider authorization, sanitized health, pending attempts, backoff, thread root and channel roles. Missing credentials/telemetry is unavailable.
2. Keep the common emergency latch engaged while investigating. Native disable revokes the provider; it does not erase accepted/unknown records. Do not clear pending records or manually delete locks/replay rows.
3. For429, wait for recorded backoff and confirm approved budget. For permissions, have the workspace operator verify app installation and membership; never request broad administration scopes to bypass review.
4. For unknown bot acceptance, use approved exact-message reconciliation. If proof cannot be established, retain quarantine. No manual database status edit is an acceptable substitute.
5. Rotate credentials only after draining/reconciling claims. Store replacements through write-only API/UI; configuration and rotation revoke activation. Back up all keys with configuration/database/documents in the existing encrypted recovery workflow.
6. To roll back native Slack operation, disable provider, stop native Slack flag and isolate approved egress. Keep existing canonical queue and SMS operation intact. Do not downgrade migration0030 destructively; restore only a separately approved matching backup, never overwrite newer data silently.

## Live acceptance still required

Workspace installation/OAuth approval, actual API token app identity fields, bot/webhook channel ownership, real rate limits, history API eligibility/read scopes, Slack retry timing and gateway TLS reachability are untested with real Slack. An operator must separately authorize a dedicated test channel and mapped test users, verify privacy, exercise one initial alert/thread/recovery and signed interaction, and record proof before expansion. This milestone sends no messages. Host backup/recovery audit ingestion and unsupported metrics remain explicitly unavailable until a verified producer exists. All pre-existing production/security/licensing/equipment acceptance blockers remain.
