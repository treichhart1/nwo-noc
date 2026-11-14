# NOC-95 — private customer integration and potential network impact

## Baseline and supported scope

Based on **NOC-94 draft PR #98**, commit `4eb327fb96f00520493e9907b3a21e4f78cc908f`, verified green CI #485. PR #98 remains unmerged, based on NOC-93 #97; this branch preserves its complete upstream chain. Follow the NOC-94 dependency snapshot and integration order through #97, then #98, then this PR; reconcile the previously documented #86 identical replay without duplicating its patch. No existing PR is merged, closed, retargeted or rewritten.

NOC-95 implements actual private PostgreSQL account/service storage, atomic source reconciliation, reviewed inventory mapping, dependency impact, incident relationships, consent-aware recipient selection, authenticated dashboards and browser acceptance. It adds migration **0013_customer_foundation** after **0012_session_revocation**, retaining all RF, analytics, troubleshooting, technician, monitoring, session and emergency-stop controls. Application version remains the NOC-94 `3.2.1-rc.3` development candidate; this is not a production release or a deployment.

**Native Powercode API and database compatibility is unverified.** No Powercode API specification, vendor-authorized endpoint list or sanitized database schema was available in the repository. The seeded `powercode` integration label had no customer implementation. This milestone deliberately provides a NOC-owned **synthetic export contract**, mock HTTPS adapter, and local PostgreSQL fixture-view adapter. Their path/view names are test interfaces, **not undocumented Powercode endpoints or tables**. Live source construction/activation is unavailable, even if an environment flag is accidentally enabled. No source credentials are requested, stored or used.

## Source methods and configuration

- `FixtureAPI(httpx.MockTransport(...), limits=...)` performs GET-only requests to the synthetic `https://powercode-fixture.invalid/noc-export-v1` origin. A real HTTP transport is rejected. TLS verification is true, redirects and environment proxies disabled. The opaque incremental cursor travels in a header, never a logged URL. No device/source write command exists. Configurable bounded limits: retries 0–2, request timeout 1–10s, connection timeout 1–3s, exponential backoff base 0.2–1s, envelope size 4096–4,000,000 bytes. Defaults are 2 retries, 5/3s timeouts, 0.25s backoff and 4MB.
- `FixturePostgres(connection)` accepts only an already-open local `127.0.0.1`/`localhost`, autocommit PostgreSQL connection to the disposable database named `nwo_noc`. It initiates no connection itself and accepts no SQL/DSN from an API caller. Within a **read-only transaction**, it validates the exact fixture schema (`noc95_export.customer_batch`, one `payload jsonb` column, exactly one envelope row), sets statement/lock timeouts to 3/1s, and executes a fixed bounded SELECT. Tests provision a synthetic view and verify schema mismatch handling. This is not a native Powercode database mapping or evidence of a production account's permissions. Connection establishment/timeouts for an injected fixture connection remain the harness's responsibility.
- Synchronization imports NOC data only. There is no live scheduler, generic source SQL, arbitrary URL, credential editor, billing writer, SMS/Slack client, or notification-send route.
- Defaults: `POWERCODE_FIXTURE_ENABLED=false`, `POWERCODE_LIVE_ENABLED=false`. Setting the live flag fails configuration validation with a static message. Fixture import additionally requires `APP_ENV=staging`, `OFFLINE_MODE=true` and administrator authorization. CLI seeding also requires `SYNTHETIC_MONITORING_ENABLED=true` and an empty private customer dataset. Never use synthetic fixture tools against real customer records.

### Synthetic export contract

The strict, extra-field-forbidden contract is in `app/customers/contracts.py`. Example (synthetic; choose timestamps not in the future when running):

```json
{
  "schema_version": "noc-powercode-export-v1",
  "snapshot_id": "SYNTHETIC-snapshot-1",
  "mode": "full",
  "complete": true,
  "as_of": "2026-01-01T00:00:01Z",
  "previous_cursor": null,
  "cursor": "SYNTHETIC-cursor-1",
  "accounts": [{
    "external_id": "SYN-C1", "name": "Synthetic Household",
    "phone": "+15555550101", "email": "synthetic@example.invalid",
    "updated_at": "2026-01-01T00:00:00Z"
  }],
  "services": [{
    "external_id": "SYN-S1", "account_id": "SYN-C1",
    "status": "active", "service_type": "wireless",
    "address": "1 Synthetic Lane, Test OH", "plan": "Synthetic 100/20",
    "download_mbps": 100, "upload_mbps": 20,
    "source_refs": {"tower": "SYN-tower-A", "access_point": "SYN-ap-A"},
    "updated_at": "2026-01-01T00:00:00Z"
  }]
}
```

Account IDs and service IDs are distinct stable Powercode-source namespaces; multiple services may share an account. Status is `active`, `suspended`, `disconnected` or `unknown`; service type is `wireless`, `fiber`, `hybrid` or `unknown`. No unsupported field is silently converted into active service. Unknown speeds/contact fields remain null; zero is not invented. Source references are untrusted hints, not NOC foreign keys. Passwords, card/billing data and unrelated fields are rejected. The export contract must be explicitly materialized from a future **validated** native schema; this example does not assert native field names.

Each incremental record is a complete account/service replacement, not a sparse patch. A delta must identify the exact prior cursor and a newer timezone-aware snapshot boundary; missing delta rows remain unchanged. Replayed snapshot/cursor pairs are idempotent. Conflicting duplicates, incomplete snapshots, backwards watermarks, account/service identifier reassignment and schema violations fail atomically. Source rows with older timestamps cannot overwrite newer records. PostgreSQL transaction advisory locking prevents overlapping import/mapping operations. Cursor advancement, imported data and success health/audit commit together. Failure health uses static codes and preserves previous successful data/cursor; a database interruption returns a safe 503 without claiming success.

A `complete=true` full snapshot must contain **all** accounts and services; it is not one arbitrary page. Only a validated completed full snapshot may reconcile absent records into tombstones. Explicit `deleted=true` is also supported. Reconciliation does not physically delete account IDs or incident history; it clears deleted account names/phone/email, disables consent and opt-in, disconnects removed services, and scrubs service address/equipment/source hints. Old deltas cannot reactivate tombstoned records. Full envelopes are bounded to 10,000 accounts, 20,000 services and 4MB; larger sources need a separately tested bounded staging/pagination strategy, never falsely marked complete truncated batches.

## Mapping and operator workflow

Use **Customer Impact** in the authenticated NOC UI. Administrators/operators can search accounts/services by customer, tower, access point or assessed incident. Search uses POST bodies to keep names/contact-like search strings out of standard URL access logs. Contacts remain masked in API/UI responses. Dashboard counts distinguish unique accounts from service records; an account with several service types/towers can appear in several groups.

Choose **Review service**, select existing NOC tower/POP, AP, sector and/or fiber segment, provide a mapping evidence note, and save. Only NOC relationships change. No inventory is created by source synchronization, and no name, IP, source hint or vendor similarity is treated as proof. AP-to-site, sector-to-site/AP and fiber-link endpoint/type consistency are validated. Wireless uses an explicit AP/sector; fiber uses an explicit segment; hybrid may record both without claiming automatic failover. MikroTik infrastructure and Ubiquiti/Cambium access points remain existing NOC devices. Racom/Aviat backhaul links and fiber paths use `NetworkLink`. Missing assignments remain unknown. Changed source tower/AP hints or service type put previously reviewed mappings into `review_required`, preserving the prior assignment for operator inspection but excluding it from confident impact classification.

Communication preferences are NOC-only records: consent `unknown/granted/denied`, opt-out, preferred channel, and a bounded consent-evidence reference. Consent defaults to unknown; importing a phone never grants consent. A changed source phone invalidates previously granted consent. A grant requires an evidence reference; operator changes are audited without recording the phone or free-text evidence in logs.

## Impact semantics and redundancy

Preview one site, AP/device, sector, link, incident or planned maintenance window. An incident assessment can persist idempotent incident-to-service evidence, then be searched through the incident filter. Preview itself does not create incidents, alerts or notifications. Incident targets prioritize an explicit link, then device, then site; site location context on an AP/link incident never expands it into a tower outage. Maintenance with an explicit link similarly treats its site as context. Maintenance without a specific infrastructure target stays unknown; it does not mean every customer is affected.

- **Confirmed:** a reviewed explicit serving site/AP/sector/fiber dependency intersects the supplied failure hypothesis. This confirms the recorded dependency, **not actual subscriber downtime or definitive root cause**.
- **Probable:** a previously recorded active path to a declared `core-pop` is lost; or a hybrid fiber dependency fails with unverified failover.
- **Unknown:** missing/unreviewed/inconsistent assignments, more than 24h since successful service synchronization, unavailable upstream evidence, missing access dependencies or an alternative path whose health is unknown. Unmodeled router dependencies are not inferred from co-location.
- **Not affected:** an inactive service, a different explicit access dependency, or a remaining recorded active path under the specified hypothesis. This is conditional topology evidence, not field verification of physical diversity or routing convergence.

The engine traverses recorded graph edges, supports multiple core roots/redundant paths and backhaul endpoint-device dependencies, and includes baseline/remaining site/link paths plus excluded links in evidence. Source/device reachability and customer impact remain separate. Unknown paths are not counted as proven redundancy, and a missing RF value is not converted into a customer outage. A site outage removes that node; an upstream router/device only cuts links explicitly attached to it. Hybrid failover, Ethernet dependency chains, route policy, shared power and unrecorded physical diversity still require operator evidence.

Impact API previews are paginated (maximum 500 services per page). Counts/eligibility clearly refer to that page, not a complete notification campaign. Persisted incident assessment is bounded to 10,000 service records and refuses larger datasets rather than silently truncating.

## Reusable notification selection — no delivery

`app.customers.impact.recipients(db, results, reveal=False)` is the NOC-96 selection interface. It selects only active-service confirmed/probable impacts, deduplicates services per account, requires strict E.164 format (no guessed country code/extensions), recorded SMS consent, no opt-out, and SMS preference. A number shared by different accounts is suppressed across the **whole** customer dataset, including other pages/unaffected accounts, pending manual review. Unknown impacts are excluded. Public operator previews stay masked; `reveal=True` is an internal future-caller facility and must only be used after that caller's own authorization/audit. It has no transport or sender dependency. Future campaigns must recalculate full authorized recipient sets, ensure freshness/consent and current incident/maintenance lifecycle authorization, and deduplicate across pages before a separately authorized delivery milestone.

## Privacy, credentials and recovery

Customer routes require a current active operator/admin session; viewers/unknown roles and unauthenticated sessions cannot read customer records. Successful access, previews, mapping, preferences, sync and incident assessment are audited by internal IDs/counts/static codes; authenticated denied access is also audited. Audit failure fails closed. Validation/provider/database errors never echo payloads or source exception text. Source cursors are not logged. Standard logging must not be extended to request/response bodies, Authorization headers or raw exports. Phone/email masking is used in API views; no raw export is stored for troubleshooting.

Only synthetic PII-like values belong in fixtures, CI/browser output or repository files. Future real names/addresses/contacts are private data even though this milestone contains no passwords/payment data. Retention, least-privilege operator membership, encrypted disks/backups, legal consent evidence, deletion policy and access-log retention must be approved before real customer ingestion. Existing database backups now contain customer PII; never upload raw dumps/exports to PRs, Slack, public artifacts or logs. Preserve NOC-94's restricted runtime role, separate migration account, private secret mounts, TLS, internal network and durable stop controls. No live network access is added.

Migration/backup/restore/rollback use the NOC-84/NOC-93/NOC-94 procedures. Back up before applying 0013; migrate with the owner-only migration service; the runtime role receives existing default DML grants on new tables. Restore to a **new isolated database**, validate counts/relationships/source cursors/consent/audit and use the matching application image. Do not downgrade or drop private tables to perform a live rollback. Rotate JWT signing secrets and reauthenticate after any restore/rollback before traffic, as required by NOC-94. Preserve old images, database/spool volumes and backups. Old code rollback does not establish that old images are safe to serve production traffic.

## Rehearsal, verification and troubleshooting

Run the backend suite with `RF_INTEGRATION_TEST_DATABASE_URL` pointing only at an explicitly disposable migrated `nwo_noc` PostgreSQL database. `tests/test_powercode_customers.py` validates actual local fixture-view reads and read-only/schema restrictions, source failure/recovery, account/service reconciliation, missing data, multiple services, tower changes, topology/redundancy, privacy/RBAC/audits, consent and no-delivery selection. Existing backend, RF, frontend, migrations, Compose and Ubuntu acceptance gates remain enabled.

In an isolated offline staging stack with synthetic monitoring enabled and an **empty** customer dataset, `python -m app.powercode_rehearsal [200..1000]` creates exactly 40 synthetic POP/tower records, known synthetic inventory/mappings, mock-export customers, incident and planned maintenance. It prints only safe fixture IDs/counts/timings. It refuses existing customer data. The Actions browser stage uses 200 accounts and validates search/masking, mapping, consent, maintenance and incident previews, and viewer denial. No source, customer or network notification is sent. Final exact SHA/counts/CI results are reported in the draft PR.

For `schema_mismatch`, obtain a sanitized vendor-approved specification and update the explicit mapper/fixtures; never guess table names/endpoints. For `incomplete_snapshot`, complete/validate source paging before reconciliation. For `cursor_conflict` or `stale_snapshot`, retain the cursor and investigate source ordering; do not reset state to force an import. For `conflicting_duplicate`/`invalid_reference`, review source identifier ownership/timestamps and NOC mapping evidence. For `timeout`, `unavailable`, `rate_limited` or database 503, retain the last successful snapshot, inspect private source/DB health, and retry within bounded policy. A failed synchronization is not an equipment outage. For unknown impact, review assignments, 24h freshness, core roots and actual redundant paths; do not automatically notify.

## Gates before any future live Powercode access

**GO for synthetic framework validation only; NO-GO for live Powercode or customer notifications.** Required separate work/approval:

1. Supported Powercode version/API documentation or sanitized actual database schema, explicit field/status/service/deletion/cursor semantics, stable identifiers, allowed endpoints or reviewed SELECT-only export view, and validated complete snapshot/pagination behavior.
2. Explicit service-owner/data-owner approval, one narrowly scoped source, a dedicated minimum-privilege read-only account, no billing mutation permissions, no unrestricted SQL/function execution, certificate verification (HTTPS or PostgreSQL `sslmode=verify-full`), exact egress allowlist, connection/query/rate limits and source access audit.
3. Private UID-owned credential-file retrieval and rotation (never source secrets in URLs/API bodies/logs), encrypted transport/storage/backups, customer data retention/deletion/consent policy, access reviews and recoverability.
4. Separately implemented/reviewed live adapter/configuration, restart-safe scheduling/leases and production schema compatibility tests. The current live flag is a hard fail-closed guard, not an activation switch.
5. Real-world mapping/routing redundancy verification and a longer source/DB capacity/failure soak. Synthetic tests do not establish native Powercode, actual radio/RouterOS or subscriber compatibility.

No production systems, customer billing writes, notifications, equipment configuration, RF remediation, merges, deployments, AI Fiber Designer or MikroTik Management repositories are involved.
