# NOC-102: preventive maintenance, documents and service records

## Dependency strategy and architecture

Builds directly on unmerged NOC-101 draft PR #105, head `45e7487ec3087b53b9d416df7da72feafaf24a48`, whose CI #522 passed all eight jobs. NOC-100 #104 remains its unmerged parent at `552287c31e7951c3dbd1b50ddfbe14309ab54761`. Integrate/review the existing upstream chain first: NOC-83 through NOC-94, then #99 NOC-95 → #100 NOC-96 → #101 NOC-97 → #102 NOC-98 → #103 NOC-99 → #104 NOC-100 → #105 NOC-101 → this PR. No PR is merged by this milestone.

The existing React application gains Maintenance & Documents and an embedded collection on the NOC-101 tower QR page. Existing Site, Asset, User, WorkOrder, Incident, EquipmentMaintenance, CostEntry, parts ledger and tower findings remain canonical. There is no second work-order or equipment system. IPAM remains on the existing shared logical Device; service documentation never deploys address, VLAN, routing or RF changes.

Additive PostgreSQL revision `0021_maintenance_documents`, parent0020, creates nine tables: schedules, occurrences, service records, recorded availability, internal reminders, document categories, document records, versions and document audit events. Existing migrations are unchanged. Native append-only guards protect service history/document events and prevent revision content metadata from being rewritten. Only version scan status/provenance/timestamp can change; every control action is audited. Document binaries use the filesystem, not new PostgreSQL binary columns. Old NOC-100/101 attachments retain their existing architecture and continue to be included in database backups.

## Recurrence and internal work generation

Schedules specify tower, optional asset/template, maintenance type, weekly/monthly/quarterly/semiannual/annual/custom-days recurrence, timezone-aware first due date, technician, priority, duration estimate, required equipment, generation lead days, due-soon warning and overdue alert grace days. Default types cover tower/backhaul/router-switch/UPS-battery/power/cabinet/grounding-surge/fiber/warranty/security inspections; codes are extensible.

Dates are normalized to UTC. Day/week intervals use elapsed UTC days. Calendar-month recurrences retain the original anchor day: January31 → February28/29 → March31. Annual February29 returns to29 in leap years. This is a UTC-clock schedule, not a local civil-time/DST rule; the browser shows local time. Custom intervals are whole days,1–3650. Recurrence edits require pausing the old schedule and creating a replacement, preserving prior occurrences; assignment/pause updates use revisions.

A transaction-scoped lock and unique(schedule,index) constraint create exactly one NOC-101 work order per occurrence and advance the cursor in the same transaction. Database/worker interruption before commit rolls everything back; retry after commit finds the advanced cursor. A cancelled work order remains the documented occurrence and is not silently recreated. Catch-up is bounded to100 eligible schedules/one occurrence per schedule per tick; repeated ticks drain older occurrences. Blocked assets/assignees require administrator review. Pause obsolete schedules rather than silently following moved/retired equipment to another site. The creator must remain an active administrator, and configured technicians must remain active.

Set `MAINTENANCE_SCHEDULER_ENABLED=true` only after reviewing schedules. It defaults false. The existing worker runs the bounded maintenance task approximately once/minute, independently of collectors; PostgreSQL owns durable scheduling state, not Redis queue contents. The UI also exposes explicit administrator generation/refresh actions. Configured assignment creates an internal work record only: no automatic dispatch, external messages, purchases, equipment changes or incident closure.

## Calendar and recorded availability

Day/week/month views query UTC boundaries derived from the browser's local dates. Filter by tower, technician, equipment category and maintenance type. The calendar includes generated work, not-yet-generated projections, completed work/service records, existing planned network maintenance and administrator-recorded availability. No availability is inferred when no record exists. Scheduling does not automatically reassign staff based on those records.

Queries are deliberately bounded: at most62 days,500 work rows,100 schedule projections,250 service rows and bounded network/availability records. Responses identify truncation and request narrower filters. Long-overdue anchors are skipped arithmetically when projecting a future window. Work-order links open the existing field workflow. Due dates and completion dates remain distinct; completed work uses its actual completion timestamp.

## Equipment service records and reports

Record service against an assigned in-progress asset work order. The existing equipment maintenance service records technician, service date, condition, fault, repair, documented parts and follow-up; the extension adds diagnostics, labor minutes, warranty claim reference, historical site, work order and incident reference. Records remain linked to the original physical asset and historical tower if equipment later moves or retires. A warranty claim note is not proof of manufacturer acceptance.

Administrator-only actual repair costs use existing NOC-100 CostEntry records with explicit currency and exact decimal amounts. Unknown labor/cost stays unknown. No currency conversion, missing-price estimate or depreciation assumption is introduced. Technicians cannot submit or retrieve financial values. Parts notes do not consume stock: actual consumption comes from NOC-101 parts events/NOC-100 ledger, avoiding double counting.

Reports cover service details, work counts by tower/technician/status, overdue/upcoming work, documented fault counts, service frequency, known/unknown labor, actual parts consumption, warranty claim notes and optional costs by currency. Aggregates query the full filtered scope; detail pages are bounded. Report windows are limited to366 days. CSV neutralizes spreadsheet formulas, enforces the same role/filter restrictions and refuses export beyond1000 service rows rather than silently truncating it; narrow filters. Printable reports contain only data returned under the current role. Structured labor/parts/currency fields are available for a future ROI module.

## Document store configuration

Default private root: `/var/lib/nwo-noc/documents`; override `MAINTENANCE_DOCUMENT_ROOT` with an absolute, runtime-owned0700 directory without symlink ancestors. Docker Compose mounts a dedicated `maintenance_documents` volume into backend/worker. Runtime UID10001 owns it. The existing one-shot runtime ownership preparer validates the directory/shards/files before changing ownership and modes; it never reads or rewrites payloads. Custom stores require equivalent operator provisioning. Do not expose the directory through nginx/static hosting.

Files are immutable SHA-256-addressed objects under two-character shards. Upload writes a private temporary file, fsyncs it, atomically links the completed object without overwrite, and syncs the directory. Reused content must match its checksum. Failed database transactions may leave unreferenced immutable files; they are inaccessible through the API and intentionally retained for administrator reconciliation, not automatically deleted. Storage capacity/quotas and off-host encryption remain operational responsibilities.

`MAINTENANCE_DOCUMENT_MAX_BYTES` defaults5242880 and permits up to10485760. The existing image/plain-text validator remains limited to512KiB. PDF supports bounded, static, unencrypted reports up to500 pages/limited object graph; JavaScript, automatic actions, embedded files, forms and external/action structures are rejected. PDF parsing is not a malware or rendering guarantee. PNG structure/CRC and JPEG signature checks retain their existing limitations. File names and metadata are validated; no credential/password fields exist. Approved configuration documents must be sanitized offline: do not upload device secrets, private keys, passwords or tokens.

Nginx permits bounded14MiB JSON upload requests only under the maintenance API. There are no server-side URL fetch/import or unrestricted file paths.

## Quarantine, malware scanning and approval

`MAINTENANCE_DOCUMENT_SCANNER=disabled` is the secure default: uploads remain quarantined and cannot be approved/downloaded. Configure `clamav` with an approved local Unix socket using `MAINTENANCE_CLAMAV_SOCKET`; mount/provision that socket explicitly. The adapter uses bounded ClamAV INSTREAM with no shell or TCP/public network connection. Scanner errors/infected responses fail closed. ClamAV daemon installation, signatures, scanning service health and real malicious-corpus validation are operator prerequisites, not supplied by the synthetic tests.

`synthetic` scanner mode is available only with staging + offline + explicit synthetic monitoring flags. Its fixture result is never trusted outside that isolation, even if a synthetic database is restored elsewhere. It does not establish real malware protection. Production must use a separately validated real scanner.

Each version retains revision number, original filename/type, size, SHA-256, uploader/time, change description and scan provenance. Approval is explicit and administrator-only after a trusted clean result and checksum read. Uploading a newer revision does not replace the approved version. Technicians/operators can download only the current approved clean revision; administrators may retrieve historical clean revisions. Quarantine/infected/error versions cannot be downloaded even by administrators. Rescan can invalidate access to an earlier approved revision.

Downloads reauthorize site/classification, recheck file hash/size, emit attachment disposition, nosniff, no-store and sandbox headers, and create audit history. No public file links or embedded document viewer is provided.

## Permissions, categories, QR and retention

Document categories are administrator-managed data, allowing site agreements/drawings/photos/reports, network/IPAM/RF/configuration/fiber diagrams, manuals/warranties/invoices, splice/OTDR/permit/811 records and future categories without database redesign. Classification is explicit: technician, operator or administrator. Administrators must classify invoices, agreements and sensitive access/configuration material appropriately; automated sensitive-content/OCR classification is not implemented.

Technicians see only technician-classified documents at towers authorized by their current NOC-101 assignments/grants. Operators see permitted non-administrator documents read-only. Administrators manage approval, holds, deletion/restoration, schedules and availability. Existing NOC authentication, durable logout, field-only technician scope guard, shared120-request/minute budget and actor/reason audit mechanisms are reused. Legacy APIs cannot bypass technician scope.

Scanning a tower QR mounts the permitted document collection, maintenance schedules/reminders, service records, warranties and related work/incidents in the existing field page. The label contains no document content or credentials. Associations retain their historical site; moving equipment does not automatically grant another tower's technician access to historical documents. An administrator must deliberately create/reclassify a permitted record for a new site when needed.

Per-document retention days default2555, with legal hold and audited soft deletion/restoration. Deleted documents are hidden from non-administrators and cannot be downloaded. The UI reports retention eligibility after deletion; legal hold suppresses eligibility. **Physical purge is not automatic or implemented here.** Metadata, historical revisions and immutable files remain available for backup/audit. Establish retention/legal requirements and a reviewed purge/reconciliation process before long-term operation.

## Internal reminders and explainable recommendations

Persisted internal reminders cover due/overdue schedules, open/missed occurrences, documented failed assets, warranty dates, repeated service actions and fresh existing tower findings (including power/battery/RF/temperature evidence when supplied). Schedule due/overdue thresholds are configurable; warranty/repeated-service thresholds use `MAINTENANCE_WARRANTY_WARNING_DAYS`, `MAINTENANCE_REPEAT_SERVICE_COUNT`, and `MAINTENANCE_SERVICE_HISTORY_DAYS` (defaults60/3/90).

Evidence includes schedule/work/asset/finding identifiers, observed/due dates, service counts, recommendation reason, suggested qualified investigation and confidence/uncertainty. Repeated inspection/service actions are not automatically called failures. Missing or stale telemetry does not become a healthy reading or future failure probability. Bounded evidence scans preserve existing reminders when incomplete rather than falsely declaring recovery. These internal records are integration points for a future explicitly approved NOC-96 notification policy; no external delivery is enabled.

## Backup, recovery and rollback

Database-only backups are insufficient for new documents. Preserve the matching PostgreSQL dump, private document store, configuration/scanner policy and volume ownership. Keep backups encrypted/off-host under the existing operational policy. For a clear controlled snapshot, stop upload traffic/worker and take the database dump first, then the immutable store; no physical file deletion runs concurrently in this milestone.

Use the existing isolated database backup/restore procedures. The new bundle helper accepts a database dump and document root and refuses to overwrite its output:

```
python -m app.maintenance_ops.archive backup --dump /private/database.dump --store /private/documents --bundle /private/maintenance.tar.gz
python -m app.maintenance_ops.archive restore --bundle /private/maintenance.tar.gz --target /private/NEW_recovery
```

Restore creates a NEW private directory containing `database.dump`, its checksum and `blobs/`. It validates member paths/types, sizes and SHA-256, never using unrestricted tar extraction. Restore the dump into a NEW isolated `restore_NAME` database with the existing restore script; point an isolated matching application at the restored `blobs/` root after provisioning permissions. Verify every version checksum and authorization before use. Do not replace a production/source database or mount a recovery root into a live application. Failed restores retain the new partial target for diagnosis; retry with a different new path.

For rollback, retain the failed database/store and run the matching old application against a tested pre-upgrade database backup in isolation. Empty-module0021→0020→0021 and schema-drift checks are development tests; downgrade deletes maintenance metadata and is not a populated-data rollback strategy. New immutable files are not automatically removed by downgrade.

## Verification and limits

Exact final test counts, commit and GitHub Actions results are recorded in the draft PR. Synthetic acceptance exercises recurrence/date clamping, unique generation/rollback, calendar scope, service/financial restrictions, document versions/checksums/approval/quarantine, local Unix-socket scanner protocol, invalid PDF/image/text, QR permissions, archive traversal rejection and existing inventory/field regressions. Browser acceptance covers administrator calendar/report/version approval and mobile QR document access with private records/costs hidden. Real phone scanning, production servers, real equipment, production Powercode, external messages and a real malicious document corpus are not tested.

Local PostgreSQL stored-record benchmark,40 POPs/400 synthetic assets:

| Documents and service records each | Insert ms |50-doc page ms|Title search ms|Bounded calendar ms|Aggregate service report ms|Generate40 occurrences ms|Peak process RSS KiB|
|---:|---:|---:|---:|---:|---:|---:|---:|
|1000|1303.52|3.44|5.86|86.11|120.67|434.96|87180|
|10000|8990.69|1.79|10.10|57.05|110.01|241.30|112136|

Sixteen read statements across measured operations; zero external calls. Calendar/detail bounds are explicitly reported; full report aggregates retain the entire filtered scope. These are single-process metadata/service queries, not filesystem throughput, malware-scanner throughput, authenticated HTTP/mobile latency or production sizing.

Remaining prerequisites: review/integrate the full PR stack; validate approved-server installation and paired recovery; configure private volume capacity/encryption/backups; validate/provision real ClamAV and signatures; approve document classifications/retention; review technician assignments, inspection safety and recurrence/DST policy; and validate actual field/mobile operation. No automatic dispatch, purchase, network change, incident closure or RF remediation is added. RF self-healing remains simulation-only. Synthetic development acceptance is not production approval.
