# NOC-87: Ubiquiti and Cambium RF telemetry

NOC-87 stacks on draft NOC-86 #90, commit
`3dd74d6c9c5855fc01fa6de5ff0d9f947c79970e` (CI #436 passed). Preserve the
NOC-83/84/85/86 dependency chain; review and merge upstream separately through
the normal release process. This work does not merge or deploy anything.

## Interface and capability scope

The implemented device transport is **SNMPv3 authPriv GET**, SHA-256 authentication
and AES-128 privacy, UDP port 161, numeric scalar OIDs only. SNMP GET is a defined
read operation (RFC 3416); USM/authentication/privacy follow the PySNMP implementation
and RFC 3414/3826/7860. No SET, GETNEXT, GETBULK, WALK, SSH, login POST, discovery,
radio configuration command, REST endpoint guess or protocol downgrade exists.
No vendor OIDs or live profiles ship as verified. A device that cannot support
this security combination is unsupported, even if it supports SNMPv1/v2c.

The adapters are versioned **capability contracts**, not assertions of actual
firmware compatibility. An administrator must obtain the vendor's model/firmware
MIB, identify exact scalar or authorized preselected table-instance OIDs and
numeric enumeration values, verify semantics and configure a read-only remote
SNMP view. Configure `mib_sha256` from the reviewed MIB and retain the mapping
and firmware evidence with the operator configuration. The mapping digest
includes every selected field/OID/unit/enumeration and is stored per sample.
A hash and approval flag record operator attestation; they do not independently
prove vendor provenance or account permissions.

| Profile | Prepared capabilities (only if mapped and returned) |
| --- | --- |
| airmax-ac-mib-v1 | local/remote RSSI, noise, SNR, TX/RX PHY rates, reported estimated capacity, frequency/width, CCQ, uptime/state, error/retry/disconnect counters, GPS state, TX/RX throughput |
| airmax-legacy-mib-v1 | local RSSI/noise/SNR, PHY rates, channel/CCQ, uptime/state and counters; remote RSSI, capacity, GPS and throughput are unsupported |
| epmp-4600-mib-v1 | local/remote RSSI, SNR/noise, modulation, MCS, PHY rates, throughput/capacity, channel/CCQ, health temperature/uptime/state, counters and authorized subscriber count |
| epmp-legacy-mib-v1 | local signal/SNR/noise, modulation, rates/capacity/channel/CCQ, uptime/state/counters and subscriber count; MCS, remote RSSI, temperature and throughput are unsupported |

Actual device capabilities may be a subset. Omit unsupported mappings; do not
map another metric into a missing field. Firmware changes require a reviewed new
mapping. Numeric wire enumerations map to fixed safe states. Arbitrary strings,
subscriber identities/MACs and unknown fields are not retained. Only explicitly
authorized subscriber **counts** are supported; subscriber table discovery and
individual client details remain outside this increment. PHY rate, actual
throughput and estimated capacity remain separate values. No SNR is inferred
from RSSI/noise; no link-quality equivalent is silently substituted for CCQ.

UISP GET-only inventory/RF integration from NOC-86 remains intact. It is not
claimed to expose every airMAX AC measurement or to support Cambium radios.
Racom/Aviat NOC-86 export profiles remain unchanged.

## Safe activation contract (future approved test environment only)

All defaults are disabled, and the worker does not schedule these collectors.
Offline mode rejects SNMP activation before configuration or secret files are
read. The isolated release validator rejects SNMP activation and config paths.
No production endpoint, credentials or OID map was used in development.

An explicit single-device entry point is implemented:

```bash
python -m app.radio_poll
```

It requires `RF_SNMP_ENABLED=true`, `INTEGRATIONS_ADMIN_APPROVED=true`,
`OFFLINE_MODE=false`, and `RF_SNMP_CONFIG_FILE` pointing to a private JSON file.
These are requirements for a later approved test; do not enable them as part of
offline release acceptance. No HTTP activation endpoint or equipment control is
provided. The inventory device must already exist, be enabled/authorized, match
the selected vendor and match the configured IP exactly.

Configuration structure (use your reviewed mapping, **not** fixture OIDs):

```json
{
  "device_id": 123,
  "credentials_file": "/run/secrets/radio-snmp.json",
  "policy": {
    "enabled": false,
    "admin_approved": false,
    "mapping_validated": false,
    "address": "192.0.2.87",
    "allowed_addresses": ["192.0.2.87"],
    "timeout_seconds": 2,
    "retries": 1,
    "min_interval_seconds": 5
  },
  "mapping": {
    "profile": "epmp-4600-mib-v1",
    "mib_sha256": "<64 lowercase hex characters from reviewed vendor MIB>",
    "fields": {}
  }
}
```

This intentionally incomplete example cannot activate a collector. `fields`
must contain 1–32 approved metric mappings of the form `{ "oid": "<numeric
instance OID>", "unit": "<explicit unit>", "states": {} }`. OID keys are canonical
metric names from the profile. Use `states` only for numeric radio/GPS/modulation
enumerations. Units include dBm/dB/percent, Hz/kHz/MHz, bits_per_second/kbps/Mbps,
seconds/centiseconds, celsius, count and enum; incompatible unit mappings fail.

The credentials JSON contains `username`, `auth_key` and `priv_key`. Both files
must be owned by the application UID and mode 0400/0600, regular files, not
symlinks, and at most 64 KiB. Mount secrets read-only; never put them in Git,
command-line arguments, browser storage, issue bodies or logs. Errors and audit
results are static codes. Redacted SecretStr values hold credentials in memory.
The CLI never prints exception text or a raw response. It exits nonzero on failed
or empty polls. Configuration/authorization failure may occur before a poll audit
record; application startup/configuration errors are reported separately.

Only explicitly allowlisted IPv4 literals are accepted; no DNS lookup or scan.
Loopback, link-local, multicast, unspecified and limited-broadcast addresses are
rejected. The library cannot infer routed subnet broadcast addresses: approve
unicast targets and enforce egress outside the application. Keep internal-only
Compose networking and public internet disabled. Real-device connectivity would
require a separately approved test egress design; no such change is included.

One GET holds at most 32 varbinds. Per-request timeout is 0.2–3 seconds, 0–2
retries for timeout/unavailability, exponential bounded retry delays and an
outer coroutine deadline. Authentication/malformed/unsupported errors are not
retried. There is no SNMP engine discovery scan: USM engine-ID negotiation can
occur only as part of contacting the single approved endpoint. Device views must
limit the read scope independently. Successful/failed CLI attempts are audited
with per-device PostgreSQL locking and persisted pacing. Run history retains
counts only. The minimum interval between explicit polls is 5 seconds; the
library also prevents concurrent/repeated use of one collector instance.

## Storage, provenance and recovery

`rf_radio_v2` lives in existing PostgreSQL `telemetry_samples.raw_data`; migration
head remains `0007_integration_readiness`. The NOC-84 backup/restore procedures
include it. No additional schema migration is necessary. Samples record profile,
mapping digest, source, measurement/receipt times, nullable typed metrics,
counters, status, original accepted numeric/enumeration values **and input units**.
Unknown/invalid values are not preserved as raw payloads; their availability
reason is retained. This deliberately avoids retaining passwords reflected by a
device. Troubleshooting requiring a complete packet/response is a separately
controlled operator procedure, not automatic application logging.

SNMP lacks a remote wall-clock measurement timestamp here. A successful GET is
labelled `collector_receipt`; observation time equals receipt time, rather than
inventing a radio source timestamp. Synthetic exports use explicit aware source
timestamps. Stale/future status is evaluated separately from field availability.
Missing, unsupported and invalid fields remain distinct from valid zero values.

Per-device advisory locking serializes storage. Equivalent same-time/profile
replays are idempotent even with a later receipt time; conflicting same-time
values are rejected. No automatic overwrite or deletion occurs. Numeric fields
project into `rf_metrics_v1` for previous diagnostics. Status-only/counter-only
samples do not make missing numeric RF measurements appear fresh. The latest
observation endpoint recognizes both generations.

Counters retain exact 64-bit integers. Delta calculation requires ordered samples,
a matching mapping/profile and known nondecreasing uptime. A decreasing uptime
marks a restart; a decreasing counter marks reset/wrap uncertainty. No guessed
wrap arithmetic, negative rate, false disconnection or fabricated recovery is
emitted. First samples, unknown uptime and changed mappings establish a new or
unknown baseline. Existing retention controls remain disabled by default; operators
must size telemetry storage and establish an approved retention policy before
continuous collection.

## Diagnostics and operator interface

`GET /api/devices/{id}/rf-radio-history?limit=288` is authenticated operator/admin,
local database only, bounded to at most 1,000 observations within seven days.
It never polls equipment. It returns sanitized history, last poll attempt/error,
freshness, counter continuity and nonexecuting diagnostic warnings.

Default consecutive threshold validation requires three observations; missing or
invalid measurements reset that metric's streak. Time gaps, mapping changes and
observed restarts break continuity. Stale/future history is not current degradation
or recovery. Current health is unknown until complete healthy evidence exists.
Warnings cover weak RSSI, low SNR, high/increasing noise, signal reduction,
absolute/relative capacity reduction, sustained disconnects, repeated observed
up→down transitions and poor CCQ. Historical change compares with the first
sample in the uninterrupted bounded segment, not a permanent learned baseline.
Intermittency uses the latest `2*consecutive_samples+1` known state observations;
missing/stale state breaks it. Noise is an indicator, not a definitive interference
or root-cause claim. Error/retry counters are displayed separately and are not
silently interpreted as packet-loss percentages.

`RF_RADIO_THRESHOLDS` accepts a JSON object validated at startup. Supported keys:
minimum_rssi_dbm=-75, minimum_snr_db=20, maximum_noise_dbm=-85,
minimum_capacity_mbps=100, minimum_ccq_percent=80, signal_drop_db=6,
noise_rise_db=6, capacity_drop_percent=40, consecutive_samples=3,
max_gap_seconds=600, stale_after_seconds=900. Unknown, nonfinite or invalid
thresholds fail configuration. Operational correlation evaluates its existing
15-minute/20-sample window; longer diagnostic API history can therefore provide
additional historical context, and operators should keep configured windows
consistent with the poll cadence. No promise of equivalence across arbitrary
window sizes is made.

The operational dashboard emits one stable `rf:<device_id>` alert key for all
radio reasons. Maintenance and probable upstream outages suppress child radio
alerts. Existing offline-only incident rehearsal deduplicates creation, audits
lifecycle actions and closes only with fresh confirmed reachability plus complete
healthy RF evidence. It is not a new production incident/remediation scheduler.

RF Diagnostics now includes current local/remote RSSI, signal/noise/SNR and
channel information, PHY versus throughput/capacity, quality/MCS/subscriber count,
GPS state, counter continuity and unsupported reasons. Accessible SVG history
charts show signal, SNR, noise and capacity without zero-filling absent values.
Accepted original values/units are available for troubleshooting. RF self-healing
remains **simulation-only**, with no execution or settings controls.

## Reproduction and acceptance

Use only disposable databases and synthetic configuration as in the NOC-84 guide.

```bash
cd backend
python -m pytest -q
python -m pytest -q tests/test_radio_snmp.py tests/test_radio_postgres.py
```

The test fixtures use RFC 5612 documentation enterprise number **32473**. These
are explicitly synthetic OIDs and must never be copied into a vendor mapping.
Transport tests inject mock GET responses; the PySNMP boundary is also mocked,
so no request reaches even the documentation addresses. Tests cover capability
profiles, values/units, unsupported/missing/malformed data, firmware differences,
64-bit counters, resets/restarts, stale/future history, threshold streaks, link
instability, disabled policy/endpoint rejection, timeouts/retries/recovery,
authentication failures, secret exclusion, real-PostgreSQL persistence/dedup,
API authorization, alert correlation, maintenance and incident lifecycle.

CI retains the entire earlier backend/frontend/migration/Compose/RF/worker-scale/
Ubuntu 24.04 installation/recovery suite. The new `python -m app.radio_rehearsal`
helper requires offline synthetic staging and exclusively synthetic inventory;
it stages six observations each for existing Ubiquiti and Cambium fixture radios,
never contacts equipment, and changes no radio settings. The conditional browser
case verifies degraded state, noise charts, timestamps, unavailable capabilities
and original units. Final SHA, exact counts and CI URL are recorded in the draft PR.

## Rollback and remaining gates

Disable polling before upgrading or rolling back. Back up the database and private
configuration securely using the existing NOC-84 guide. Retain egress isolation.
Older images ignore `rf_radio_v2` but can still read its compatible numeric
projection; no migration downgrade is required for this increment. Remove the
new activation/configuration settings before reverting images and use the
NOC-86 credential-removal precautions when reverting further upstream. Never
restore onto or overwrite a production database as part of acceptance.

Before real-radio tests: obtain authoritative airMAX and ePMP 4600 MIB/firmware
fixtures, verify per-metric units/semantics and enum codes, verify SHA-256/AES128
SNMPv3 and least-privilege views, establish approved unicast targets/egress,
validate accuracy/latency against independent observations, and obtain explicit
permission for that separate test. Devices with incompatible SNMP security need
a separately reviewed transport, not a downgrade. Per-subscriber identities,
automatic fleet discovery/scheduling and verified vendor OID catalogs are not
implemented. Upstream review, bare-VM installation, immutable artifacts, inherited
JWT/retention/monitoring-SLA and credential audit gates remain. Synthetic tests
establish software behavior only, **not actual firmware compatibility**.
