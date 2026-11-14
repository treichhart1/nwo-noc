# NOC-100: equipment inventory and lifecycle management

## Baseline and integration order

Based on NOC-99 draft PR #103, commit `af5a97cc94dedbb7bdb22a71ffdb5eb204cfdd15`; its CI #516 passed all eight jobs. No upstream draft was assumed merged. Preserve the existing ancestor chain, integrating each parent before its child: NOC-83 through NOC-94, then #99 (NOC-95), #100 (NOC-96), #101 (NOC-97), #102 (NOC-98), #103 (NOC-99), and this NOC-100 branch. No PR was merged during development. Application version remains 3.2.1-rc.3; schema head becomes `0019_equipment_inventory`.

## Architecture and identifiers

The equipment module extends existing Site, Device, User, customer-service, IPAM and tower-health identities. It does not create another monitored-device inventory. Seventeen additive PostgreSQL tables represent categories, manufacturers, products, locations, assets, historical device bindings, movements, bulk balances/movements, maintenance, attachments, financial records, actual costs, stock policies, imports, request budgets, and restricted financial history.

Categories and manufacturers are data, not fixed enums: register MikroTik, Ubiquiti, Cambium, Racom, Aviat, fiber, power, passive infrastructure and future products without a schema change. Multiple physical assets share a product. Product tracking mode and stock unit are immutable. Manufacturer-scoped, case-insensitive serial uniqueness avoids conflating unrelated manufacturers; supplied physical MAC addresses and asset tags are unique. Unknown serials and telemetry remain unknown. Product model identity is not a claim of validated firmware compatibility.

Stable random UUID asset identifiers resolve to `/?asset=<uuid>` and an authenticated asset API. UUIDs are references, not bearer credentials. Search and filters cover tags/serials/MACs/models, site, manufacturer, category, product, location and lifecycle. Pages are bounded; catalogue and stock pages are separately pageable.

## Lifecycle, warehouses and concurrency

States: ordered, received, available, reserved, installed, in_service, under_maintenance, failed, removed, returned, retired. The service and PostgreSQL trigger enforce valid transitions. Available/reserved individual equipment needs a serviceable documented condition; unknown is not a verified spare. Installation requires an existing tower/customer location matching the associated monitored device. Removing, returning or retiring clears the current device binding, retaining its history. Installed equipment must be removed before stock transfer.

Warehouse, office, vehicle, technician stock, tower, customer premises, repair, returned and retired locations are supported. Serialized asset movements are separate from decimal bulk-material balances. Bulk units are explicit, `each` is integral, and no negative stock is permitted. Receive/transfer/install/remove/reserve/release/repair/retire/scrap operations retain source, destination, actor and reason. Immutable event keys make exact bulk retries idempotent; changed retries fail. Expected revisions plus transaction-scoped advisory locks prevent conflicting transfers. Failed transactions roll back balances and history together.

Replacement is an atomic old/new workflow. The old asset moves to removed stock; a verified spare takes its location and optionally the same canonical Device identity. Binding windows are non-overlapping and historical bindings immutable. Existing IPAM interfaces, assignments and customer references remain on that shared logical Device. No IPAM, router, radio or VLAN configuration is deployed.

## Monitoring, tower and customer integration

Asset detail joins existing tower health, Device, IPAM interfaces/assignments, incident and maintenance-finding records. Current telemetry must be fresh and collected after the current physical binding began. Removed assets do not inherit the replacement's current telemetry or IP addresses. Inventory status alone is not a successful monitoring check; unsupported, missing, stale and pre-binding measurements do not become healthy readings. Shared logical-device incidents are displayed with physical cause unverified. Passive equipment remains unmonitored unless an existing supported adapter supplies evidence.

Tower location and Device identifiers are reused. Customer-premises locations can reference existing customer services; billing data remains read-only. No new external collectors, network scanning, notifications or outbound integrations are enabled by this module. Existing RF self-healing remains simulation-only.

## Maintenance and attachments

Inspections record date, technician, work, condition, fault, repair, documented parts, follow-up, status and optional existing tower finding. Completion is audited with expected-status protection; completed entries are immutable. Parts are documentation, not automatic stock consumption: record a separate explicit stock movement when appropriate. These records and stable IDs are extension points for future QR/checklist modules.

Attachments are authenticated, download-only PostgreSQL BYTEA records with SHA-256 and metadata, limited to five files per maintenance entry and 512 KiB each. PNG structure/CRC and JPEG signatures are checked; plain text rejects active content, controls and obvious credential patterns. PDF/SVG/executables are unsupported. Files are **unscanned**, without OCR or antivirus guarantees. Downloads use attachment disposition, nosniff, sandbox CSP and no-store. Financial attachments require administrator classification and access; operators cannot retrieve them. Do not upload secrets or sensitive documents pending a managed malware-scanning/retention policy.

## Costs and spare planning

Only administrators can access monetary fields, financial exports or private financial change history. General audit events omit amounts. Costs use exact decimals and explicit currencies; no exchange conversion or invented missing prices. Purchase basis, actual repairs/bulk receipts and replacement/labour estimates are reported separately. Repair location attribution is explicit rather than inferred from the asset's present location. Tower installed purchase basis is not a complete historical valuation.

Depreciation is optional documentation: explicit purchase price/date, currency, straight-line monthly method, useful life and salvage value are all required. Whole calendar months are used; book value is withheld before purchase or when assumptions are absent. No depreciation or FIFO assumption is invented.

Configured product/category stock policies include location, unit, minimum and warranty warning horizon. Recommendations cite counted verified spares/bulk quantities, critical models without spares, documented retirement dates, warranty dates or repeated documented failures. They propose technician/procurement review, not failure probabilities or automatic orders.

## Imports, exports and security

CSV preview validates at most 300 rows/256,000 characters, required schema, stable identifiers, existing catalogue/location references, duplicates and financial privileges. Preview performs rollback-only validation. Explicit approval revalidates an unexpired batch and applies all rows transactionally; concurrent changes cause failure rather than silent overwrite. Existing-device previews expose invalid source serial/MAC values as missing with warnings. Historical device bindings require operator replacement/reconciliation review, preventing duplicate physical identities. Bulk inventory uses its own transaction workflow, not serialized CSV imports.

Exports are authenticated, paginated to 1,000 rows, neutralize spreadsheet formulas and exclude monetary columns unless explicitly requested by an administrator. Operators cannot see monetary import rows. Asset UUID/tag/product identity is immutable; revision-protected metadata edits record reason and actor. Inventory APIs require operator/admin roles, audit denied roles, and apply a PostgreSQL-backed 120-request/minute/user budget. Errors do not echo SQL, credentials or imported row contents. No external delivery or automatic equipment changes occur.

## Migration, installation and recovery

Back up PostgreSQL and application configuration using the existing Ubuntu release guide before upgrade. Run `alembic upgrade head` from `backend` with the intended isolated/configured DATABASE_URL. Revision 0019 adds only equipment objects; existing revisions are unchanged. `alembic check` must report no drift. Existing installer, offline Compose restrictions, authentication and monitoring recovery remain in force.

A downgrade removes equipment records: do not use it as routine production rollback. Prefer restoring a tested complete pre-upgrade backup into a separate database and running the matching application commit, preserving the failed database and attachments for recovery. The empty equipment-schema 0019→0018→0019 round trip was tested; this is not evidence that populated equipment data survives a downgrade. Never point development rehearsal at production databases.

## Verification and measured performance

Local PostgreSQL-backed full regression: **1,180 passed**, two pre-existing deprecation warnings, 335.08 seconds. This includes **60 equipment tests**, covering identifiers, lifecycle, bulk transactions, concurrent transfers, rollback, replacement/IPAM bindings, monitoring freshness, import reconciliation, financial isolation, attachment authorization, native guards and request limits. Equipment browser acceptance: **1 passed**, including protected login, inspection, technical attachment and replacement preserving IPAM. TypeScript/Vite production build passed. Alembic drift check and empty equipment migration round trip passed. Final GitHub Actions results are recorded in the draft PR.

Synthetic PostgreSQL 16 workload, 40 POPs, 400 monitored devices, 101 models and 40 bulk balances; all fixture changes rolled back. Timings are single-run observations, not production guarantees:

| Assets | Insert ms | Page 100 ms | Identity search ms | Dashboard with costs ms | Spare planning ms | Audited transfer ms | Process max RSS KiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 302.91 | 4.13 | 6.40 | 30.22 | 15.09 | 12.87 | 86,724 |
| 10,000 | 2,137.54 | 3.94 | 20.76 | 24.08 | 15.27 | 6.63 | 124,080 |

Measured read operations used 22 SELECTs; external connections: zero. Inserts use synthetic batch fixtures and native constraints; these are not end-to-end collector or concurrent production-load measurements. Reproduce with `python -m app.equipment.benchmark` only in explicitly staging/offline/synthetic mode against an empty equipment fixture database.

## Limitations and pilot prerequisites

No real hardware, firmware, production Powercode or notification delivery was tested. Physical per-port/component mounting and automatic serial discovery are outside this milestone. Detail history displays the latest 100 movements/maintenance records while retaining database history. Location selectors show a bounded initial catalogue; API pagination is available for larger deployments. No attachment malware scanning, automated retention/archive, full financial accounting/FX/FIFO, or automatic ordering is provided. Warranty/retirement/failure evidence depends on operator documentation.

Before any controlled pilot: review and integrate the full stacked PR chain, test backups and access roles on the approved server, configure catalogue/location identities, reconcile physical serials and Device bindings, define stock and financial policies, and validate each permitted adapter/firmware using separately approved read-only credentials and endpoint allowlists. **No-go for production deployment from synthetic evidence alone.** Network changes, unrestricted scans and automatic RF remediation remain prohibited.
