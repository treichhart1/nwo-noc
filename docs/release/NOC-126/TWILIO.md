# Twilio setup and acceptance

Official contracts reviewed: [Messages](https://www.twilio.com/docs/messaging/api/message-resource), [Messaging Services](https://www.twilio.com/docs/messaging/services), [webhook security](https://www.twilio.com/docs/usage/webhooks/webhooks-security), [webhook requests](https://www.twilio.com/docs/messaging/guides/webhook-request), [21610](https://www.twilio.com/docs/api/errors/21610). Unit signature vector is from the official twilio-python RequestValidator tests. Review current provider terms and registration before activation.

## Configuration (private administrator Notifications page)

Choose Twilio. Save the ProviderInput JSON schema below with operator-owned values; angle-bracket values must be replaced. No actual credentials belong in this JSON. Keep enabled false while preparing.

```json
{
  "sender":"<owned E.164 number>",
  "approved_senders":["<same owned E.164 number>"],
  "account_sid":"<AC followed by 32 hex digits>",
  "service_id":"<MG Messaging Service SID>",
  "callback_url":"https://<approved callback host>/callbacks/sms/twilio",
  "callbacks_enabled":false,
  "registration_reference":"<review reference>",
  "recipient_allowlist":[],
  "categories":["outage","update","extended","maintenance","rescheduled","cancelled","restoration"],
  "currency":"USD",
  "price_per_segment_microunits":null,
  "daily_limit_microunits":0,
  "monthly_limit_microunits":0,
  "enabled":false
}
```

In the separate password control enter JSON with exactly `api_key_sid`, `api_key_secret`, `webhook_auth_token`. Outbound Basic authentication uses API key SID(SK) and secret; AccountSID(AC) scopes the API path. The account AuthToken verifies native webhooks and is **not** the API key secret. Credentials are encrypted/write-only. Saving configuration or credentials revokes authorization. Do not paste secrets into curl arguments, logs, screenshots or tickets.

An approved From number and MessagingServiceSid are both submitted; disable provider-controlled sender substitution outside the independently approved sender set. Register US A2P10DLC or verify toll-free senders where required; record sender ownership, operational-message category, consent evidence and provider restrictions. Provider registration is an operator responsibility, not inferred from a string reference.

## Callbacks

Use the isolated gateway in SECURITY-AND-OPERATIONS.md; never point at private NOC. Configure incoming SMS and status callbacks as POST `application/x-www-form-urlencoded` at the exact HTTPS URL above, with no query/redirect/rewrite. Configure account ownership consistently. The adapter supplies the same StatusCallback on sends. HMAC-SHA1 X-Twilio-Signature covers configured full URL and **all** form parameters, including unrecognized ones. AccountSid, owned destination/sender, MessagingServiceSid when supplied and MessageSid are validated.

Native Twilio form signatures have **no signed timestamp**. Durable MessageSid/event/status dedupe, body-conflict rejection, state ordering and explicit provider GET reconciliation prevent repeat processing; this is not a claim of cryptographic freshness. Do not configure a different signature-settings mode expecting an implicit fallback. Captured valid delayed inbound events remain a native-protocol limitation; replayed START cannot restore consent. Monitor conflicts and protect TLS endpoints. Upstream official canonical vector is tested, not an invented timestamp algorithm.

STOP/STOPALL/UNSUBSCRIBE/CANCEL/END/QUIT/REVOKE/OPTOUT and OptOutType STOP generate cross-provider negative consent evidence. START/HELP are review-only. Error21610 is also global opt-out evidence, even when reported after acceptance. Provider-managed opt-out/HELP behavior and service Advanced Opt-Out must be independently verified; the NOC does not emit automatic replies.

## Controlled activation and rollback

Only after separate authorization: validate gateway/TLS, read-only reconciliation and secrets, enter actual approved test-recipient allowlist, configured price/positive caps and callbacks_enabled, then enabled; configure primary routing with failover false. Host gates must be separately enabled by the operator (see shared guide). Authorize the provider with a review reference. Review/approve the canonical incident plan and separately release the exact delivery fingerprint/message/recipient/cost. Saving a plan's simulation approval alone cannot send native SMS. This assignment does not authorize any of those live actions.

401/403 are sanitized authentication rejection;429 uses bounded Retry-After; timeout/5xx/malformed receipt requires reconciliation and cannot fail over. GET Messages/{SID} is explicit read-only reconciliation. A missing GET receipt is not proof no SMS was accepted. To stop: emergency-disable processing and disable provider sending; retain gateway callbacks and keys until pending messages reconcile. Never delete opt-out/history records or retry unknown acceptance. Rotate callback tokens only after draining pending receipts; incompatible signatures fail closed.
