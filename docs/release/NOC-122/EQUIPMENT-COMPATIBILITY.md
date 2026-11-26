# Read-only equipment acceptance — no live compatibility certification

Status vocabulary: **synthetic contract verified** means parser/TLS/policy/test evidence, not a tested model/firmware. **BLOCKED** requires named operator/vendor inputs. No model is certified from a fixture name, shared frequency or inventory entry. Every device requires separate approval and nonsecret evidence of exact model, board revision, full stable firmware build, API/MIB revision/hash, read-only permission policy, allowlisted endpoint, units/indexes and observed-versus-NOC comparisons.

## MikroTik native API-SSL and REST

|Equipment/firmware|Implemented collection path|Acceptance status|
|---|---|---|
|RouterOS6.43+ stable|Native certificate-based API-SSL TCP8729, post6.43 login/sentence framing|Synthetic protocol/TLS/error/scheduler tested; exact hardware/firmware BLOCKED|
|RouterOS7 stable|Same native API-SSL; separately allowlisted HTTPS REST GET|Synthetic tested; choose exactly one polling path/device; firmware acceptance BLOCKED|
|CCR1036,CCR2116,RB5009|Reuse matching supported RouterOS native/REST contract, no new model-specific endpoints|All exact builds/sensor availability NOT VERIFIED|
|CRS/netPower switches|Same CPU/memory/interface contract if the installed RouterOS supports it|SwOS-only devices are unsupported by this RouterOS collector; hardware offload/PoE/sensor/OSPF visibility varies|
|RouterOS below6.43 / legacy challenge login|No native support; RouterOS6 REST is rejected|Unsupported; never enable plaintext or legacy-MD5 fallback|

[Vendor API protocol](https://help.mikrotik.com/docs/spaces/ROS/pages/47579160/API) and [current manual](https://manual.mikrotik.com/docs/developer-guides/api/) establish API framing/8729/certificate mode; certificate-less anonymous TLS in vendor documentation is deliberately unsupported. [REST documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/47579162/REST+API) establishes RouterOS7 HTTPS JSON GET. Example insecure curl flags in vendor pages are not accepted by this project.

Native fixed read commands/properties are enforced at the wire boundary in backend/app/integrations/mikrotik_native.py:

|Read|Normalized measurements and limits|
|---|---|
|/system/resource/print|version,cpu-load,total-memory,free-memory,uptime; CPU percent, memory bytes/percent, uptime seconds|
|/system/identity/print|Name for response validation; restricted scheduler DTO does not synchronize identity/firmware into inventory|
|/interface/print|.id,name,running,disabled,rx-byte,tx-byte,rx-error,tx-error,rx-drop,tx-drop; exact integer counters and interface state; rates require consecutive fresh/reset-safe observations|
|/system/health/print (optional)|Only configured Celsius/decicelsius temperature; unknown/multiple sensors unavailable, no guessed units|
|/routing/ospf/neighbor/print (optional)|router-id,state; missing/permission/unsupported results are collection evidence, not fabricated adjacency failure|

RouterOS6/7 health table shapes and sensor units differ; see [health guidance](https://help.mikrotik.com/docs/spaces/ROS/pages/25690117/Health). Optional health polling can be expensive. Keep temperature_unit=null/collect_ospf=false initially, enable separately only after validating exact units/permissions. No native DHCP/ARP/routes/IPAM discovery, IPv6 management transport, switch ASIC/PoE counters or inventory firmware synchronization is claimed. OSPFv3/multitable details are not collected by the small neighbor contract. Interface throughput is not RF capacity; hardware-offloaded counters need independent comparison.

### Operator procedure after separate authorization

1. Select exactly one existing Device/Site ID, model/full stable firmware and literal management IPv4 from the approved IP plan. Protocol minimum6.43 is **not a security recommendation** to retain obsolete firmware. Select a currently supported secure device release under its own change control; this task does not upgrade equipment.
2. The authorized device administrator creates a dedicated nonshared monitoring user/group through the trusted management interface, not the NOC. For native API, restrict RouterOS policies to **read,api** as supported by that exact release, source address to the NOC egress /32; exclude write,policy,reboot,sensitive,test,ssh,winbox,web,ftp and other unnecessary rights. Do not use the broad default read group without reviewing its actual policies. Record the granted policy, not its password. If optional reads need more privilege, stop and review; do not grant full admin. For REST use the release's minimum read plus rest-api policy where supported, not native api as a substitute.
3. For API-SSL select TCP8729 with a server certificate issued by the approved private CA, valid dates/server usage and **IP SAN equal to the literal collector address**. Import the CA/certificate through the operator's protected process, bind that certificate to api-ssl, restrict service allowed addresses and intervening routed/firewall ACL to the NOC /32. Leave plaintext8728 disabled. Do not reconfigure a production device merely to perform this assignment. REST uses approved HTTPS www-ssl origin/certificate on its separately authorized port, never www/HTTP.
4. Independently transfer only the public CA to the service, verify fingerprint out of band, check clock and chain before authorization. After device/network authorization an operator may use `openssl s_client -connect APPROVED_IP:8729 -CAfile APPROVED_CA.pem -verify_ip APPROVED_IP -verify_return_error </dev/null` as a TLS-only acceptance probe; **do not run now**. Do not use -k, CERT_NONE, anonymous ciphers or hostname-disable switches.
5. Use [native inert template](../../../deploy/pilot/mikrotik-native.example.json), disabled/unapproved by default. Private config/credential files are regular0400/0600 files, owned by the application UID10001, approved read-only mounts and non-writable parents. Credentials JSON fields are username/password; obtain from a protected broker/interactive editor, never argv/logs/Git. Template192.0.2.121 is documentation-only. A reviewed deployment configuration must deliver these mounts; the core lab installer intentionally refuses live integration overrides. **Do not edit generated signed deployment artifacts to activate a pilot.**
6. Under a separately reviewed activation, existing flags are MIKROTIK_ENABLED, MIKROTIK_DEVICE_ID, MIKROTIK_NATIVE_CONFIG_FILE, INTEGRATIONS_ADMIN_APPROVED and RF_SCHEDULER_ENABLED, with OFFLINE_MODE=false. Native and REST origins/credentials are mutually exclusive. Device/address must match inventory and explicit allowed_addresses. Do not activate these in PhaseA. Initial job interval **300s**, concurrency1. Native minimum60s, socket timeout<=3s, total30s, max2MiB/512rows. Scheduler lease/deadline must cover validated overall request budget (existing native/MikroTik contract>=40s), with bounded retries/backoff; no tight reconnect loop.
7. Validate normal CPU/memory/uptime/interface/counters against the device UI at recorded timestamps. Observe repeated samples and a **synthetic** counter reset/disconnection/recovery; do not reboot a live router for a test without separate authorization. Confirm optional metrics are unavailable until explicitly validated. Preserve all unique sample/receipt/audit evidence.
8. Authentication/permission/timeout/unsupported/oversized failures use static sanitized codes. Stop polling on credential mismatch and review the dedicated account; never retry passwords indefinitely, log raw traps or classify absent telemetry as an RF outage. Known static failures survive scheduler reporting. Synthetic contract tests cover these cases; device logs/access rate must still be checked.

No account/service/certificate/firewall/router changes were executed. Exact policy support and TLS configuration are device-administrator acceptance items.

## Ubiquiti Wave and AF60 — explicit compatibility blocker

|Family|Official evidence|Unverified requirements|
|---|---|---|
|Wave Pro|[Specifications](https://techspecs.ui.com/uisp/60ghz-wireless/wave-pro) list UISP/SNMP|SNMPv3 authPriv support, per-firmware OIDs/units/rows, secure UISP RF schema|
|Wave LR|[Specifications](https://techspecs.ui.com/uisp/60ghz-wireless/wave-lr) / [vendor datasheet](https://dl.ui.com/ds/wave-lr_ds.pdf)|Same; product service listings do not establish SNMP security versions|
|Wave AP/AP Micro/AP Gen2|[Wave AP specs](https://techspecs.ui.com/uisp/60ghz-wireless/wave-ap)|AP/station table indexes and each model/firmware differ; no inherited LR mapping|
|AF60-XR and other60GHz|[AF60-XR specs](https://techspecs.ui.com/uisp/60ghz-wireless/af60-xr)|Not automatically the same firmware/protocol as Wave; confirm exact platform and revision|

Official [Wave4.1.0 release](https://community.ui.com/releases/Wave-PTMP-PTP-4-1-0/8f6eb0b1-eed8-401a-b9af-595065f97502) and [MIB revision thread](https://community.ui.com/releases/aef449c9-51e8-4373-bac8-06e2eab4b23e) were reviewed in NOC121; current public pages render only a JavaScript loading shell through the browsing tool. NOC122 bounded reads of both official archives again failed **Tunnel connection failed:403 Forbidden**:

- https://ui.com/downloads/firmwares/wave/v4.1.0/ui-mib.zip
- https://ui.com/downloads/firmwares/wave/v3.4.0/ui-mib.zip

No archive bytes/hash verified. Required operator/vendor input: actual installed model/build, its official **ui-mib.zip**, including **UBNT-MIB.txt**, **UI-AF60-MIB.txt (v1.0 where supplied)** and **UI-AF60-MIB-REV1.txt (v1.1 where supplied)** plus every imported module; acquisition source/date/vendor checksum/signature and localSHA256. Those are known archive/revision names, **not a claim that every firmware ships all three**. Supply the actual archive's exact names/revisions if different. Need per-field OBJECT-TYPE syntax/units/scale/sentinel/enums/access, table INDEX and specific instance suffixes; carrier/60GHz-versus-backup-radio semantics and a sanitized authorized response fixture. Root enterprise/subtree information alone is not an operational OID mapping.

For signal, link capacity, throughput, modulation, link availability, temperature, Ethernet state/errors: **no exact OID or availability is approved**. First obtain vendor evidence of SNMPv3 **authPriv** with permitted auth/encryption algorithms; otherwise provide documented HTTPS UISP API version, endpoint/read-only permissions/TLS/schema that actually exposes these values. UISP inventory import does not imply Wave RF metrics. Do not map old airFiber MIBs to Wave, add community-string SNMP, scrape undocumented web endpoints or use unverified HTTPS. A dedicated collector remains **BLOCKED**, not implemented on speculation. Operator must supply protocol evidence before any code correction or live acceptance.

## Other wireless and microwave equipment

Existing SNMP collectors issue explicit mapped **GET only**, SNMPv3 authPriv (no walk,SET,discovery). They require approved literalIPv4 allowlists, per-firmware MIB SHA256, exact unique OIDs/instances, units and enumerations, validated mapping, separate private secrets. Defaults disabled/unapproved. Initial300s/concurrency1; underlying mapped radio timeout0.2–3s/retries0–2 are bounded capabilities, not recommended aggressive polling. Failures remain monitoring failures and unsupported metrics stay unavailable.

|Requested equipment|Existing contract and supported normalized fields **when mapped**|Operator/vendor prerequisite|
|---|---|---|
|Ubiquiti airMAX/RocketAC/Gen2|airmax-ac-mib-v1 / legacy profile: localRSSI,SNR,noise,frequency,width,rate,uptime,state,quality/errors/retries; AC optional remoteRSSI,GPS,throughput,capacity/utilization|Exact model/firmware official MIB/imports/SHA256/units and confirmed authPriv; if firmware only offers community SNMP, **blocked**, no downgrade|
|Ubiquiti airFiber5XHD|No certified model-specific live profile; generic export/microwave foundation is not AF5XHD compatibility|Exact vendor firmware/MIB plus secure method and schema/carrier mapping required; do not select airMAX profile by brand|
|Cambium Force4600C / ePMP|epmp-4600-mib-v1 / legacy contracts: RSSI,SNR/noise,frequency,width,rates/state,modulation/MCS,subscriber count,errors;4600 optionaltemperature/throughput/capacity|Verify exact product name and part number: requested Force4600C must not silently be treated as ePMP4600c AP or Force4625 subscriber. Actual MIB revision/authPriv/field support required|
|RACOM RAy3 11GHz|Mapped microwave carriers: RSL/SNR,modulation,adaptive capacity,throughput,frequency,width,availability/errors/temperature where provided|Official exact RAy3 firmware MIB archive and imported modules; explicit carrier/link indexes, licensing/channel configuration provenance, counter reset/ACM semantics|
|Aviat11GHz|Mapped microwave multi-carrier contract|Exact Aviat platform/build/official MIBs; per-carrier capacity/RSL/modulation/alarm/state/error/temperature mapping; no generic endpoint invention|
|Ubiquiti AF11|Inventory/generic mapped microwave/export foundation|Exact AF11 firmware/secure protocol/carrier MIB mapping; frequency alone proves nothing|

Read backend/app/integrations/radio_snmp.py and microwave_snmp.py with the normalized observation models before approving a mapping. A capability field in code means the normalization can represent it, **not** that a device exposes it. Per-carrier capacity may change with adaptive modulation; use contemporaneous capacity and measured throughput, not configured Ethernet speed as RF bandwidth. Validate reboot/counter resets, missing/stale/invalid distinctions, link state versus Ethernet failure, isolated collector failure and stable consecutive recovery using synthetic fixtures before approved device comparison. Do not induce a live outage or change radio channel/power/modulation for acceptance.

## Required acceptance record per device

Owner/authorization; existing Site/Device/Asset/Link IDs; full product/build; vendor protocol/MIB source/hash; read-only group and endpoint allowlist (no secrets); TLS chain/IP SAN or authPriv policy;300s interval/deadline/retries; each metric's units/value/timestamp versus vendor UI; unavailable metrics; counter reset/stale/malformed/auth/timeout/recovery synthetic results; actual device load/connection logs; duplicate receipts/alert correctness; stop/revocation signoff. Keep raw private telemetry protected, not public CI artifacts. No live-device test is reported as executed in NOC122.
