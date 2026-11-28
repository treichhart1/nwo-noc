# NOC-133 — Wave / 60 GHz monitoring evidence foundation

Base: NOC-132 draft PR135, exact commit `401270657f499eae1b1532f118eef7051ccb448e`, tree `a61c354f06633ccd78c64a4d8a539bc4a22db424`. Verified run892 succeeded with all13 jobs. Branch `feature/noc-133-ubiquiti-wave-monitoring`, stacked without merging. No historical migration, dependency lock or monitoring benchmark changed.

## Scope and audit

NOC86 UISP inventory uses the existing allowlisted TLS read-only client. `uisp_rf.py` only accepts explicitly named/unit-tagged fields; it is **not** a verified Wave API schema. NOC87 `radio_snmp.py` has four closed airMAX/ePMP profiles, not Wave. Its authPriv GET transport cannot be reused with guessed OIDs. NOC88 microwave adapters are separate. NOC89 analytics, NOC90 advice, NOC97 incident correlation and NOC98 tower health consume canonical evidence; unavailable Wave values must never be replaced with zero/healthy. NOC121/122/123 documented vendor MIB/secure protocol blockers, which persist.

Implemented the evidence-independent portion explicitly permitted by this assignment:

- Closed, bounded, versioned **synthetic-only** Wave observation schema with strict units, model identity, timestamps, directional capacity/traffic, counters, Ethernet, primary/backup path and station-table evidence.
- Existing `Device`, `NetworkLink` and `TelemetrySample` IDs/storage; no new device/link/incident/queue/collector database. PostgreSQL transaction advisory locking, timestamp identity, identical replay deduplication, conflicting replay rejection and caller-owned rollback.
- Existing link endpoints must match. AP/subscriber references must already exist in synthetic inventory. Partial tables do not prove disconnection; complete membership absence is labeled absence, not service outage.
- Bounded history, explicit unknown/stale/invalid evidence, reset/restart-aware derived rates, primary/backup transitions, interval observations (not continuous failover duration proof), multi-sample synthetic recovery evidence and advisory recommendations with assumptions.
- Offline weather correlation requires source/timestamp alignment; it never asserts causation. No weather provider or downloads.
- Authenticated operator readiness/history screen integrated into RF diagnostics. Admin-only staging accepts only `offline_mode=true`, `app_env=staging` and enabled matching synthetic devices. No native activation endpoint, network requests, credentials or scheduler registration.
- A preview projects compatible numeric fields into the existing RF vocabulary but **is not automatically published**. Synthetic Wave data is isolated under `wave_evidence_v1`, avoiding contamination of production Down Detector, tower health and Grafana projections. Those live integrations remain BLOCKED until an authoritative native contract is available.

## Limits and release decision

**Native equipment acceptance: BLOCKED. Production/live pilot: NO-GO.** No model/firmware pair is certified. Supported synthetic model names are an inventory catalog, not protocol approval. No MIB parser/collector is claimed: official bytes, dependency modules, security modes, indexes, units and firmware compatibility are unavailable. No community OIDs, insecure fallback, scraping, discovery, radio writes, live UISP or actual device connections were used.

No schema migration required; current migration head remains0035. Existing monitoring scheduler/outbox/sample/receipt assertions, durable PostgreSQL settings, CPU/memory limits and all13 CI jobs remain unchanged. Focused Wave tests are added to the real PostgreSQL job. Full final-commit CI, image security, Ubuntu recovery and strict benchmark results are recorded in the draft PR final evidence ledger, rather than inferred from base CI or skipped local tests.

See [official evidence](VENDOR-EVIDENCE.md), [operator guide](OPERATOR-GUIDE.md) and [machine-readable acceptance](ACCEPTANCE.json). Existing vendor-native risks, licensing, production signing, real VPN/MFA, physical off-site recovery, native Powercode and real provider acceptance remain separate blockers.

Local pre-CI validation:34 focused tests passed;7 PostgreSQL cases skipped because no local disposable DB exists (not passes). Frontend TypeScript/production build passed. Strict reviewed static gate:83 reviewed/0 unreviewed/0 HIGH. Native equipment/MIB compatibility remains BLOCKED regardless of synthetic tests. Final exact-source results are recorded in the draft PR ledger.
