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

Create immutable template revisions in the private screen, selecting an allowed category. Plain-text body is configurable; only `{reference}`/`{status}` expand generally. For `quote_available` only, `{quote_token}` may appear in the private body after canonical quote access has been independently prepared. It never appears in a URL, subject or public plan metadata. Arbitrary HTML/resources/links are refused. Preview renders as text in the UI; generated HTML is escaped, responsive and includes plain-text fallback. Optional logos are base64-encoded, metadata-free 32–256px RGB/RGBA PNGs under16KiB, embedded as CID MIME attachments, never fetched remotely. Export through an approved image editor; SVG, metadata, tiny tracking pixels, oversized/decompression-bomb images and other formats reject. Operator must own/approve the logo. Do not put customer PII in reusable templates. Categories:

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

Run the isolated `app.notifications.email_gateway:app` behind a dedicated TLS ingress, **not** the private backend listener. Expose only exact `POST /callbacks/email/sendgrid`; deny all other paths externally. Disable body/access-debug logging, require JSON, cap64KiB, request timeouts/rate limits, verify official timestamped ECDSA signatures. `/health` is process readiness only and may remain private. Set no permissive CORS. Keep the private dashboard and all `/api/*` unreachable. Configure a separate least-privilege database role: read email provider/envelope records, update canonical delivery/attempt outcomes and provider health, insert email events/suppressions/audit plus the existing callback quota table; no access to customer/signup/billing/network tables or provider vault decrypt key. Review exact column grants with deployment security. The receiver mounts only the dedicated persistent EMAIL_CALLBACK_KEY_FILE. It must NEVER receive the private NOC JWT SECRET_KEY, ADMIN_PASSWORD or outbound credential-vault key. Supply independent random unused SECRET_KEY/ADMIN_PASSWORD values to satisfy the shared settings schema; do not reuse the private backend environment. Use its own restricted database role and isolated container/filesystem/egress. Identity uses domain-separated HKDF/HMAC from the dedicated key, so private JWT rotation cannot bypass shared email suppression.

Enable provider signed Event Webhook for processed/delivered/deferred/dropped/bounce/spamreport/unsubscribe/group unsubscribe; no open/click events. Capture the actual public verification key and confirm account ownership. Replays are deduplicated by authenticated event identity; old signatures, unknown attempt capabilities and mismatched recipients reject. Do not set signed callback key from an unverified source.

Emergency disable revokes every email provider authorization; the existing notification emergency latch additionally stops all channels. Preserve outstanding claims and events, do not delete them or reset accepted messages to queued. Reconfiguration requires review. Keep encrypted DB/config/documents/notification vault backups and recovery credentials independent. Existing actual VM recovery now verifies synthetic encrypted email/key and suppression preservation. Restoring an old backup can resurrect stale permissions: reconcile suppression/provider activity before enabling any sending on a restored system. Redact expired terminal encrypted bodies via the private bounded endpoint; retain receipts/history and uncertain claims. Schema rollback is only through reviewed historical migration/recovery procedures, never automatic DB overwrite.

Troubleshooting: inspect canonical attempt codes and timestamps, provider health, private configuration/vault permissions, host allowlist and independently verified TLS. Generic logs must not contain credentials, recipient addresses, message bodies or quote tokens. Never retry an ambiguous SMTP DATA result or unknown HTTP response. Provider-reported cost remains unavailable until an authenticated supported source establishes it; no cost is invented. See existing NOC-126/127/128 guides for independent SMS, Slack and public publication administration.

## Reporting denominators

Use Review communication analytics in the private center. Submission success uses accepted/(accepted+proven rejected) attempts; unknown and pending attempts are reported separately rather than counted as failures or successes. Confirmed SendGrid delivery is unique messages with signed delivered evidence / unique accepted messages. Missing callbacks remain unconfirmed, not inferred failures. Bounce/complaint evidence can overlap delivered evidence; event counts are non-additive. SMTP/mock confirmed delivery is unavailable. UTC calendar day/month usage counts submission attempts, while conservative delivery budgets reserve pending/unknown attempts over the configured windows. Currency-specific cost rows retain unknown as null and never combine currencies. Immutable callback kinds/timestamps are shown alongside attempt history, without email addresses or bodies.

## Dedicated email identity key and supported upgrades

Fresh installations provision a private `email-callback` key with the existing installer key mechanism, owner10001 and mode0400. Encrypted backups include it. This key is not a JWT signer and cannot decrypt provider credentials. Keep it stable while suppression/recipient evidence exists; do not silently regenerate it. The actual restore probes recompute a synthetic suppression identity using the restored key.

A NOC131 installation can upgrade with new email flags absent: they remain disabled. Before preparing any email identity/preferences, a separately authorized operator must provision the new private key and add `EMAIL_CALLBACK_KEY_FILE=/run/nwo-noc-keys/email-callback` to protected runtime configuration. Missing, unsafe or unreadable identity keys fail closed. Back up configuration and existing keys using the encrypted procedure first. On the approved empty/new-feature installation only, the following creates a key without printing it or overwriting anything (it does not enable email):

```sh
sudo python3 - <<'PYKEY'
import os,base64,secrets
from pathlib import Path
p=Path('/etc/nwo-noc/keys/email-callback')
if not p.parent.is_dir() or p.parent.is_symlink():
    raise SystemExit('Reviewed existing private key directory required')
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
try:
    os.write(fd,base64.urlsafe_b64encode(secrets.token_bytes(32)))
    os.fchown(fd,10001,10001)
    os.fsync(fd)
finally:
    os.close(fd)
fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)
PYKEY
```

Use an approved private configuration editor to add only the key-file path; never paste key bytes into commands or logs. The existing keys-directory read-only container mount supplies the file. Preserve all native email flags false and rerun existing verification. The controlled-test installer continues to reject explicit live email enablement, as it does for SMS/Slack; production activation requires a separately approved release/policy, not bypassing verify.sh. A dedicated receiver deployment needs only an encrypted, authorized copy of this single identity key plus its restricted DB credential and SendGrid public verification key. Never copy the backend's entire runtime.env, keys directory, JWT signer or provider-vault key to a public gateway.
