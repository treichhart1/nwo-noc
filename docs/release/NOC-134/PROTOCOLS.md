# Verified protocol references and hardware limits — 2026-11-28

Official sources reviewed:

- [Native API framing/login](https://help.mikrotik.com/docs/spaces/ROS/pages/47579160/API): existing6.43+ login, fixed print requests and API-SSL8729, CA/hostname validation. No plaintext8728, legacy MD5 or certificate bypass.
- [REST API](https://help.mikrotik.com/docs/spaces/ROS/pages/47579162/REST+API): RouterOS7 HTTPS, fixed read paths. REST supports write operations but AI NOC does not expose them. Special proplist/query behavior is a print contract, not proof of filtering on LTE monitor.
- [Ethernet](https://help.mikrotik.com/docs/spaces/ROS/pages/8323191/Ethernet): one-shot monitor and explicitly unit-bearing optical/rate output. Examples establish dBm/C/V and Mbps/Gbps; unitless/unknown formats stay unavailable. Actual DOM/transceiver availability depends on port/module/firmware.
- [PoE-Out](https://help.mikrotik.com/docs/spaces/ROS/pages/19136769/PoE-Out): monitor-only output, not PoE configuration. Measured V/mA/W are separate from configured output mode. Per-port totals do not establish total switch input power.
- [Connection tracking](https://help.mikrotik.com/docs/spaces/ROS/pages/130220087/Connection+tracking): only table total/max counters, never individual subscriber connection tuples. [Health](https://help.mikrotik.com/docs/spaces/ROS/pages/25690117/Health) exposes model-dependent name/value/type; raw units are preserved because older API voltage scales differ.
- [WireGuard](https://help.mikrotik.com/docs/spaces/ROS/pages/69664792/WireGuard):7-only peer read properties last-handshake age/rx/tx. Request only`.id,interface,disabled,last-handshake,rx,tx`. Never request private/public/preshared keys or endpoint credentials. A recent handshake is not proof of tunnel reachability; absent/zero/unsupported evidence is not healthy.
- [Current LTE/5G manual](https://manual.mikrotik.com/docs/mobile-networking/lte-5g/) and [frozen LTE notes](https://help.mikrotik.com/docs/spaces/ROS/pages/30146563/LTE+5G): fields vary by modem. Documented LTE monitor output includes subscriber identities. **Native LTE monitor is deliberately not allowlisted** until official source-minimized contract is supplied. Recorded parser rejects IMSI,ICCID,IMEI,SIM secrets and every unknown field. No modem AT chat, scanning, cell locks or SIM changes. Do not treat LTE anchor values as NR/NSA serving identity; explicit technology namespace required.
- [IP addresses](https://help.mikrotik.com/docs/spaces/ROS/pages/328247/IP+Address), [VRF](https://help.mikrotik.com/docs/spaces/ROS/pages/328206/Virtual+Routing+and+Forwarding+-+VRF), [OSPF](https://help.mikrotik.com/docs/spaces/ROS/pages/9863229/OSPF): existing state evidence/recorded routing domains. API6 uses `/ip/route/vrf/print` and routing-mark;7 uses `/ip/vrf/print`, `/routing/table/print`, routing-table. No inference of verified redundant path from a route entry alone.

The newer manual identifies7.26; frozen documentation remains linked to preserve historical6/7 evidence. These are documented protocol contracts, **not confirmation on every firmware build**. Required operator acceptance: exact RouterOS version/build, hardware revision, applicable package/modem/transceiver, sanitized command output, verified units and supported fields. Optional denied/absent resources are labeled `unavailable_or_permission_denied`; no false claim that a permission failure proves unsupported hardware.

| Equipment | Supported path foundation | Remaining actual acceptance |
|---|---|---|
|CCR1036 / CCR2116|RouterOS6.43+ or7 API-SSL as actually installed;7 HTTPS REST base|Resource/sensor fields and board revision; no identical-temperature assumption|
|RB5009|RouterOS7 API-SSL/REST; optional peer/IPAM reads|Exact hub model/firmware; no automatic management reachability claim|
|CRS326 / CRS318 / CRS310|RouterOS native base + optional bridge/VLAN/optical reads|Confirm booted OS and transceiver DOM; SwOS has no implemented collector|
|netPower16P / RB960|RouterOS base + explicit PoE monitor interface IDs|Actual PoE output hardware/units/ports/firmware; never power-cycle|
|LtAP mini LTE|RouterOS base monitoring, recorded sanitized cellular evidence|Modem/build and source-minimized cellular contract required|
|Chateau LTE/5G|Same; explicit recorded LTE/NR namespaces|LTE/NSA/SA/modem-specific field evidence; never infer mode from product name|
|LHG LTE/5G / SXT LTE / ATL LTE/5G|Same RouterOS base; per-modem recorded fields|Exact supported package/modem, firmware, units; native LTE blocked|
|Other RouterOS devices|Version-gated base contract only|No blanket hardware/sensor compatibility claim|
|SwOS devices|Unavailable|Separate verified secure read-only protocol needed; no RouterOS commands sent|
|Inseego FW2010/other CPE|Compatibility foundation only|[Official product](https://inseego.com/products/receivers/fw2010/) lists cloud API via Inseego Connect Advanced; [user guide](https://static.inseego.com/us/download/FW2010_user_guide.pdf) does not establish an available least-privilege API schema. Require official version/auth/read endpoints/secure transport/schema/sanitized fixtures. No scraping|

Unavailable without verified evidence: hardware-offloaded traffic accounting completeness, loop cause, total switch power, neighbor uptime/version-specific OSPF fields, L2TP/PPP session details that risk user credentials, LTE native registration/SIM-safe fields, NR identifiers/bands/aggregation/modem temperature where undocumented. Do not estimate any of these from unrelated fields.
