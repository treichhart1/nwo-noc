# Wave operator guide and exact acceptance inputs

Native collection is **BLOCKED**. No new live enable switch is supplied. Existing offline mode/egress/authPriv/TLS/approval gates remain authoritative. Do not configure community-string SNMP, certificate bypass, web scraping, network discovery, radio SET operations or firmware changes to work around missing evidence.

## Acquisition checklist (one package per exact model/build)

1. Record model/SKU/hardware revision, installed firmware full build, UISP version, role (PTP/AP/subscriber), approved inventory IDs/POP/site, existing NetworkLink endpoints and optional canonical AP association. No private credentials in fixtures or tickets.
2. Obtain vendor-origin firmware-specific `ui-mib.zip` through an approved vendor/support channel. Supply source URL, acquisition date, vendor checksum/signature when available and locally computed `sha256sum ui-mib.zip`. A local hash identifies bytes; it does not establish vendor authenticity.
3. Include actual files **UBNT-MIB.txt**, **UI-AF60-MIB.txt**, **UI-AF60-MIB-REV1.txt** only where supplied by that firmware. These are acquisition candidates, not an assertion every archive contains them. Include every imported dependency (exact module/version/file). Preserve archive filenames, MODULE-IDENTITY LAST-UPDATED/REVISION and import graph. Do not rename or combine incompatible revisions.
4. Supply exact per-field OBJECT-TYPE, units, scale, sentinel values, enums, read-only access and complete instance OID suffix; table INDEX, row identity, pagination/completeness and discontinuity semantics. An enterprise root is not an operational field mapping.
5. Supply official proof of SNMPv3 authPriv support, allowed auth/encryption algorithms, firmware/version behavior and GET/table capabilities. Existing transport uses SHA256/AES128 and bounded GET only; a different or unsupported security contract requires review, not downgrade. If using UISP, export the exact console Network `/api-docs/` schema/read-only permissions and sanitized GET fixtures over approved verified HTTPS.
6. Fixtures must distinguish local/remote, AP/subscriber, primary60GHz/backup5GHz, actual capacity/actual traffic/cumulative byte counters, resets, uptime, station removal versus partial snapshot, unavailable fields and expired source timestamps. Never include credentials, customer identities, topology coordinates or production addresses. Include timeout/authentication/incompatible-firmware/malformed/empty/incomplete cases.

All RF fields (signal/SNR, modulation, directional capacity/traffic, frequency/width, Ethernet, packet loss, temperature, station counts, backup signal/capacity) remain unavailable on actual equipment until the exact contract validates them. Do not derive internet service capacity from wireless capacity or speed from modulation alone.

## Isolated synthetic use

Use an independently authorized staging install with all external integrations disabled. In the existing private inventory create enabled `source=synthetic`, `vendor=Ubiquiti`, exact catalog `model` devices. Reuse canonical NetworkLink endpoints for PTP. Operator GET `/api/rf/wave/compatibility` and `/api/rf/wave/devices/{id}/history?limit=120` require authenticated private access. Admin POST `/api/rf/wave/devices/{id}/synthetic` is available **only offline/staging**; use the private authenticated client, not tokens in shell commands/history.

Example body (replace timestamp with current UTC within one hour, and use matching synthetic AP inventory):

```json
{"schema_version":"wave_evidence_v1","source_kind":"synthetic_export","model":"Wave AP","firmware":"synthetic-v1","observed_at":"2026-11-28T00:00:00Z","role":"ap","availability":"up","metrics":{"rssi_dbm":-65,"tx_capacity_mbps":1000,"tx_throughput_mbps":20,"uptime_seconds":600},"station_table_complete":false,"stations":[],"primary_state":"up","active_path":"primary"}
```

Missing metrics remain null/unavailable. No hardcoded RF specifications or live firmware semantics are implied. UI: existing RF diagnostics → Wave/60GHz monitoring readiness. Capacity and throughput are labeled separately. Invalid/stale evidence stays unknown. Backup operation is operational degradation, never confirmed outage. Three consecutive primary-UP synthetic samples may establish **synthetic** recovery evidence; they do not publish incidents/notifications or resolve actual Down Detector records. Partial station tables never disconnect subscribers. Membership absence is not outage. Counter-derived Mbps needs increasing timestamps/uptime, no restart/reset, and bounded gaps. Weather correlation requires independent source/time evidence; causation stays unverified.

## Separately authorized live acceptance (not performed)

Only after reviewed vendor contracts and a supported collector exist: approve one device, read-only credentials encrypted through existing vault, explicit address/origin allowlist, verified CA/TLS or authPriv, initial300s interval/concurrency1 and bounded timeouts. Compare each collected field against vendor UI/counters, test authentication failure/timeouts/stale data, AP versus subscriber fault boundaries, upstream dependencies and three-sample recovery. Reuse durable scheduler/receipts; never register duplicate polling. Verify existing tower/capacity/history/Grafana projections before enabling incident consumers. No SMS/Slack/public publication or remediation is authorized by collector acceptance.

## Troubleshooting and rollback

403 staging: verify private admin role, `offline_mode` and staging environment; never weaken guards.409 evidence conflict: inspect canonical IDs/model/timestamp/link and conflicting replay; do not overwrite history. Missing history means unavailable.503 storage: restore database connectivity, retain evidence and retry only identical source data. Disable use of synthetic import; no worker was installed or scheduler modified. Existing backup/restore retains canonical telemetry JSON automatically. No migration downgrade, record deletion or radio configuration rollback is needed. Retain historical data and audit logs.
