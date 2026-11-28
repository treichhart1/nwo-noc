# Official acquisition evidence — 2026-11-28 UTC

No source below establishes a verified secure native telemetry contract. Product specifications are not firmware/MIB compatibility certification. Third-party MIB mirrors and search snippets were not adopted as authoritative mappings.

| Model | Official product evidence | Firmware / secure field contract |
|---|---|---|
|Wave LR|[Wave LR](https://techspecs.ui.com/uisp/60ghz-wireless/wave-lr)|BLOCKED; exact installed build and module/security evidence required|
|Wave Pro|[Wave Pro](https://techspecs.ui.com/uisp/60ghz-wireless/wave-pro)|BLOCKED; lists UISP/SNMP, not authPriv/version/OIDs|
|Wave Nano|[Wave Nano](https://techspecs.ui.com/uisp/60ghz-wireless/wave-nano)|BLOCKED; no inherited LR mapping|
|Wave AP|[Wave AP](https://techspecs.ui.com/uisp/60ghz-wireless/wave-ap)|BLOCKED; AP station/index contract missing|
|Wave AP Micro|[Wave AP Micro](https://techspecs.ui.com/uisp/60ghz-wireless/wave-a-p-micro)|BLOCKED; per-model table evidence required|
|Wave AP Gen2|[Wave AP Gen2](https://techspecs.ui.com/uisp/60ghz-wireless/wave-ap-gen2)|BLOCKED; not assumed same as AP|
|Wave Pico|[Wave Pico](https://techspecs.ui.com/uisp/60ghz-wireless/wave-pico)|BLOCKED; exact build/schema required|
|airFiber60|Original AF60 requested by operator; [official60GHz catalog](https://techspecs.ui.com/uisp/60ghz-wireless)|BLOCKED; original model/build not established by other AF60 specs|
|airFiber60LR|[AF60-LR](https://techspecs.ui.com/uisp/60ghz-wireless/af-60-lr)|BLOCKED; independent firmware family evidence required|
|airFiber60XR|[AF60-XR](https://techspecs.ui.com/uisp/60ghz-wireless/af60-xr)|BLOCKED; product lists PtP, UISP; no secure field contract|
|Other60GHz|[Vendor catalog](https://techspecs.ui.com/uisp/60ghz-wireless) also lists AF60XG/HD/GigaBeam|Not implemented; no automatic family matching or legacy mapping|

Bounded15s urllib reads of these exact official URLs returned `URLError: Tunnel connection failed: 403 Forbidden`:

- `https://ui.com/downloads/firmwares/wave/v3.4.0/ui-mib.zip`
- `https://ui.com/downloads/firmwares/wave/v4.1.0/ui-mib.zip`

Browser retrieval of3.4.0 also reported inaccessible. **No bytes obtained; SHA256, module revisions/imports and fields are NOT VERIFIED.** Preserve earlier NOC121/122403 evidence; this is a new failed acquisition, not a fabricated successful archive check.

[Official MIB revision release thread](https://community.ui.com/releases/aef449c9-51e8-4373-bac8-06e2eab4b23e) rendered only a JavaScript loading shell. [Official4.1.0 release](https://community.ui.com/releases/Wave-PTMP-PTP-4-1-0/8f6eb0b1-eed8-401a-b9af-595065f97502) is historical provenance, not proof all devices use that firmware.

[Ubiquiti API guidance](https://help.uisp.com/hc/en-us/articles/22590956856087-UISP-CRM-API-Usage) states Network API documentation is on each console at `/api-docs/`. Its CRM examples do not establish Wave RF endpoints or fields. No live console was accessed; request an operator-exported sanitized OpenAPI/schema, version, GET path, units, permissions and fixture. Do not implement an invented Wave endpoint or infer RF schema from inventory identifiers.
