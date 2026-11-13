# NOC-86 read-only network integration readiness

## Baseline and isolation

Direct base: NOC-85 `07ac49c150d3d972c3326e6106370b4fa68699b1`, draft PR #89.
GitHub CI #432 / run 43363969392 was confirmed successful before this work.
The stacked NOC-83/84/85 dependency chain remains intact; no PR is merged or
production deployment authorized. Tests use httpx.MockTransport, synthetic JSON,
disposable local PostgreSQL and the existing isolated Ubuntu acceptance stack.
**No live UISP, MikroTik, RF or production integration test is claimed.**

## Activation policy (future administrator action only)

Both UISP_ENABLED and MIKROTIK_ENABLED default to false. Credentials alone never
activate a collector. An administrator must separately review read-only account
permissions, TLS trust and endpoint ownership, provision credentials, set exact
HTTPS origin allowlists and INTEGRATIONS_ADMIN_APPROVED=true, then explicitly
enable the selected adapter. Server-managed configuration is the activation
boundary; there is no HTTP/UI activation control. Trigger endpoints require the
admin role. Offline acceptance additionally rejects activation flags/credentials.
Do not turn on these settings during synthetic acceptance or against production.

Configuration names are documented in `.env.example`. Keep generated test state
private (0700 directories, 0600 environment files). Prefer a secret manager mounted
as a regular, non-symlink mode-0400/0600 file: UISP_TOKEN_FILE or
MIKROTIK_PASSWORD_FILE. File content is bounded and cannot be combined with an
inline credential. Otherwise use protected environment variables; never commit
credentials. Settings redact integration secrets in repr/JSON, and neither
credentials nor arbitrary remote bodies are stored in inventory/provenance/logs.
Old databases may contain raw UISP JSON/error text from pre-NOC-86 versions; audit
and rotate historical credentials through a separate approved process. This task
does not access or scrub production databases. Public API serialization masks
legacy error text rather than returning it to operators.

Allowlist examples use **synthetic names only**, e.g. https://uisp.example.invalid
or https://router.example.invalid:8443. Exact scheme/host/port must match. No
wildcards, URL userinfo, base paths, query strings or fragments. HTTPS certificate
validation is always on for the new adapters; redirects are not followed. Use
trusted CAs/normal certificate trust configuration; never disable verification.
DNS/TLS ownership, device account read permissions and deployment egress/firewall
policy still need independent approval. Exact host allowlisting is not a host
firewall or a DNS-pinning implementation.

## GET-only request contracts and bounded behavior

UISP supports exactly:

- `/nms/api/v2.1/devices`
- `/nms/api/v2.1/sites`
- `/nms/api/v2.1/links`
- `/nms/api/v2.1/sectors`

Device lists may be a single JSON array, an array with integer X-Next-Page, or an
`items` envelope with sequential integer nextPage. Only page/limit query parameters
are sent. Pagination never follows a returned URL. Limits are 50 pages per resource,
10,000 items, 2 MB decoded response per request, 3 s connect/pool/write and 5 s read
timeouts, a checked 10 s per-response budget and a checked 30 s client budget.
Pacing is at most 5 requests/second per sequential client. At most two retries
follow timeouts/transport errors, 429 and 5xx. Retry-After values above 2 seconds
or unsupported/date forms fail safely instead of hammering or sleeping indefinitely.
These are application-level checked budgets; socket/OS scheduling and a blocked
read can add up to the configured read timeout. They are not hard real-time limits.
Synchronization locks serialize each integration within one database; distributed
rate coordination across separate deployments remains an operator concern.

MikroTik RouterOS REST supports exactly:

- `/rest/system/identity`
- `/rest/system/resource`
- `/rest/interface`
- `/rest/system/health`
- `/rest/routing/ospf/neighbor`

Every external request is GET. There is no CLI, arbitrary command, POST, PUT,
PATCH, DELETE, reboot, routing mutation or firewall mutation operation. The adapter
collects one explicitly configured MIKROTIK_DEVICE_ID at one allowed endpoint;
it never discovers/scans IP ranges. Basic authentication is sent only to that
HTTPS origin. Device account permissions must independently be restricted to
read-only REST access. Application allowlists do not prove remote account scope.

The RouterOS contract normalizes identity/version, interface running/disabled
state, exact 64-bit byte/error/drop counters, CPU, free/total RAM, uptime, optional
temperature and OSPF neighbor state. Unsupported optional health/OSPF endpoints
produce partial status and unknown data, not fabricated zeroes. Counter deltas
require trustworthy prior collected data and non-reset uptime/counters; reboot
or counter reset does not produce negative rates. Historical nonzero errors do
not alone establish new degradation. OSPF non-Full states, known running-state
failures and positive error/drop deltas flag degradation. These are observations,
not a confirmed root-cause diagnosis or authorization to remediate.

Endpoint schemas/permissions vary by UISP/RouterOS release. Only the documented
synthetic profiles were tested; version-specific pagination, links/sector shape,
interface statistics and optional sensors require sanitized fixtures and separate
read-only acceptance before any live activation. No unsupported interface fallback
silently issues a command or configuration-changing request.

## Inventory reconciliation and provenance

Migration `0007_integration_readiness` adds integration attempt/success/count
fields, durable IntegrationBinding identities, sanitized import digests/values,
and a source label for monitoring-history entries. Existing data is retained.

- UISP source ID is stable. Bindings map sites/devices/backhauls/sectors to local
  records across repeated imports. Duplicate IDs are skipped. Exact normalized
  MAC matching can correlate a unique existing device; ambiguous matches fail
  closed. Cross-source matches preserve NOC source IDs and operator-owned labels.
- Site and link references must resolve before relationships are assigned.
  Unresolved relationships are skipped/reported rather than inventing topology.
  Existing manual site/parent assignments and manual edits are preserved.
- Stale/future or conflicting observations do not replace current status/last-seen
  information. Missing timestamps yield unknown imported reachability. Fresh
  remote inventory status is kept separate from NOC TCP monitoring state.
- Imported records retain only explicit model/firmware/management fields and
  safe provenance; arbitrary JSON/password/token fields are dropped. Known secret
  values echoed in metadata are redacted; credential-bearing IDs are rejected.
- Valid rows can commit when malformed rows or optional resources fail, with
  degraded status, skip/conflict counts and an audit record. A failed paginated
  device fetch imports no partial device list. Records absent from a partial
  snapshot are never deleted or automatically disabled.
- RF staging preserves the existing independent savepoint, interval/deduplication
  and opt-in rules. RF failure keeps inventory but reports partial status; it
  never claims rolled-back RF samples succeeded. Earlier RF tests retain these
  assertions, updated only for the safe fetch seam, explicit activation and the
  newly truthful degraded status.
- MikroTik maps to an existing NOC device ID; it does not create a duplicate or
  overwrite operator inventory. Observations and read-only reachability history
  are stored with source provenance. Collection success does not overwrite the
  independent TCP monitoring state, and authentication failure is not labelled
  a router outage. Firmware disagreement is reported as a validation conflict.
- Only digest-verified collector observations are served from the MikroTik view;
  arbitrary payloads posted to the generic telemetry API are not trusted as
  validated integration data. Correlation labels probable upstream impact and
  never declares definitive physical root cause.

## Operator endpoints and status view

Settings → Read-only integrations displays UISP and MikroTik configured/enabled
state, read-only status, last attempt, last complete successful sync, received/
imported/skipped counts, stale state and static connection/validation error codes.
It exposes no credentials or activation/equipment controls and makes no network
request to equipment when viewed. Legacy Powercode/LibreNMS remain unsupported
rather than being presented as active collectors.

- `GET /api/integrations/read-only-status`: authenticated, local status only.
- `POST /api/integrations/uisp/sync`: admin-only trigger, still gated by activation,
  approval, TLS, offline mode and exact endpoint configuration.
- `POST /api/integrations/mikrotik/collect`: same admin/gating requirements.
- `GET /api/devices/{id}/mikrotik-observation`: operator/admin, verified local
  observation only; does not poll the router.

Attempt time is distinct from last complete success. Errors never advance the
complete-success timestamp. Partial syncs retain prior complete success and
report degraded state. Worker failures respect the configured interval instead
of retrying every scheduler tick; worker heartbeat is refreshed between bounded
integration tasks. No new remediation path is introduced. RF self-healing stays
simulation-only.

## Reproduction and release checks

Run the existing backend suite with an explicit disposable
RF_INTEGRATION_TEST_DATABASE_URL, plus the new test modules:

```bash
cd backend
python -m pytest -q -rs
python -m pytest -q tests/test_read_only_transport.py tests/test_read_only_integrations_postgres.py tests/test_integration_api_security.py
alembic upgrade head
alembic check
```

New synthetic coverage includes authentication failure, unavailable/unsupported
resources, malformed JSON/schema, response/page bounds, timeouts, bounded 429
recovery, blocked redirects/commands, exact endpoints, duplicate IDs/MAC ambiguity,
stale data, source/manual conflicts, partial sync, firmware conflicts, counter
precision/reset, secret files/redaction, provenance validation and role guards.
No transport test opens a device socket. PostgreSQL tests use real migrated local
storage with in-memory MockTransport responses.

CI retains backend/frontend/migration/Compose/RF, Ubuntu 24.04 installation/recovery,
NOC-85 worker scale and 400-device WISP browser gates. Browser acceptance adds
Settings and disabled/credential-free integration status checks. The new migration
is checked for schema drift/idempotency; validate downgrade/re-upgrade and restored
schema head on disposable databases. Stop writers and use matching backups/images
for any future rollback; downgrading removes only the new integration metadata
and must never be run against production without separate change authorization.

Remaining gates: upstream stack approval, immutable release artifacts, real Ubuntu
VM/systemd/firewall validation, vendor-version contracts, read-only credential
scope, TLS/DNS ownership and approved egress. Production notification policy and
NOC-85 polling timeout-storm SLA limitations remain unchanged. Historical raw
credential retention/JWT revocation remain separate reviews. Exact tested SHA,
counts and CI outcome are recorded in the draft PR. No live integration test,
production readiness approval, deployment or PR merge is implied.

The current migration head is **0007_integration_readiness**, superseding the
0006 head recorded in historical NOC-83/84 documents. Downgrade/re-upgrade and
schema drift checks were run on disposable PostgreSQL only. Do not apply a
historical head literally when following those installation documents.

UISP import rows also report progress to the worker's throttled heartbeat without
sharing database sessions across threads. Large imports still require deployment-
specific timing/transaction budgets; the existing NOC-85 monitoring SLA and
sharding limitations are not removed by these adapters.
