# NOC-88: Racom and Aviat microwave monitoring

This work stacks on draft NOC-87 #91 at
`f31b35eceeb99d6167c284fa15777825206f6616` (CI #438 passed), preserving
NOC-83 through NOC-87. Nothing is merged or deployed. Review upstream PRs
separately before release. See the NOC-84 install/recovery and NOC-86/87
integration guides for inherited release requirements.

## Read-only interfaces and actual compatibility limits

The implemented transport is numeric-instance **SNMPv3 authPriv GET**, with
SHA-256 authentication and AES-128 privacy through pinned PySNMP. SNMP GET/USM
are documented in RFC 3416/3414, AES privacy in RFC 3826, and SHA-2 USM in
RFC 7860. There is no SET, walk, GETNEXT/GETBULK, scan, SSH, guessed REST API,
configuration write, or fallback to plaintext/community SNMP. USM engine-ID
negotiation can contact only the single approved endpoint; it is not discovery
across a network. This standard protocol implementation does **not** establish
that a given Ray3 or Aviat firmware supports these algorithms or metric OIDs.

Prepared contracts:

| Profile | Explicit carrier layout | Available mapped telemetry |
| --- | --- | --- |
| racom-ray3-mib-v1 | 1–2 selected channels, including separately reported 2 × 80 MHz | Local/remote RSL and TX power, optional SNR, modulation/adaptive status, frequency/bandwidth, capacity, Ethernet throughput, temperature, availability, alarms, error counter, uptime/version/hardware ID |
| aviat-11ghz-mib-v1 | 1–8 selected carriers | Same normalized field vocabulary, only where the particular platform/MIB supports it |
| aviat-18ghz-mib-v1 | 1–8 selected carriers | Independent mappings and instance selections; not assumed identical to 11 GHz platforms |
| aviat-multicarrier-mib-v1 | 1–8 selected carriers | Carrier-specific evidence plus separately reported aggregate capacity/throughput/utilization |

A profile name is a capability contract, **not certified device compatibility**.
No vendor OID catalog ships as verified. An administrator must obtain and review
an authoritative model/firmware-specific MIB, enumerate exact instance OIDs,
confirm units/enum meanings and create a least-privilege remote SNMP view.
`mib_sha256` records operator attestation to the reviewed MIB; it does not prove
provenance independently. Preserve mapping, MIB/firmware evidence and engineered
plans in controlled operator configuration. The complete mapping/reference
content is hashed into each observation's mapping digest.

Only configured capabilities are read; missing or unsupported fields are not
substituted. No carrier auto-discovery or assumption of identical Aviat models
is made. An actual device without the required SNMPv3 security combination is
unsupported. Alternate reviewed interfaces need a separate implementation and
fixtures; there is no automatic downgrade. All current vendor tests are mocked
or synthetic. No radio, vendor login, real TLS/SNMP session, production subnet,
production credentials or production database was contacted.

## Configuration and execution

Defaults: `RF_MICROWAVE_ENABLED=false`, empty `RF_MICROWAVE_CONFIG_FILE`, and
`RF_MICROWAVE_THRESHOLDS={}`. Polling is explicit, never scheduled by the worker:

```bash
python -m app.microwave_poll
```

This entry point requires online mode, explicit global administrator approval,
explicit microwave enablement and a private configuration file. Offline mode
fails before reading configuration/credentials or opening a socket. Isolated
acceptance rejects microwave activation and config-file references. Do not
activate collection as part of release acceptance.

Configuration structure (intentionally incomplete and disabled):

```json
{
  "device_id": 123,
  "network_link_id": 45,
  "credentials_file": "/run/secrets/microwave-snmp.json",
  "policy": {
    "enabled": false,
    "admin_approved": false,
    "mapping_validated": false,
    "address": "192.0.2.88",
    "allowed_addresses": ["192.0.2.88"],
    "timeout_seconds": 2,
    "retries": 1,
    "min_interval_seconds": 5
  },
  "mapping": {
    "profile": "racom-ray3-mib-v1",
    "mib_sha256": "<64 lowercase hex characters from reviewed MIB>",
    "carriers": {"c1": {}, "c2": {}},
    "link_fields": {},
    "engineered_references": {}
  }
}
```

Replace empty maps only with reviewed vendor instance mappings. Each field has
`oid`, `unit` and optional numeric `states`. Carrier identifiers are c1–c8, and
Ray3 is limited to c1/c2 or another explicit pair within that identifier set.
Duplicate OIDs, unknown fields, incompatible units, unsupported enums and
out-of-range values fail or become explicitly invalid. No nonread command
facility exists. At least one and at most 192 mapped values are allowed.
A poll issues GET batches of at most 32 varbinds, with pacing between batches,
0.2–3 second request timeouts, 0–2 retries for timeout/unavailability, bounded
backoff and a 30-second overall deadline. Any failed batch discards the whole
observation; a partial poll cannot masquerade as a complete multi-carrier poll.
Sequential batches are **not** an atomic device snapshot. SNMP timestamps are
labelled collector receipt time, not an invented source wall clock.

The device must already exist, be enabled/authorized, match the vendor, and have
an IP exactly matching the approved IPv4 literal. The NetworkLink must already
exist and reference that device as an endpoint. No inventory discovery, topology
rewriting or automatic link reassignment occurs. Only exact IP allowlists are
supported; no DNS, CIDR scans, loopback/link-local/multicast/unspecified or limited
broadcast targets. Administrators must verify unicast ownership and routed
subnet boundaries separately and enforce external egress restrictions.

Configuration and credential files use the NOC-87 bounded private-file reader:
owned by application UID, regular files, mode 0400/0600, no symlinks, at most
64 KiB. Credentials contain username/auth_key/priv_key as redacted SecretStr
values. Keep them in read-only secret mounts, not Git, argv, browser storage or
logs. Public errors/audits are fixed codes, not exception text or device JSON.
Failed attempts are audited and paced; a PostgreSQL try-lock prevents overlapping
radio/microwave polls of one endpoint. No activation controls are exposed in UI.

The existing internal-only Compose network and public internet restrictions are
retained. A future lab connection needs a separately approved egress design;
no production access or egress relaxation is included here.

## Measurement semantics and historical storage

`rf_microwave_v1` is stored in existing PostgreSQL telemetry_samples JSON with
an explicit microwave_link_id. Migration head remains 0007; no schema change is
needed. Normalization retains only accepted original numeric values/enumeration
codes, explicit units and a tightly restricted firmware version string. Firmware
strings outside numeric dotted versions with optional -rcN/-buildN are invalid;
arbitrary strings, model names, raw packets and credentials are not retained.
API/browser counter displays include exact decimal strings for 64-bit counters
and original values above JavaScript's safe integer range. Hardware identification is an optional **numeric vendor ID**, not an inferred
human model or a serial number. Richer identification needs verified fixtures.

RSL/TX power use dBm; SNR uses dB; Hz/kHz/MHz normalize to MHz; bit/s, kbps and
Mbps normalize to Mbps; centiseconds normalize to seconds. Local and remote
values never overwrite one another. TX power is an observed reading, not a
configuration action. PHY/modulation, reported capacity and measured Ethernet
throughput remain distinct. Aggregate capacity/utilization are only values
reported through explicitly mapped aggregate OIDs. Carrier capacities are never
automatically summed, and bandwidth is never converted into a capacity estimate.
Two 80 MHz measurements remain two carrier observations.

Missing, unsupported and invalid field reasons are distinct from zero. Collection
freshness is independent: fresh/stale/missing/future. Reported availability uses
the reviewed MIB's measurement window and is not a calculated SLA. Error counters
retain exact unsigned 64-bit values. Error events/second require ordered samples,
matching mappings, known nondecreasing uptime and nondecreasing counters. Reboot,
reset/wrap uncertainty, missing counter/uptime or mapping changes produce no
invented delta, percentage or rate.

Per-device PostgreSQL staging locks serialize equivalent retry detection. Same
measurement timestamp/profile/value/link is idempotent even with a later receipt;
conflicting data or link association is rejected. Nothing silently overwrites or
deletes historical samples. Changing the associated link begins a new diagnostic
segment. A missing/changed current topology association makes health unknown.

Microwave carriers are deliberately **not projected** into legacy single-radio
rf_metrics_v1: choosing the first carrier or summing capacity would misrepresent
the link. Earlier RF diagnostics and simulation-only self-healing stay intact;
use microwave history for microwave health. Existing backups include the new JSON.

## Diagnostics, hysteresis and engineered references

A pure bounded-history replay returns RF, Ethernet and collection evidence
separately. Default threshold events enter and clear after three observations.
Values in the hysteresis band retain an existing event, without triggering a new
one. Missing/invalid measurements make that check unknown rather than clear.
Time gaps, restarts, mapping changes and stale/future samples break continuity.
Healthy recovery requires complete core carrier evidence and clear supported
checks, not merely disappearance of an alert.

Events include weak/declining local RSL, absolute/relative capacity reduction,
low modulation efficiency, single-carrier/full radio disconnection, instability,
reported availability degradation, high temperature, sustained reported alarms
and high/increasing error-event rates. Error events are not silently called BER
or packet loss. Modulation fallback compares normalized known QAM efficiency,
not undocumented vendor MCS numbers. Unknown modulation codes remain invalid.
Noise/rain/interference/equipment failure are never confirmed root causes.

An optional engineered reference requires receiver_threshold_dbm, a SHA-256
reference identifier, modulation and bandwidth_mhz. Fade margin is calculated
only when observed modulation and bandwidth match that approved reference.
Otherwise it is unavailable, not zero or a guessed sensitivity. The application
validates matching/ranges; an engineer must validate the underlying reference
and its suitability for the actual hardware, direction and design objective.

`RF_MICROWAVE_THRESHOLDS` is a validated JSON object. Defaults:

- consecutive_samples=3; max_gap_seconds=600; stale_after_seconds=900.
- RSL enter below -75 dBm, clear at/above -72 dBm.
- Capacity enter below 100 Mbps, clear at/above 130 Mbps.
- Temperature enter above 75 °C, clear at/below 70 °C.
- Availability enter below 99%, clear at/above 99.9%.
- Modulation enters below 4 bits/symbol, clears at/above 6; a sustained relative
  drop of 2 bits/symbol also warns, clearing below a 1-bit/symbol drop.
- Fade margin enter below 10 dB, clear at/above 13 dB, only with matching reference.
- Errors enter above 10 events/s, clear at/below 5 events/s. Rate increase also
  enters above 10 events/s relative to the first valid rate in the segment,
  clearing at/below 5 events/s above that baseline.
- Relative RSL decline 6 dB and capacity reduction 40%; clearing requires recovery
  below half of the relative threshold against the uninterrupted segment baseline.

The exact setting names/ranges are in microwave_policy.py. Nonfinite, inverted
hysteresis, unknown or invalid settings fail startup. Diagnostic API history is
bounded to 1,000 observations/seven days; operational correlation replays the
latest 41 observations, enough for the maximum configured 20-sample streak and
its state-transition window. No permanent learned baseline is claimed.

## Dashboard, topology and incident correlation

Authenticated operator/admin GET `/api/devices/{id}/microwave-history` reads local
history only. It returns carrier values/status/alarms, original values/units,
firmware/numeric hardware ID, source/mapping/timestamps, topology association,
last collection-attempt error, and separate RF/Ethernet/freshness diagnostics.
The RF screen displays per-carrier local/remote RSL charts without zero filling,
TX power/modulation/bandwidth/capacity/throughput, fade-reference validity,
error continuity and reported alarms. It exposes no equipment controls.

The main dashboard includes microwave link state, RF versus Ethernet state,
carrier status, last collection, freshness and potential downstream inventory.
The operational summary retains six batched SELECTs, rather than polling or
querying each device individually. Stale microwave observations remain visible
within the seven-day lookup bound, and are not current outages or recovery.

Configured A→Z links and existing parent relationships determine **probable**
impact. Traversal is cycle-safe. One stable microwave:<link_id> alert aggregates
carrier reasons. Maintenance and fresh upstream device outages suppress it.
A sustained, nonconflicting complete RF outage suppresses duplicate downstream
device/RF alerts. Single-carrier degradation, collection errors and conflicting
endpoint reports retain independent device evidence. Endpoint reports and
single/multiple-endpoint coverage remain explicit; mixed evidence is not a
confirmed physical root cause. A single endpoint's view is not proof of complete
bidirectional radio compatibility or end-to-end service delivery.

The existing explicit offline-only incident rehearsal deduplicates microwave
incidents, audits lifecycle actions, and closes only after healthy microwave
history **and fresh confirmed reachability at both configured endpoints**.
Maintenance, missing telemetry or polling failure never establish recovery.
There is no new production incident scheduler or automatic remediation.

## Reproduction, rollback and remaining release gates

Use only disposable PostgreSQL and synthetic data as in NOC-84:

```bash
cd backend
python -m pytest -q
python -m pytest -q tests/test_microwave.py tests/test_microwave_postgres.py
```

Fixtures use RFC 5612 documentation enterprise number **32473**, never invented
Racom/Aviat OIDs. All device transports are mocked, including the PySNMP boundary;
no packets are sent to radios or the fixture documentation addresses. Tests
cover all four profiles, 2 × 80 MHz, eight configured Aviat carriers, impairment,
modulation/capacity changes, carrier/full outage, radio/Ethernet alarms, missing/
malformed values, resets/restarts, engineered-reference matching, stale/future
history, batching/retries/recovery, secret exclusion, PostgreSQL dedup/topology,
API permissions and incident correlation/recovery.

CI preserves all earlier backend/frontend/migration/Compose/Ubuntu 24.04/RF and
250/500/1000-device worker checks. The offline-only app.microwave_rehearsal helper
creates two synthetic backhaul associations in the disposable WISP fixture and
stages six observations each for two-channel Racom/four-carrier Aviat. It never
changes equipment or existing live infrastructure. A conditional browser case
checks carrier alarms/charts/units, separated RF/Ethernet/collection state and
main-dashboard microwave visibility. Exact final SHA/counts/CI are in the PR.

Before rollback, disable microwave polling and retain egress isolation. Back up
PostgreSQL and private configuration securely using NOC-84 procedures. Earlier
images ignore this versioned JSON; no migration downgrade or data deletion is
needed. Remove new activation/config paths before reverting, and apply NOC-86
credential-removal precautions for older code. Restore tests must target isolated
databases; never overwrite production data. Old images cannot display microwave
history, so plan operator visibility during rollback.

Remaining gates: authoritative Ray3 and specific Aviat model/firmware MIBs and
sanitized captures, supported SHA-256/AES128 SNMPv3 and read-only views, reviewed
OID instances/enum units/window semantics, numeric hardware-ID interpretation,
engineered reference approval, lab endpoint ownership/egress, and independent
measurement comparison. No real-device or firmware compatibility acceptance has
passed. Scheduling/fleet discovery, raw response retention, richer firmware/model
parsers, continuous-storage retention/SLA sizing and alternate transports are
not added. Upstream review, clean bare-VM installation, immutable artifact and
inherited JWT/credential/monitoring-SLA release gates remain. All RF self-healing
remains simulation-only. No production access, deployments, merges or changes to
AI Fiber Designer/MikroTik Management occurred.
