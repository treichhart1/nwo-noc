# Equipment information worksheet — no device access

Complete from approved records/vendor files and authorized administrators. Do not contact equipment, scan networks, collect passwords or assume a product name establishes support. All acceptance flags remain false. Reuse NOC122 [compatibility matrix](../NOC-122/EQUIPMENT-COMPATIBILITY.md) and existing Site/Device/Asset/Link IDs.

|Equipment|Model/part and full firmware|Required collection evidence|
|---|---|---|
|MikroTikCCR1036|Operator supplies exact board/build|RouterOS6.43+ stable native API-SSL8729 or approved ROS7REST; CA/IP-SAN/read-only policy/source allowlist|
|MikroTikCCR2116|Operator supplies|Same; optional sensors/OSPF units and supported fields verified separately|
|MikroTikRB5009|Operator supplies|Same; no firmware certification from fixture labels|
|MikroTikCRS/netPower|Exact switch/OS (RouterOS vsSwOS)|Native RouterOS collector does not support SwOS-only devices; hardware-offload/PoE counters not assumed|
|WavePro/WaveLR/WaveAP|Exact product/build|**BLOCKED:** verified authPriv or documented secure UISP RF contract, official per-firmware ui-mib.zip/modules/hash/OID instances/units|
|AF60-XR|Exact platform/build|Do not assume Wave/olderairFiber MIB compatibility; official secure protocol/schema required|
|AF5XHD|Exact platform/build|No certified model-specific live profile; official MIB/secure mapped contract and fixture required|
|CambiumForce4600C|Exact part number/name (do not silently substitute an ePMP4600c AP or Force4625)|Official firmware/MIB/imports/authPriv, field/index/unit/capacity/subscriber authorization evidence|
|RACOMRAy3|Exact11GHz hardware/build|Official MIB/SHA256/imports, carrier/index/adaptive capacity/RSL/modulation/error/temperature mappings and authPriv|
|Aviatmicrowave|Exact platform/band/build|Official per-platform MIBs/carrier mapping, supported metrics/secure privileges; no generic API assumption|

Copy this **documentation-only** record for each device. It is not an activation configuration and contains no production endpoint or credentials:

```json
{
  "approved": false,
  "existing_site_id": null,
  "existing_device_id": null,
  "existing_asset_id": null,
  "existing_link_or_sector_id": null,
  "manufacturer": "OPERATOR TO SUPPLY",
  "model_and_part_number": "OPERATOR TO SUPPLY",
  "hardware_revision": "UNKNOWN",
  "full_firmware_build": "UNKNOWN",
  "management_protocol": "NOT VERIFIED",
  "endpoint_reference": "PRIVATE OPERATOR RECORD; no secrets here",
  "read_only_policy_reference": null,
  "allowed_noc_source_reference": null,
  "tls_ca_fingerprint_and_san_reference": null,
  "snmpv3_authpriv_vendor_support_reference": null,
  "official_mib_archive_filename": null,
  "mib_revision_and_sha256": null,
  "vendor_download_and_acquisition_date": null,
  "required_imported_mib_modules": [],
  "verified_field_oid_instance_unit_enum_mapping": [],
  "unsupported_metrics": [],
  "sanitized_synthetic_fixture_reference": null,
  "future_interval_seconds": 300,
  "future_concurrency": 1,
  "operator_acceptance_authorization": null,
  "live_test_executed": false
}
```

For Wave obtain its actual official **ui-mib.zip** and imported modules including UBNT-MIB.txt/UI-AF60-MIB.txt/UI-AF60-MIB-REV1.txt **where supplied by that exact firmware**. Preserve actual filenames/revisions/vendor provenance/hash; do not assume every archive includes all three. NOC122's official4.1.0/3.4.0archive requests were proxy403. Need vendor confirmation of SNMPv3authPriv or documented HTTPS UISP API/version/permissions exposing the RF fields, exact table INDEX/instance suffixes/units/sentinels and sanitized observations. Enterprise subtree roots/oldairFiber/community snippets are insufficient. No community-string or unverified HTTPS fallback.

MikroTik native API-SSL implementation remains unchanged: TLS1.2+, literalIPv4/8729 allowlist, verified CA/IP SAN, fixed print/property operations, bounded deadlines/rows/bytes and private credentials. Optional temperature stays unavailable without validated units; OSPF optional. Dedicated monitoring account generally read,api for native; exact release rights/REST policy validated by device administrator. No router configuration, plaintext8728 or certificate verification bypass is introduced.

Separate future authorization must approve each device/account/endpoint/egress and reviewed deployment secret/CA mounts. Begin one device at a time300s, compare readings and supported fields, validate stale/missing/auth/timeout/counter-reset/recovery using synthetic faults first. No live fault induction, configuration writes, notifications or automatic remediation. This worksheet grants no equipment access.
