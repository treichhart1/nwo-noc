# NOC-106: Approved fiber integration

## Release and dependency boundary

This implementation is an integration foundation, not verified compatibility with AI Fiber Designer. It uses synthetic exports and a mock adapter. No Designer repository, production endpoint, device, customer provider or notification delivery is accessed.

Base: NOC-105 PR #109, `495182d86e3f90fb6d86be6794c2bbf8457a92d2`, containing NOC-104 PR #108 (`e41a2d89c9cd99a416bcf33216b6255af7286580`) and its upstream development chain. NOC-105 CI #548 passed at inspection. Existing draft PRs remain unmerged. Integrate ancestors in their recorded dependency order, then #108, #109, this PR; retarget/revalidate each child after upstream integration. Do not merge a child directly onto an incomplete base.

| Responsibility | Application |
| --- | --- |
| Engineering route geometry, cable/splice design, as-built approval, construction, permits/easements, 811, reel consumption and cost estimation | AI Fiber Designer |
| Approved revision identifiers, normalized WGS84 route/location display, stable external identity mapping, versioned export contract | Shared integration boundary |
| Operational graph, installed strand documentation/reservations/trace, customer impact, observed health, inventory, IPAM, incidents and maintenance | AI NOC |
| Existing NOC-105 terrain/LOS/RF advisory planning and calibration | Retained NOC radio planning; not fiber construction design |

NOC-104's manual fiber documentation and strand tools overlap engineering records. They remain intact. Prefer source-managed fields for imported assets, expose provenance, and report operator drift as conflicts. Rewiring, strand-count changes and authoritative-source migration require a reviewed migration plan; this milestone deletes no existing feature or fiber record. NOC-105 is reused unchanged rather than duplicated.

## Architecture and ownership

`app.fiber_sync` stages an approved export, resolves explicit canonical mappings, previews native NOC-104 operations in a rolled-back savepoint, and requires a separate administrator approval. Native topology services enforce relationships and reservations. No second tower, asset, customer, document or work-order inventory is created.

Canonical mappings can reference existing sites, assets, devices, Powercode service locations, documents, work orders, IPAM prefixes and interfaces. Source instance/project namespaces are immutable. Bindings use `(source, kind, external_id)` and retain original record revisions. Existing assets must have an installed-compatible inventory state; an approval label cannot turn warehouse or retired stock into installed equipment. Mismatches require inventory reconciliation.

New integration graph annotations are source-owned. Updating existing supported engineering fields requires explicit administrator ownership acknowledgement. NOC operational state, device observations, telemetry and canonical location coordinates remain NOC-owned. Approved point/route overlays retain source provenance and do not modify canonical site coordinates. Native polling timestamps are excluded from engineering drift fingerprints.

## Contract v1

Authenticated operators can retrieve JSON Schema at `GET /api/fiber-designer/v1/contract`. The closed contract is `nwo.fiber-operational.v1`; additional fields, commands and invented telemetry are rejected.

An envelope requires a UUID `envelope_id`, immutable `instance_id`/`project_id`, UTC `generated_at`, integer `from_cursor`/`to_cursor`, and `records`. Cursors must advance from the last consumed source cursor. Every record requires a stable `external_id`, positive `revision`, previous revision where applicable, explicit `publication` (`approved` or `published`), `approval_ref`, timezone-aware `approved_at`, and **`infrastructure_status` (`installed`, `commissioned`, or `completed`)**. Draft or planned construction cannot become operational infrastructure. Approvedness is a source assertion: authenticating a signed export does not prove that its upstream approval workflow is adequate.

Supported discriminated record types:

| Kind | Meaning |
| --- | --- |
| node | Existing site/equipment/service mapped to an operational role, optional approved source point |
| route | Approved read-only route geometry and connection references |
| connection | Physical/logical fiber or GPON connection; existing hybrid links may be explicitly mapped |
| port | Existing node's documented OLT, PON, splitter or other supported termination |
| cable | Bounded cable and strand inventory associated with a connection |
| splice | Approved splice/patch relationship between explicit strands |
| circuit / assignment / path | Documented transport, strand allocation and service dependencies |
| reference | Existing permitted document, work order or IPAM reference |
| construction / as_built | Completed construction summary or approved revision provenance, not a project system |

Geometry is finite WGS84 longitude/latitude. Unsupported lifecycle, measurement and engineering fields are rejected. Limits: 2 MiB signed payload, bounded pre-parser request lengths, 2,048 records/export, 1,728 strands/cable, 10,000 declared strands/export, 100 sources and 20,000 bindings/source. Partition larger projects into reviewed, dependency-ordered exports. No absence-as-delete, automatic discovery, remote binary fetching or unrestricted download is provided.

Generate a synthetic contract example locally (no network):

```python
from types import SimpleNamespace
from app.fiber_sync.fixtures import record, manifest
source = SimpleNamespace(instance_id="synthetic-designer", project_id="approved-demo")
export = manifest(source, [record("node", "pop-1", role="pop", label="Synthetic POP")])
print(export.model_dump_json(indent=2))
```

The node still needs an explicit existing NOC site mapping before approval. A producer must not copy fixture approval values into real exports.

## Authentication and deployment configuration

Private operator routes reuse NOC authentication and field-access request budgets. Administrators manage sources, mappings, approval and recovery; existing authorized operators can read; technicians and unauthenticated users cannot access this administrative integration. Customer details and internal graph coordinates are never placed in public signup APIs. Receipts bind idempotency to the source/run as well as the operation key. Reasons, actor identity and audit records are retained.

Defaults are disabled:

```dotenv
FIBER_DESIGNER_INGEST_ENABLED=false
FIBER_DESIGNER_WORKER_ENABLED=false
FIBER_DESIGNER_KEY_FILE=
```

Source enabled, ingest and scheduling flags are also false. Live HTTP fetching is **not implemented**. Manual JSON preview works without a source secret. For explicitly approved staging, provision a private absolute, non-symlink Fernet vault key using the existing secret-key procedure, owned by the service UID with mode 0600. Configure its file path, then rotate a random per-source signing secret via the administrator credential endpoint. Secrets are Fernet-encrypted at rest, never returned, placed in metadata, or logged. Back up this vault key separately and securely; loss prevents credential decryption. Do not use development secrets in deployment.

Optional signed producer endpoint:
`POST /api/fiber-designer-ingest/v1/sources/{id}/exports`.
It requires HTTPS, global ingest opt-in, enabled source and source ingest opt-in. Headers are `X-Fiber-Timestamp` (Unix seconds), `X-Fiber-Nonce` (canonical lowercase UUID), and `X-Fiber-Signature` (hex HMAC-SHA256). Sign these exact UTF-8 bytes, without a final newline:

```text
v1\n{source_id}\n{timestamp}\n{nonce}\n{sha256(exact_request_body_bytes)}
```

The credential is staging-only: it cannot approve or change equipment. Timestamp tolerance is five minutes, nonce reuse is rejected, and authenticated requests are limited to 20/source/minute, including malformed signed bodies. Invalid signed schemas leave a digest-only failure history. There is no public integration discovery endpoint. Require trusted reverse-proxy configuration, TLS certificate verification at the producer, private ingress/source allowlists and firewall restrictions; do not trust arbitrary forwarded headers. No unverified external URL is configured or contacted by this implementation.

## Operator workflow

1. Open **Fiber Integration** in the authenticated NOC. Register the Designer instance/project as a disabled manual source.
2. Review its identity and explicitly enable the source. Create canonical mappings using stable existing identifiers. Confirm asset location/lifecycle, service identity and document permissions.
3. Stage the bounded approved/installed JSON export. Review additions, version changes, missing mappings and conflicts. Preview changes no installed records and grants no operational verification.
4. Resolve conflicts, then repreview. If a native record was deliberately edited, explicitly rebind the same target to acknowledge its new baseline. Do not approve stale previews.
5. Check the approval acknowledgement and submit the run-specific plan token. Approval checks namespace, cursor, source/binding/native baseline, and locks source, inventory and targets before transactionally applying native changes.
6. Review provenance, source health, versions, imported counts and history. Failed application rolls back native changes and cursor advancement while preserving an actionable failed run.

Reusing the same envelope ID with identical content is idempotent; different content conflicts. Record revisions are immutable; changed content at the same revision is rejected. Incremental exports must follow the consumed cursor and record revision chain. Structural rewires, cable strand-count changes and service ownership changes require reviewed migration rather than implicit replacement. Import does not alter live optical, routing, IPAM or customer provisioning settings.

Native customer-impact calculations reuse NOC-104 paths and Powercode service IDs. Independently verified functioning alternate paths suppress confirmed impact. Unknown failover/topology remains uncertain. A source cannot assert observed health, optical continuity, verified functioning paths or a confirmed physical fiber fault. Installed documentation alone is not proof of service activation.

## Scheduling and emergency disable

The adapter interface is mockable. Only `manual` and `mock` adapters exist. Mock synchronization requires offline/synthetic staging. Scheduling is explicitly opted in and stages **previews only**, never approvals. Intervals are bounded to 60–86,400 seconds. PostgreSQL source locking with SKIP LOCKED, the existing inventory lock, bounded database timeouts and envelope deduplication protect restart/concurrent-worker behavior.

For isolated testing, the optional Compose `fiber-integration` profile adds `fiber-sync-worker`. It uses existing secured application settings, the internal network and private documents volume; no new port is exposed. Set the worker flag only for the intended service. Run `python -m app.fiber_sync.worker --once` for a single isolated cycle; `--health` checks its private heartbeat. Default heartbeat is `/var/lib/nwo-noc/documents/fiber-sync.heartbeat`. A live production scheduler/Designer transport remains unavailable until separately implemented and verified.

Emergency disable: clear source enabled, ingest and schedule flags in the administrator view; disable global ingest/worker configuration and stop the optional worker. Already staged runs still require the source to be enabled and an administrator to approve. Disabling does not erase installed history. Confirm disabled state and audit entry before considering access revoked; rotate the source credential if compromise is suspected.

## Compensation and recovery

Use the run's **compensation preview**, inspect dependent changes, then approve its token with a reason. Only the latest applied cursor can be compensated and any outside dependency or changed baseline blocks compensation.

Compensation retains all native records and audit/version history. It retires newly imported graph paths/edges/circuits/nodes where safe, releases its own strand allocations and deactivates its own joins. New cables' strands become out of service; ports and reference links remain historical documentation. Existing canonical nodes are not retired merely because their imported adoption is revoked. Supported previous source-owned engineering fields are restored with increasing native revisions. Verification evidence is not resurrected.

`current_revision` and the source cursor are **last consumed source positions**, never rewound. `operational_revision` separately identifies the revision currently in use (zero when adoption is revoked). This distinction prevents reapplying a compensated export and preserves accurate provenance. Recovery pauses scheduling, requires administrator acknowledgement, a fresh reviewed preview and later approved source revisions. Retired native operational states may require a separate reviewed operational update; an upstream export cannot override NOC health/status. Compensating one update does not provide automatic cascading rollback of all earlier updates.

Migration `0025_fiber_designer_sync` adds sources, bindings, runs, versions, changes and nonces with foreign keys, uniqueness, lookup indexes and append-only version/change triggers. Backup PostgreSQL plus existing document storage and protected vault keys. Test restore into a **new isolated database**, run `alembic current`/`alembic check`, verify provenance/history and disabled connections before startup. A schema down/up rehearsal on an empty database is not a populated operational rollback procedure. Never drop these tables or overwrite a live database to undo an import.

## Validation and limitations

The focused tests cover closed schema/lifecycle, authentication and staging-only machine scope, immutable versions, dry-run safety, retries, conflicts, transactional failures, compensation, provenance, canonical inventory/IPAM/document/work references, GPON/strand relationships, hybrid customer impact and verified alternate paths. Existing NOC-104/105 tests remain in the full suite. Browser tests cover mobile review, explicit approval, incremental update, conflict handling and compensation. Benchmark fixtures use 40 synthetic POPs, 40/200 segments and 960/4,800 strands with actual row counts; all benchmark transactions roll back and make zero external calls. Exact final results and CI links are in the draft PR.

Known limitations and required Designer work:

- No native Designer API, authentication interoperability, schema compatibility or real approved/as-built workflow has been verified. Designer must implement an approval-filtered, installed/commissioned-only export using stable instance/project/record IDs and immutable revisions, and validate this contract in its own repository.
- No live device, optical continuity, OLT provisioning, physical fault or real customer service was tested. Imported completion labels do not establish field verification.
- No remote fetch transport, automatic apply, automatic dispatch, messaging or construction management is included. No import deletion/tombstone protocol or arbitrary structural migration is implemented.
- Source geometry is read-only display; it is not a fiber design/splice editor. Native legacy manual tools remain pending an approved ownership migration.
- Scheduling is mock-only in isolated staging. Performance numbers describe bounded synthetic workloads, not sustained production throughput, cross-instance load or a full Designer deployment.
- Approval-process review, private transport/firewall policy, backup restoration, key custody/rotation, project partitioning and operator conflict/compensation training are prerequisites to any future controlled integration pilot.

**Go/no-go:** suitable for isolated mock integration acceptance; no-go for a live Designer synchronization pilot until the producer contract, approval filtering, private authentication/transport and operational recovery have been jointly validated. No production readiness is asserted.
