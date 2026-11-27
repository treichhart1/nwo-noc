# Powercode API verification matrix

Reviewed2026-11-27. Sources: https://api.powercode.com/ ; https://kb.powercode.com/?ht_kb=powercode-api ; https://kb.powercode.com/?ht_kb=rest-api ; https://restapi.powercode.com/ ; https://api.powercode.com/v1-7/ . Public documentation confirms multiple API generations and authentication concepts; it does not establish compatibility with this installation. No production endpoint was contacted.

| Operation | Native status | Delivered NOC contract / remaining proof |
| --- | --- | --- |
| Customer retrieval/contact/address | BLOCKED | NOC95 export account/service observations; obtain installed-version schema, ID ownership, contact/address semantics and pagination |
| Service retrieval/status/equipment/tower/sector | BLOCKED | Existing stable IDs and reviewed network mapping; obtain status enums, equipment/interface references and authoritative association fields |
| Service packages/speeds/availability | BLOCKED | Optional NOC-owned package observations and administrator mapping; obtain catalog schemas, price/currency/tax/promotion/availability semantics |
| Monthly recurring revenue | BLOCKED | Explicit contracted/estimated source observations only; no invoices, collected cash, bank or payment fields |
| Full synchronization | BLOCKED | Existing complete/full synthetic contract, timestamp and duplicate checks; verify source completeness, paging and tombstone behavior |
| Incremental synchronization | BLOCKED | Existing contiguous cursor contract; verify native delta/cursor ordering, overlap, expiry and deleted-record guarantees |
| Account creation | BLOCKED | Immutable reviewed preview and socket-free synthetic acceptance; verify exact request/response, duplicate search, idempotency, billing side effects and stable accepted ID |
| Service assignment | BLOCKED | Separate synthetic stage with preserved account ID; verify assignment vs activation, service ID, charges and partial failure behavior |
| Activation | BLOCKED | Independent authorization evidence only; no activation executor |
| Cancellation/package changes | BLOCKED | Read-only source status history only; no external cancellation/change method |
| Health/authentication | BLOCKED | Config preparation, encrypted vault, explicit HTTPS origin and sanitized errors; no native health request or successful connectivity claim |

## Synthetic protocol, not native Powercode

`noc-powercode-export-v1` remains the NOC-owned canonical test envelope. Optional `packages` entries carry external_id, name, technology(wireless/fiber/hybrid/lte/5g), audience, availability, currency, monthly/installation charges, speeds and aware source timestamp. Services optionally include package_id/monthly_recurring/currency/revenue_basis. Unknown values stay null/unavailable; contact import never implies consent. A complete full batch includes all accounts/services; omitted records become existing source tombstones, never physical deletion. Catalog omissions retain records; unavailable packages require explicit evidence.

The mock-only `.invalid/noc-provision-v1/account` and `/service` routes are **NOC synthetic fixtures**, not Powercode endpoints. Accepted outcomes require a stable external ID; explicit nonacceptance distinguishes authentication/rate-limit/validation errors; unexpected statuses, redirects, malformed successes and timeouts are ambiguous. No write retry occurs. Neither API-key placement nor basic authentication is guessed from examples. Never redirect native credentials, put them in query strings or disable TLS.

## Exact evidence required before a native adapter

Provide the installed product/platform/build; matching vendor OpenAPI/Postman export and license/permissions statement; sanitized requests/responses with status/headers for each allowed operation; complete pagination/delta/deletion contracts; least-privilege auth/header/certificate requirements; documented rate/timeout policy; stable account/service/package/equipment/network IDs; deduplication/idempotency and timeout reconciliation behavior; billing and activation side effects. Include explicit examples of suspended/disconnected/inactive services, duplicate contacts, multiple services, missing pages and accepted-account/failed-assignment outcomes. Never supply real credentials, payment data, private keys or customer PII.
