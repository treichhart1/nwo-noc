# NOC129 security and communication contract

Public boundaries are separate ASGI applications and separate database roles. WordPress iframe content comes from a reviewed public HTTPS hostname; its diagnostic fetch uses bounded cURL, certificate verification, public-IP validation and DNS pinning. Redirects, private/link-local/metadata addresses, credentials, custom ports and arbitrary paths are rejected. No private NOC routing or CORS wildcard is added.

Quote decisions use48randombytes, purpose-specific HMAC lookup, per-application encryption at rest, approved/current/expiring revisions, CSRF, Turnstile, PostgreSQL budgets and serialized append-only response evidence. Possession is not identity verification. Rotation invalidates old access; rejection returns the application to review. Same decision retries return the immutable original result; opposite decisions conflict. No token is in a URL, generic audit record or WordPress setting. Security-definer functions fix search_path and public-qualify tables, with PUBLIC execution revoked.

## Explicit prospect SMS

NotificationRecipient has exactly one customer_id or application_id; no synthetic Powercode customer or incident is created for real prospects. Preparing an eligible application SMS draft creates the existing NotificationPlan/Recipient/Delivery records. Snapshot approval includes application revision, current consent, template/message and canonical notification policy. Existing recipient hashes, provider identity, provider authorization/category/allowlist, quiet hours, budgets, rate limits, sender registration, approval fingerprint, ambiguity quarantine, retry/reconciliation and safe failover controls still run. Any context change invalidates the release. Preparations do not send.

A current opt-in and SMS preference are mandatory; verified provider STOPs, imported customer opt-outs and operator-recorded contact withdrawal block both providers. Contact withdrawal creates a shared HMAC suppression, so changing provider or moving between prospect/customer subjects cannot evade it. START does not automatically restore consent. Suppression hashes use the existing canonical notification SECRET_KEY; rotation requires reviewed reindex/recovery, not dropping suppressions. Preserve the independent SMS callback identity key through backup/restore.

Administrator: prepare a signup SMS draft → canonical prospect plan → review exact fingerprint/recipient → approve plan in Notifications → independently release the approved native provider/category/sender/recipient with current registration/budget evidence. Live transport, egress, native worker and provider authorization remain disabled unless separately approved. This milestone tests mocked HTTP only. Never enable actual providers while running synthetic fixtures.

Slack uses its existing source/versioned routing and outbox. Signup/review/quote/install scheduled/overdue/completed events contain only canonical references and safe operational metadata, not names, phone numbers, emails, addresses or quote money. Separate Slack channel/release approval is required; SMS approval never grants Slack approval or vice versa. Unsupported email delivery is not represented as sent.

## Evidence and remaining risk

No application dependency or lockfile update is required. Existing npm audits include development dependencies; existing container, signature, install/recovery, malware failure and benchmark gates remain mandatory. Dedicated signup CI scans exact backend, signup-web and PostgreSQL images and uploads machine-readable reports. Do not substitute historical scanner counts for final results. New scanner findings remain blockers under unchanged gates.

Remaining operational requirements: independently approved public DNS/TLS and egress, isolated restricted login, key custody/recovery, actual Turnstile acceptance, WP theme/mobile acceptance, photo scanner/definition updates, data retention/privacy review, sender registration/consent/provider testing, live Slack workspace approval, licensing and vendor-native risk review. No production readiness is asserted.
