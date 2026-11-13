# NOC-86 RF monitoring foundation and RouterOS support

This increment extends draft PR #90 on the existing requested NOC-86 branch;
its NOC-85 parent remains #89 at 07ac49c, preserving the full stack. The previous
NOC-86 CI #434 passed before this increment. No branch was replaced or merged.
See NOC-86-read-only-integrations.md for UISP/MikroTik transport, authorization,
secret management, import reconciliation and rollout requirements.

## Normalized RF observations

`RFMeasurements` and `NormalizedRFTelemetry` provide a closed, versioned model:
signal/RSSI dBm, SNR dB, noise floor dBm, frequency MHz, channel width MHz,
link capacity Mbps and reported radio status. CCQ, PHY rates, temperature and
uptime remain optional. Measurements are finite and range-checked. Booleans,
unknown units, ambiguous fields and malformed values are not converted into
measurements. No SNR/noise/capacity is inferred from other metrics.

Missing numeric values are null, not zero. Availability explicitly distinguishes
missing, invalid and unsupported fields. Radio status is a separate reported
observation: up, down, degraded, unknown, or not supplied. It is not substituted
for management/TCP reachability or treated as confirmed physical root cause.

Profiles carry explicit vendor, version, source kind, UTC measurement timestamp
and receipt timestamp. Missing/naive source timestamps are rejected; inventory
lastSeen is never silently reused as an RF measurement timestamp. Future data
is exposed as future by freshness evaluation, not treated as current. Legacy
unit-tagged samples remain readable with unknown vendor, a legacy provenance
marker, and no invented receipt/last-poll timestamp.

## Prepared vendor collectors and their limits

| Vendor | Profile | Supported synthetic input/units | Live transport readiness |
| --- | --- | --- | --- |
| Ubiquiti | ubiquiti-uisp-explicit-v1 | radio.rssi_dbm, snr_db, noise_floor_dbm, frequency_mhz, channel_width_mhz, link_capacity_mbps, radio_status | Explicit-unit UISP snapshots can use the existing approved GET-only client; no direct device endpoints |
| Cambium | cambium-unit-export-v1 | rf.rssiDbm/snrDb/noiseFloorDbm, frequencyHz, channelWidthHz, capacityBps, linkState | Export normalization prepared; direct transport unconfigured |
| Racom | racom-unit-export-v1 | radio.rxLevelDbm/snrDb/noiseDbm, frequencyKhz, bandwidthKhz, capacityKbps, state | Export normalization prepared; direct transport unconfigured |
| Aviat | aviat-unit-export-v1 | link.rxPowerDbm/snrDb/noiseFloorDbm, frequencyGhz, channelWidthMhz, capacityMbps, operationalStatus | Export normalization prepared; direct transport unconfigured |

These are **explicit synthetic export contracts**, not assertions that every
vendor exposes these JSON keys or an HTTP API. Unit conversion is selected by
profile and field name, never numeric magnitude. Cambium/Racom/Aviat direct wire
collection fails closed as unsupported. No guessed REST paths, SNMP OIDs, SSH,
login POST, settings change or automated remediation has been added. Ubiquiti
UISP collection still requires an enabled/admin-approved exact-origin ReadClient
and a real RF sampled_at field; it does not create a network connection merely
because a profile exists. Collectors are not scheduled or enabled automatically.

Before real devices: obtain sanitized model/firmware-specific fixtures, confirm
read-only transport/account permissions, validate metric units and timestamp
semantics, configure approved TLS/endpoint/egress policy, and review measurement
accuracy against trusted read-only observations. The current tests use mocks and
synthetic values only. No live vendor, RF, TLS-handshake or credential-scope
acceptance is claimed. SNMP/NETCONF or vendor-specific transport work remains
separate where a device family does not provide the supported UISP/export contract.

## Persistence, diagnostics and dashboard

Vendor observations stage into existing telemetry_samples as rf_observation_v1,
with the compatible rf_metrics_v1 numeric projection when numeric measurements
exist. No new database migration is needed beyond the existing 0007 head.
Staging owns neither commit nor polling. It checks inventory vendor, serializes
concurrent stages per device and deduplicates equivalent timestamp/profile data;
retries with later receipt times keep the first receipt. Conflicting data at the
same timestamp is rejected. A status-only record never makes legacy numeric RF
diagnostics falsely fresh. Unknown/invalid raw fields and credentials do not
appear in the normalized model or operator response.

Existing RF diagnostics, trend/degradation rules, freshness and simulation-only
self-healing remain intact. Link capacity is a reported RF capacity, not inferred
from PHY rates or TCP latency, and is not written back to network equipment.

Authenticated operator/admin endpoints:

- `GET /api/rf/vendor-profiles`: prepared profiles, disabled direct transports,
  read-only state and execution_enabled=false; never polls equipment.
- `GET /api/devices/{id}/rf-observation`: latest local normalized or explicit
  legacy observation, availability, provenance and freshness. Unknown/invalid
  payloads return safe reasons without returning raw input or credentials.

The main dashboard includes integration health and last complete successful sync;
RF Diagnostics adds signal/noise/SNR/frequency/width/capacity/status and last
successful ingest with source/freshness context. Legacy receipt time is labelled
not recorded. Last successful ingestion is not claimed to be a live poll. Stale
or missing data is clearly distinguished from link health. Profile display has
no activation or network-equipment controls.

## RouterOS 7 and RouterOS 6

The read-only MikroTik collector supports the existing RouterOS **7 stable 7.x
REST contract**: GET identity/resource/interface/health/OSPF neighbor endpoints.
Synthetic 7.17 fixtures exercise CPU, exact memory/counter values, uptime,
temperature where supported, OSPF state and interface error deltas/reset handling.
Read-only permission and sensor availability still vary by model/version; the
application allowlist does not prove the device account's permissions.

RouterOS **6 has no equivalent REST API**. A reported 6.x version is rejected
as unsupported after identity/resource reads. There is no fallback to binary
API (8728/8729), SNMP, SSH or CLI, and no scan for those services. A future ROS6
collector would need a separately reviewed, TLS-protected read-only API or
read-only SNMP profile, exact command/OID allowlists and synthetic regression
fixtures. ROS7 prerelease and future major-version contracts likewise need
explicit validation. Missing/malformed version data remains invalid_response,
not a fabricated supported version. No RouterOS configuration commands are run.

## Reproduction and evidence

```bash
cd backend
# Supply only disposable test SECRET_KEY/ADMIN_PASSWORD/DATABASE_URL and an
# explicit local RF_INTEGRATION_TEST_DATABASE_URL, as in the NOC-84 guide.
python -m pytest -q -rs
python -m pytest -q tests/test_vendor_rf_foundation.py tests/test_vendor_rf_postgres.py
```

Unit/real-PostgreSQL tests cover all four profiles, unit conversions, missing and
invalid values, secret-field exclusion, strict schemas, source timestamps, unknown
vendor rejection, blocked unverified transports, guarded mocked UISP reads,
persistence/deduplication/conflicts, status-only observations, stale/future data,
legacy compatibility, API permissions and the ROS6 no-fallback guard.

CI retains NOC-83/84/85/86 backend, frontend, migration/schema-drift, Compose,
RF/PostgreSQL, worker-scale, Ubuntu 24.04 runtime/recovery and WISP browser gates.
The offline-only `python -m app.rf_vendor_rehearsal` stages four vendor-shaped
observations into the existing synthetic WISP fixture without device requests.
The additional browser test verifies all four profiles' units and provenance.
The helper requires offline synthetic staging and exclusively synthetic inventory;
it changes telemetry in that disposable database, never RF settings.

Exact final SHA, test counts and CI run are recorded in the updated draft PR.
All previous real-device, production egress, credential scope, historical secret
retention, immutable artifact, bare-VM and monitoring-SLA release gates remain.
