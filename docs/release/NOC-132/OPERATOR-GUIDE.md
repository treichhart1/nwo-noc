# Email operator guide

## Prepare without activating

1. Use the reviewed stacked candidate and existing approved immutable artifact/install/backup procedure. Keep `EMAIL_NATIVE_TRANSPORT_ENABLED=false`, `EMAIL_NATIVE_WORKER_ENABLED=false`, `EMAIL_EGRESS_APPROVED=false`, `EMAIL_CALLBACK_GATEWAY_ENABLED=false`. No test sends to real recipients.
2. In private **Notifications → Unified customer communications**, configure provider (mock, SendGrid or SMTP), business sender/reply-to, approved domains, documented ownership reference, allowed purpose categories, synthetic-recipient hashes and daily/monthly count and cost limits. Unit cost is operator supplied; unknown is not free. No cross-currency totals or billed-cost claims.
3. `cs@nwohiobb.com` can be entered only after actual business ownership and provider/domain verification. It is not preauthorized. Never put credentials in CLI arguments, Git, screenshots or logs.
4. Store write-only credentials using the private administration form, over private TLS. SendGrid JSON keys: `api_key`, `webhook_public_key` (base64 DER P-256 SPKI from the provider). SMTP: `username`, `password`. The existing private notification vault encrypts them; updates revoke live authorization. Callback signing key replacement is blocked while historical nonrejected provider attempts exist: retain/reconcile signing evidence and obtain a reviewed rotation plan, do not bypass the guard.
5. SMTP requires `EMAIL_SMTP_ALLOWED_HOSTS=["smtp.operator-approved.example"]` on the host and the same configured hostname. Port587 is mandatory STARTTLS;465 implicit TLS. No plaintext/25, trust bypass, arbitrary relay or unauthenticated sending. Firewall/egress policy independently allows only the approved host/port. Timeout10s, TLS minimum1.2/default CA+hostname validation.
6. SendGrid requests only `https://api.sendgrid.com/v3/mail/send`, no redirects/proxy inheritance. Grant a dedicated least-privilege Mail Send key; operators managing account/DNS/webhook settings may need separate administrative credentials, never deploy those as the send key. Regional/EU endpoints are not currently implemented or silently substituted.

## Domain controls (operator must execute separately)

Use the actual provider-generated records. Review SPF without duplicating SPF records or exceeding lookup limits; configure provider DKIM, aligned DMARC and a documented return-path. Verify domain and sender in the provider account. Start DMARC monitoring through a separately authorized process before enforcement changes. Confirm reply-to routing and mailbox ownership. Do not copy guessed DNS values from generic examples. This milestone changes no DNS and certifies no deliverability.

## Verified identity and templates

Record a canonical customer ID **or** application ID, actual verified current email, purpose, granted/withdrawn permission and evidence reference. This is not a bulk grant for all channels. Imported customer email alone is not consent or identity proof. Applicant form email consent must remain valid. Record unsubscribe/bounce/complaint immediately through `POST /api/communications/email/suppress` with verified mailbox, evidence reference, reason and `confirm:true`; this holds the address across providers without changing SMS. No automatic resubscribe exists.

Create immutable template revisions in the private screen, selecting an allowed category. Plain-text body is configurable; only `{reference}`/`{status}` expand. Arbitrary HTML/resources/links are refused. Preview renders as text in the UI; generated HTML is escaped, responsive and includes plain-text fallback. Do not put customer PII in reusable templates. Categories:

- Signup: `signup_received`, `signup_information`, `survey_scheduling`, `qualification_result`, `quote_available`.
- Installation: `installation_scheduling`, `installation_reminder`, `appointment_rescheduling`, `installation_completion`, `activation_update`.
- Network: `network_outage`, `extended_outage`, `verified_restoration`.
- Maintenance: `planned_maintenance`, `maintenance_rescheduling`, `maintenance_restoration`.
- Cancellation: `cancellation_received`, `verification_required`, `equipment_return`, `cancellation_update`, `cancellation_completion`.
- Retention: `retention_review`, `retention_offer`.

Use a current canonical source ID and explicit subject, select the immutable template ID, prepare and review the preview. Lifecycle/identity/impact/recovery checks apply. Network sources are existing notification plan IDs; confirmed customer impact and evidence are required. Recovery requires verified samples, not incident closure. No email template authorizes billing, pricing, service activation or disconnection. Offers and actual quote terms stay in their independently approved canonical workflows.

## Approval and safe activation

Offline mock processing is available only in staging through `POST /api/communications/email/mock-drain` with explicit confirmation/reference after independent preview approval. A real loopback SMTP test is in `backend/tests/test_email_native.py` and never uses a provider account.

For a separately authorized real acceptance, first verify host egress, approved endpoints, provider identity and exact synthetic test-recipient allowlist; then independently enable host controls, authorize the configured provider using an evidence reference, and approve one exact draft. Changing provider settings/credentials revokes authorization. No provider or channel fallback follows ambiguous acceptance. Secondary-provider switching is manual and possible only for verified nonaccepted attempts, with a fresh independent authorization/eligibility/budget review. Routing's automatic failover flag is deliberately rejected.

SMTP250 and SendGrid202 mean accepted, not delivered. Missing callback is not failure/nonacceptance. `reconciliation_required` stays quarantined. Signed callbacks may establish actual provider acceptance/delivery; `/email/attempts/{id}/reconcile-accepted` records explicitly labeled operator acceptance attestation with an evidence reference/fingerprint but never confirms delivery or authorizes resend. Cancellation of a queue record is allowed only before submission or after proven rejection.

## Gateway, recovery and rollback

Run the isolated `app.notifications.email_gateway:app` behind a dedicated TLS ingress, **not** the private backend listener. Expose only exact `POST /callbacks/email/sendgrid`; deny all other paths externally. Disable body/access-debug logging, require JSON, cap64KiB, request timeouts/rate limits, verify official timestamped ECDSA signatures. `/health` is process readiness only and may remain private. Set no permissive CORS. Keep the private dashboard and all `/api/*` unreachable. Configure a separate least-privilege database role: read email provider/envelope records, update canonical delivery/attempt outcomes and provider health, insert email events/suppressions/audit plus the existing callback quota table; no access to customer/signup/billing/network tables or provider vault decrypt key. Review exact column grants with deployment security. The receiver uses the existing application keyed identity salt but never needs the credential-vault file; isolate its configuration and network accordingly.

Enable provider signed Event Webhook for processed/delivered/deferred/dropped/bounce/spamreport/unsubscribe/group unsubscribe; no open/click events. Capture the actual public verification key and confirm account ownership. Replays are deduplicated by authenticated event identity; old signatures, unknown attempt capabilities and mismatched recipients reject. Do not set signed callback key from an unverified source.

Emergency disable revokes every email provider authorization; the existing notification emergency latch additionally stops all channels. Preserve outstanding claims and events, do not delete them or reset accepted messages to queued. Reconfiguration requires review. Keep encrypted DB/config/documents/notification vault backups and recovery credentials independent. Existing actual VM recovery now verifies synthetic encrypted email/key and suppression preservation. Restoring an old backup can resurrect stale permissions: reconcile suppression/provider activity before enabling any sending on a restored system. Redact expired terminal encrypted bodies via the private bounded endpoint; retain receipts/history and uncertain claims. Schema rollback is only through reviewed historical migration/recovery procedures, never automatic DB overwrite.

Troubleshooting: inspect canonical attempt codes and timestamps, provider health, private configuration/vault permissions, host allowlist and independently verified TLS. Generic logs must not contain credentials, recipient addresses, message bodies or quote tokens. Never retry an ambiguous SMTP DATA result or unknown HTTP response. Provider-reported cost remains unavailable until an authenticated supported source establishes it; no cost is invented. See existing NOC-126/127/128 guides for independent SMS, Slack and public publication administration.
