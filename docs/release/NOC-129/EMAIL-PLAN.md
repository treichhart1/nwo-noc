# Bounded email delivery gap

Audit: NOC103 records consent-aware email drafts; there is no native outbound email transport, verified sender contract, authenticated delivery callback/reconciliation or email budget/abuse control. NOC129 does not pretend those drafts are delivered. Operators may use a separately approved manual channel and record evidence. No real email was sent.

A separate bounded change, if authorized, should add one native provider adapter to the existing notification outbox, explicit prospect/customer subject reuse, encrypted credentials, approved sender/domain and recipient categories, consent/unsubscribe/bounce/complaint suppression, bounded retries with ambiguous acceptance quarantine, authenticated replay-safe provider callbacks, estimated/actual costs, fingerprint approval and independent live activation. It must not create a second queue, CRM, public email lookup or billing account.

Acceptance requires mocked contract/security tests, disposable PostgreSQL restart/concurrency tests, cross-subject opt-out enforcement, browser settings/redaction tests, DNS/SPF/DKIM/DMARC verification and separately approved provider-account acceptance. No automatic activation or production mail authorization is included here.
