# Private monitoring, IPAM and cellular operation

No live deployment/connection is authorized by this milestone. All existing native/integration/scheduler activation gates remain disabled by default. Start only after separate server/equipment approval, exact model/build/contract verification and VPN-only/MFA access. Initial live acceptance should be one approved device/concurrency1/300s polling, no discovery scans, notifications or remediation.

## Native optional profiles

Reuse the existing private `MIKROTIK_NATIVE_CONFIG_FILE`, private credentials file and verified CA chain. Dedicated account requires only reviewed read/API access; deny configuration, scripts and sensitive properties. Explicitly restrict source IP on the actual router after independent operator authorization. Require API-SSL8729 and certificate/IP-SAN validation. Never plaintext8728/cert bypass. Existing native config, base REST isolation and scheduler device/address/pacing checks remain.

Optional private JSON additions (synthetic interface IDs; resolve actual approved IDs from inventory/read-only documentation):

```json
{"advanced_groups":["router","switch","tunnels","ipam"],"ethernet_monitor_ids":["*1"],"poe_monitor_ids":["*2"]}
```

Merge into an existing reviewed NativeConfig, not a standalone complete configuration. Empty lists are the defaults. No names, wildcard/find expressions or command parameters accepted for monitors. One-shot read-only optical/PoE only. Hardware misses/permission denial remain unavailable; inspect operator permissions/firmware before retrying. WireGuard is7-only; print projections exclude private/public/preshared keys. Handshake age threshold300s is advisory and does not establish tunnel UP. Unknown/unreachable remains unknown. PPP/L2TP sessions are not collected without a verified identity-minimizing contract.

Session timeout30s, individual socket timeout≤3s and total response≤2MiB remain. Durable advanced snapshot≤200kB and full envelope≤256KiB. If a large router exceeds limits, narrow the configured profiles and approved scope; never truncate or increase limits to hide data loss. Reuse scheduler leases/receipts, no parallel ad-hoc polling. Restart/recovery uses existing durable machinery.

## Native IPAM review

Existing private `/api/ipam` UI retains VRF-scoped assignment/conflict/reconciliation workflows. Native optional IPAM reads are observations only. Private admin POST `/api/cellular/ipam/native-preview` body:

```json
{"device_id":1,"sample_id":1,"vrf_id":1,"routing_domain":"main","reason":"Review native routing-domain observations"}
```

Use actual canonical IDs and a recent native receipt (<1hour). Preview performs no network I/O. Review interface VRF mapping, version/routing-table conventions and normalized evidence. Then POST the exact same body plus returned `preview_digest` to `/api/cellular/ipam/native-record`. Existing DiscoveryInput/Observation/DiscoveryBatch append history; canonical IPAM assignments remain unchanged until their own separate workflow. Missing VRF snapshots, wildcard/ambiguous memberships, multi-domain batches and records with no proven per-interface domain fail closed or are excluded. DHCP/lease/network observations remain in native receipts but are not assigned to a guessed VRF. The source protocol label `api6_recorded`/`api7_recorded` describes reviewed stored native receipts, not live discovery from this endpoint.

Existing reconciliation detects potential duplicate claims only within a selected VRF and shows documented discrepancies/staleness. It never calls intentional separate-VRF overlap a conflict or treats absent observations as unused/free. Some requested heuristics (unexpected DHCP allocation, gateway correctness, inferred loop/ownership) require reviewed baseline policy/evidence; do not invent correctness from a snapshot.

## Cellular records and mapping

Private existing RF diagnostics → advanced MikroTik/cellular panel. Private unified topology → cellular observation map. Independent NWO documented/estimated-cell/structure toggles; source/LTE-or-NR/MCC/MNC/age/confidence filters. No external tiles. Coordinates from source estimates do not become canonical towers/customer locations. Band/carrier names stay unavailable when source does not document them; NR layers can be empty because current import supports LTE only.

Administrator import uses local CSV, source metadata/license review, preview digest and explicit approval. API equivalents: POST `/api/cellular/datasets/preview`, then same input plus fingerprint to `/api/cellular/datasets/import`. GET `/api/cellular/datasets` for history; GET `/api/cellular/layers` accepts a regional bbox and max500 records, never unrestricted world queries. See DATASETS.md for exact fields/licensing. Do not put API tokens/keys in bodies or CSV.

Recorded evidence POST `/api/cellular/evidence` uses a closed CellularEvidence schema, canonical device/interface IDs, timestamp/source/reference, explicit technology and cell namespace. `synthetic` requires synthetic inventory. Operator-recorded sanitized evidence is labeled, not a native receipt. Do not submit IMSI/ICCID/IMEI, SIM data, subscriber contacts or arbitrary router output; unknown fields rejected. Actual source-minimized native LTE monitor remains blocked. RSRP/RSRQ/RSSI/SINR units are explicit. Do not equate carrier display name with MCC/MNC or LTE anchor with NSA NR identity. NR ARFCN/band/gNodeB values need explicit verified recorded evidence, not derivation.

POST `/api/cellular/correlate` takes typed `evidence` plus optional coordinates, and returns only candidate/ambiguous/unknown locations. POST `/api/cellular/qualification` takes latitude/longitude and`technology:lte|5g`; no service approval. Existing private signup qualification attaches the same advisory when LTE/5G coordinates are reviewed, preserving manual_review_required and encrypted applicant coordinates. No public internal layer exposure. No signal-based exact geolocation, carrier coverage invention, activation, Powercode account or customer message.

## Acceptance, rollback and recovery

For each actual device verify model, OS (not SwOS), full RouterOS/modem/transceiver build, per-field availability/units and identity relationships. Test TLS failure/read-only denial/timeouts, omitted sensors, counter restart, OSPF/route transitions, VRF overlap, handshake staleness and no key retrieval using separate authorization. Compare observed counters with independently trusted UI/equipment readings. No commands to force failures or failover are provided.

To stop optional collection, empty advanced groups/monitor IDs in the reviewed private config or disable the existing collector through existing approval controls. Do not delete history. No router rollback is needed because no writes exist. Disable imports/recording without deleting source data. Existing encrypted whole-DB/config/document/key backups include datasets and telemetry. Disposable restore probes now verify attribution and cellular history on the restored database and independent host. Physical offsite restore still requires operator testing. Migration36 is additive; never downgrade/delete imported history to recover an application problem—use existing guarded upgrade/recovery runbook.
