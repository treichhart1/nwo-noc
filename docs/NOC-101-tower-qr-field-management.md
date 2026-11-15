# NOC-101: tower QR and field technician workflows

## Dependency strategy

This branch builds directly on unmerged NOC-100 draft PR #104 at `552287c31e7951c3dbd1b50ddfbe14309ab54761`. Its CI #518 passed all eight jobs. Preserve the entire ancestor stack: NOC-83 through NOC-94, then #99 NOC-95 → #100 NOC-96 → #101 NOC-97 → #102 NOC-98 → #103 NOC-99 → #104 NOC-100 → this NOC-101 PR. Parent PRs must be reviewed/integrated before children. Nothing was merged, closed or deployed.

The field UI is part of the existing React application. Existing PostgreSQL Site/Device/User/Incident, tower health and equipment identities are reused. There is no separate technician application, equipment inventory, authentication provider or device configuration engine.

## QR identifiers and printing

`field_qr_labels` holds a random UUID identifier and exactly one existing Site or equipment Asset foreign key. A partial unique index permits one current label per target; revoked generations remain stored. Tower names are not keys. Rename, label regeneration and revocation do not change work-order/inspection references or delete history. NOC-100's permanent authenticated asset record URL remains available; the new revocable printed labels are a separate reference layer.

An administrator provisions all current tower labels, or generates/regenerates one selected tower/equipment label. Newly added towers require another provision action or individual label generation. Provisioning is bounded to 1,000 towers; larger catalogues use individual label operations. No labels or device records are generated from network scanning.

Configure `FIELD_PUBLIC_BASE_URL=https://approved-noc-host.example` to the operator-approved HTTPS origin (no path, credentials, query, fragment, alternate port or Host-header inference). It is blank by default; printing fails until configured. Disposable installer rehearsals use `https://noc.example.invalid`. This is a synthetic label origin, not a deployed endpoint. The server generates an actual QR SVG using qrcode 8.2. The printable page contains the opaque QR URL, target kind and stable target ID; it contains no credentials, private management IPs or customer data. Use **Prepare printable label**, verify the target, then **Print this label**. Print CSS hides the rest of the UI. Verify actual phone-camera decoding and label durability in a separately approved environment before field use.

Scanning `/?qr=<uuid>` preserves the target through normal NOC login and resolves it using the authenticated `/api/field/qr/…` endpoint. Revoked and unknown labels fail closed. Knowledge of the identifier does not grant access.

## Permissions and assignment scope

- Administrators manage labels, template versions, work creation, scheduling, technician roles/assignments and site grants. They can inspect and document all field work.
- Active `technician` users see their assigned work orders. An open assignment grants temporary tower context; an explicit active site grant permits continued site access independently of work completion. Completing/cancelling the last open assignment removes that temporary broad site access. Assigned historical work orders and their original asset records remain accessible for history. Grant revocation does not erase an independently active work assignment.
- Technicians can update only their assigned work: inspections, notes, photographs, documented replacements and parts installation. They cannot create/reassign/schedule work, manage templates/labels/grants or access financial data.
- Existing `operator` users have read-only **field** API access. Their established permissions in other NOC modules are unchanged. Viewer/unknown roles cannot access the field module.

A central authentication guard prevents the new technician role from bypassing field scope through legacy inventory, customer, IPAM or general monitoring APIs. Only field APIs and session identity/logout are available to that role. All checks use the existing active User and durable JWT revocation mechanisms. Administrator **Enable field-only technician** converts an existing active non-administrator NOC user, with audit history; it does not create a password or external identity. Review the existing user ID carefully because this replaces their former general NOC role. User onboarding and password provisioning remain existing operator responsibilities.

The shared PostgreSQL request budget limits each user to 120 equipment/field requests per minute. Requests require authentication; role and object-level denials are audited. Audit entries and work events contain operator identity and documented reasons; financial values and credentials are excluded. Do not enter secrets in notes.

## Work orders and technician dashboard

Open **Field Management** in the NOC navigation, or `/?field=home`. Technician login defaults to this view. Deep links retain the selected work order. At phone widths the dashboard displays assigned work cards, tower identity/location, supported health evidence and freshness, active alarms, installed equipment, service history, related incidents and maintenance timeline.

Work orders retain a unique immutable number, site, optional asset/incident/tower finding/template, priority, technician, creation/scheduled/completion timestamps, required equipment, completion notes, follow-up and revision. Administrators can prepare work from a tower finding or enter a related incident. Association validation checks the existing tower, device or backhaul endpoints; no automatic dispatch or incident closure occurs.

Lifecycle:

- `open` → assigned, scheduled or cancelled.
- `assigned` → scheduled, in_progress, blocked or cancelled.
- `scheduled` → assigned, in_progress, blocked or cancelled.
- `in_progress` → waiting_for_parts, blocked, completed or cancelled.
- `waiting_for_parts` → in_progress, blocked or cancelled.
- `blocked` → assigned, scheduled, in_progress or cancelled.
- Completed/cancelled orders are immutable through field mutation workflows; create follow-up work rather than rewriting history.

Status changes require expected revisions and meaningful work notes. Scheduling requires a technician and timezone-aware time. Blocked/waiting states require follow-up. Completion requires the assigned inspection, if configured, and explicit follow-up when structural/electrical failures were observed. Work-order events and inspection snapshots are append-only at the database layer. Lists and timelines use 50-row offset pages; older inspection detail is available by authenticated inspection ID. Work detail shows the latest 50 inspections, and legacy equipment history remains bounded to the existing inventory views.

## Inspection templates and qualified review

The standard template contains 23 configurable checks covering structure, corrosion, hardware, antenna mounting, grounding, weatherproofing, AC/DC/UPS/batteries, surge protection, ventilation, wiring, routers/switches/radios, Ethernet/fiber, labels/cabinet organization, water/animal/security/access concerns and obstructions. Administrators can edit checklist JSON and save a new version; previous versions and assigned work remain intact.

Each result is explicitly pass/fail/not_applicable, with optional notes. Missing/duplicate/unknown answers are rejected; values never default to pass. Submitted inspection time must be timezone-aware and nonfuture. Every inspection preserves template version/items, results, notes, technician and collection/recording times. Structural or power-group failures flag **qualified review required**, and work completion requires follow-up. Passing a visual checklist is not a structural/electrical safety certification.

Asset inspections also call NOC-100's existing maintenance service and retain its maintenance ID, rather than creating another asset inventory. Tower-only inspections use the same work timeline and template semantics. The schema provides stable references for future NOC-102 document/checklist expansion.

## Replacement and parts procedures

1. Scan the old equipment label and verify the existing asset/location.
2. Open/start the administrator-assigned work order.
3. Approve NOC documentation changes, record a meaningful reason and select a verified existing spare.
4. Record removal and installation details, removed-stock location and disposition.
5. Optionally complete the work order in the same transaction. Required inspections/follow-up still apply.

This calls NOC-100 `replace_asset`: old physical binding ends, old asset moves to removed stock, replacement is installed at the existing tower and receives a historical relationship. The shared logical Device/IPAM association is preserved when requested. Old equipment history remains; replacement telemetry needs a fresh post-binding sample. A failure in completion rolls back inventory movements and field events together. Disposition documents intended repair/return/retirement/scrap/review; it is not proof that physical disposal occurred.

The technician spare picker exposes bounded verified available/reserved assets, excluding spares assigned to another technician. It does not infer model/firmware compatibility; the operator must verify suitability. Removed-stock locations are existing inventory locations. Replacement never configures IP addresses, VLANs, routers, radios or monitoring endpoints.

For bulk parts, use **Document consumed bulk parts**. Select existing stock balance, explicit quantity/unit and tower installation location. Technicians consume only their own technician-stock location; administrators can use warehouse stock. The existing inventory service performs revision-protected quantity transfer and records its movement ID on the work timeline. Inspection notes alone never deduct stock, preventing double consumption.

## Photo/report storage

Field attachments use the existing NOC-100 content validator and the same PostgreSQL BYTEA/metadata pattern: SHA-256, uploader, timestamp, work-order and optional inspection reference. Maximum 512 KiB/file and 20 files/order. PNG structure/CRC, JPEG signatures and plain UTF-8 text are validated; PDF/SVG/executables are unsupported. General reports can be plain text. There is no separate document-management system.

Retrieval checks the current work assignment/role and returns an attachment with no-store, nosniff and sandbox policy. No unauthenticated file URLs exist. Attachments are **unscanned**; JPEG signature checking is not full decoding or malware detection. Do not upload credentials, billing/financial records or sensitive customer documents. Use NOC-100's restricted financial workflow for administrator-only financial attachments. A managed scanning, retention, EXIF/privacy and document policy is required before sensitive field use.

## Online-first reliability and drafts

This milestone does **not** provide full offline synchronization, a service worker or offline data caches. Browser online/offline state is displayed, and offline submissions are disabled. Browser online status alone does not prove server reachability; requests have a 10-second client timeout. Server/database failure leaves an actionable error and draft/retry envelope.

Inspection drafts are optionally stored in `sessionStorage`, namespaced by signed-in user and work ID, surviving reload in the same tab. They end when cleared, on field logout or when the browser session ends. They are not encrypted and must not contain secrets; clear drafts on shared devices. Attachments are not available for offline browsing. A pending upload envelope contains bounded file data until success/discard for retry safety. Storage quota failure is reported; this is not a guarantee against device/browser loss.

Every mutation has a UUID operation key and digest. A database receipt uniquely scopes the operation to actor/key; an exact retry returns its original response. Reusing a key with different contents fails. Authorization is checked again before replay, so reassignment cannot be bypassed using an old key. A browser uncertain-result envelope retains exact submitted data and offers explicit retry/discard. After discard, refresh server history before submitting a new operation because the old one may have committed. Expected revisions prevent stale edits; transactions and consistent advisory-lock order protect field/inventory changes together.

Chromium tests actually ran offline/online transitions, reload draft recovery and a deliberately lost post-commit response followed by exact replay without duplicate inspection. This establishes tested online-first recovery, not full offline support or real mobile-WAN reliability.

## Schema, installation and recovery

Additive revision `0020_field_technician_workflows`, parent `0019_equipment_inventory`, creates eight field tables and indexes. It extends the existing inventory technician-identity guard to accept active field technicians while retaining every other inventory check. No earlier migration is modified. Application version remains 3.2.1-rc.3.

Back up PostgreSQL and configuration with the existing release procedures before `alembic upgrade head`. Configure the approved HTTPS origin before printing labels. Never point development commands at production. Empty-module 0020→0019→0020 and Alembic drift checks passed. Downgrade removes field data and restores the older technician guard; it is not a populated-data rollback mechanism. For rollback, preserve the failed database and restore a pre-upgrade backup into a new isolated database with the matching old application commit. Preserve attachments, labels, receipts and inspection snapshots in backups; do not overwrite a live database.

## Tests and measured performance

Local targeted field/inventory regression: **109 passed** (49 field,60 inventory). Field Chromium acceptance: **3 passed**, covering phone rendering/authenticated QR, draft/reconnect/lost-response replay, inspection/report completion, administrator SVG printing/revocation, and scanned equipment replacement/atomic work closure. Full local regression: **1,229 passed**, two existing deprecation warnings,344.30 seconds. Final GitHub Actions counts are recorded in the draft PR. A separate populated restore matched all **94 public tables**, including labels, receipts, inspection snapshots, field/equipment attachments and binding history.

PostgreSQL 16, rollback-only synthetic workload,40 POPs:

| Work orders | Insert ms | Authorized QR lookup ms |50-order page ms|Work detail ms|Tower context ms|Statements measured|Peak process RSS KiB|
|---:|---:|---:|---:|---:|---:|---:|---:|
|1000|198.67|11.16|2.85|6.01|16.74|11|70948|
|10000|1124.11|3.26|3.80|2.51|6.70|11|92452|

Single-process stored-record reads, not physical camera scanning, authenticated HTTP/WAN latency or production sizing. External calls: zero. Inventory/device scale coverage remains in NOC-100 and the existing 250/500/1000-device acceptance gates. Reproduce only with staging/offline/synthetic configuration and an empty field fixture database using `python -m app.field.benchmark`.

## Remaining limits and activation prerequisites

No production server, live equipment, real customer data, external messages or configuration changes were used. No automatic dispatch, network remediation, configuration deployment or physical safety decision is implemented. RF self-healing remains simulation-only.

Before controlled use: integrate/review the full stacked PR chain; rehearse installation/backup/restore on the approved server; establish HTTPS/DNS; provision least-privilege technician accounts and explicit assignments/grants; reconcile asset labels/spares/locations; have qualified staff approve inspection templates and safety escalation; validate phone-camera decoding/printing; and establish attachment scanning, retention, shared-device and access-revocation policies. Full offline operation and a general document-management platform remain outside this milestone. Synthetic acceptance is not production approval.
