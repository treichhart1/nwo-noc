# Operational integration acceptance — A111-15

Every real-equipment row is **BLOCKED**. A profile name is a NOC normalization
contract, not a firmware certification. No equipment has been contacted. Capture
exact model, firmware/build, vendor MIB hash/API documentation, raw redacted reply,
measurement units, account privilege list and device-side read-only audit before
operator sign-off. Never place credentials or management addresses in evidence.

| Equipment | Implemented method / contract | Available normalized fields | Required verification / unsupported capabilities |
|---|---|---|---|
| MikroTik ROS7 | GET-only TLS REST allowlist, identity/resource/interface/health/OSPF; IPAM separate observed export | CPU, memory, uptime, interfaces/counters/errors, OSPF; temperature if exposed | ROS7 version guard is tested, exact firmware is unverified; optional health/OSPF404 unavailable. No writes/CLI. Verify minimal read REST account without write/reboot/policy rights |
| MikroTik ROS6 | Documentation-only IPAM export normalization | Supplied interface/address/route/DHCP/ARP observations | **No ROS6 live REST collector**. ROS6 REST rejected; no claim of ROS6 live polling. Requires separately reviewed native API/SNMP adapter before pilot |
| Rocket AC/Gen2 | Explicit SNMPv3 authPriv GET mappings, airmax-ac-mib-v1 / legacy | RSSI/remote, noise/SNR, frequency/width, rates/capacity, CCQ, uptime/state, errors/retries; GPS only mapped | Vendor MIB/OIDs and hardware/firmware unverified. Legacy profile excludes capacity; every absent metric stays unavailable |
| AF11 | Existing microwave explicit MIB/export normalization | RSL, capacity, modulation/state/errors when mapped | No certified AF11 MIB/OID/firmware combination; reject unreviewed mapping. Frequency/11GHz alone proves no compatibility |
| Wave Pro/LR | Generic approved export foundation only | Only supplied/validated fields | **No certified Wave collector or antenna/device spec**. Dedicated capabilities/OIDs/API and firmware must be validated; not supported for live pilot by this candidate |
| Cambium4600c/4625 | SNMPv3 mapped epmp-4600-mib-v1 / legacy | RSSI/SNR, modulation/MCS, rates/throughput, capacity, subscriber count, temperature and radio state where mapped | Profile name does not certify4625/4600c firmware. Legacy/missing metrics remain null; no invented REST endpoint |
| Racom Ray3 | Explicit SNMPv3 microwave mapped carriers | Local/remote RSL, SNR, modulation, capacity/traffic, carrier/Ethernet state, errors | Licensed model/revision, exact MIB, carrier relationship, units/reset semantics need radio validation |
| Aviat | Explicit SNMPv3 microwave mapped carriers | Same normalized microwave contract, only available fields | Model/firmware/OIDs and multicarrier mapping unverified; no generic Aviat API invented |

## Common connection and credential contract

- Disabled by default; OFFLINE_MODE and administrator approval prevent live polling.
  Only approved individual origins/endpoints/OIDs; no subnet scan, walk, SET or config.
- HTTPS integrations require certificate verification, exact HTTPS origin, no redirect,
  GET allowlist, no userinfo/query in base URL. TLS applies to HTTP; SNMP instead
  requires authPriv, reviewed auth/encryption parameters and exact host/port allowlist.
- ReadClient:5s read,3s connect/pool/write,30s total budget, at most2 retries,
  minimum0.2s between requests. RF adapters use their bounded per-device private
  config/deadlines; scheduler timeout must exceed complete adapter budget (MikroTik
  minimum40s). Validate configured limits before approval, not a generic default.
- Proposed initial pilot: one device per technology,120s or longer intervals,
  concurrency1, independently reviewed lease above worst-case deadline. This is
  a conservative proposal, not a measured real-device throughput guarantee.
- Egress permits only each reviewed address/protocol/port and required approved
  DNS/NTP/CA dependencies. Verify DNS resolution/rebinding and firewall at deployment.
  No unrestricted internet or private-subnet discovery. Do not enable during NOC116.
- Secrets use private configuration/SecretStr and protected files, never logs, URLs,
  metrics labels or screenshots. Device account permissions must independently
  prevent writes; application allowlists alone cannot establish device permissions.
- Auth/permission failure, timeout, unsupported/malformed/stale data and disconnect
  remain explicit failures/unknown. Missing data never proves power/RF outage.
  Consecutive evidence/hysteresis and unique receipts prevent false recovery/duplicates.

Tests: test_release_blockers.py, test_read_only_integrations_postgres.py,
test_radio_snmp.py, test_radio_postgres.py, test_microwave.py,
test_microwave_postgres.py, test_monitoring_reliability.py. These prove synthetic
contracts and safety boundaries, **not firmware compatibility**.

## Powercode compatibility matrix

Synthetic fixture: backend/tests/fixtures/noc116-powercode.json:3 accounts/5 services,
multiple services per account, active/suspended/disconnected/unknown, wireless/fiber/
hybrid, stable tower/sector references. It intentionally supplies no consent.

| Contract | Code/test evidence | Operational status and next action |
|---|---|---|
| Stable account/service IDs | customers/contracts.py, sync.py; synthetic duplicates/replays/tombstones | BLOCKED: approve exact native ID/view mapping; never use names/phone as identity |
| Read-only export | FixtureAPI/FixturePostgres; exact HTTPS or approved read-only export view | BLOCKED: approve API/version or database export view and SELECT-only account; no native schema assumed |
| Tower/sector/service mapping | customers/impact.py and tests/test_powercode_customers.py | BLOCKED: operator reconcile stable source refs with existing NOC IDs; inactive services excluded, stale/unverified impact conservative |
| Consent/opt-out | NOC preferences with provenance; missing consent ineligible | BLOCKED: validate lawful provider fields, timestamp/source, global opt-out/quiet-hour rules; never infer consent from a phone number |
| Customer impact/redundancy | Existing topology/outage tests, customer-status targeting | BLOCKED: validate actual alternate path/failover evidence and freshness; confirmed versus estimated remains explicit |
| Billing/revenue | Approved read-only foundation | BLOCKED: contracted/invoiced/collected are distinct; no writes or live accounts created |

No fixture is advertised as native Powercode API compatibility. Production field
names, types, status enum/deletion semantics, paging/cursors/timezones, service-plan
IDs, contact consent and permission scopes require the Powercode operator's redacted
schema/export agreement and separately approved acceptance. Existing outage-targeting
suite tests deduplication, stale mappings, alternate paths, absent contacts and opt-out.

## External provider contracts

| Provider | Implemented boundary / synthetic verification | Remaining acceptance |
|---|---|---|
| Grafana | Private approved dashboard UIDs, restricted metrics reader, mock health/provisioning; analytics tests | BLOCKED: deployed version/API/service-account scopes, TLS/VPN/SSO/MFA, datasource, iframe cookies/CSP; no anonymous dashboards |
| WordPress | Versioned allowlisted public status API/example component; customer-status browser/API tests | BLOCKED: actual approved origin/cache/CSP/Turnstile/status hosting review. No live site modified or incident published |
| SMS | Approval outbox, consent/opt-out, stable idempotency/ambiguous-result hold, mock provider | BLOCKED: signed provider contract, delivery/reconciliation identifiers, rate/quiet-hour/opt-out semantics. Never enable delivery for this milestone |
| Fiber Designer | Versioned approved/published export, draft rejection, provenance/conflict/preview | BLOCKED: upstream application API contract/credentials/export compatibility. NOC does not edit engineering/as-built authority |
| Optional terrain/AI/geocoding | Offline fixtures/advisory outputs only | BLOCKED until provider privacy/egress/TLS contract accepted. No external download or auto-provisioning |
