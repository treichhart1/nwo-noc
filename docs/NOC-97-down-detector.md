# NOC-97 — recorded-signal Down Detector

## Baseline and safety decision

Built on unmerged NOC-96 draft PR #100, `1f401d0aebdc8159a0bc23b46fd25969adc9aa91`, verified CI #498, retaining NOC-95 #99 at `b27a4683f1300e108e748260cac2940f1cefa224` and the complete NOC-94/RF chain. No upstream PR is merged or retargeted. Future authorized integration order remains reviewed develop foundation → #1–85 → reconcile #86's identical replay without duplication → #87–100 → NOC-97, then revalidate the integration commit. Version remains development candidate3.2.1-rc.3. Additive migration0016 follows unchanged0015.

**GO for isolated synthetic framework acceptance. NO-GO for live equipment, live Powercode, real SMS/Slack or production deployment.** The detector performs database reads/writes only. It introduces no ping/SNMP/HTTP transport, scanning or equipment action. Existing collector authorization, allowlists, TLS, timeout/concurrency controls and RF simulation-only safeguards remain authoritative. No native device compatibility is established by these tests.

## Architecture and observed evidence

`app.down_detector` separates closed contracts, durable observations/state/episodes/timeline, deterministic correlation, APIs, the recorded-input worker bridge, rehearsal and benchmark. Existing Device/Site/Sector/NetworkLink and Powercode service records remain the only inventory/topology sources. Existing incidents are shared with RF Operations and Notifications, rather than a separate ticket system.

The normalized contract accepts a registered inventory target, bounded event ID/source, aware observation timestamp, fixed boolean-or-unknown signals and optional finite measured uptime/loss/latency/utilization/voltage. Signals cover ICMP, SNMP, telemetry, interfaces, OSPF, wireless/microwave/fiber availability, collector heartbeat and commercial power/UPS/battery/supply. Unknown stays NULL, never false/zero. Arbitrary raw strings, credentials and unregistered fields are rejected. Receipt time and source observation time are separate.

These are **recorded-signal inputs**, not newly implemented ICMP or UPS vendor collectors. The synthetic ingress requires active administrator access, offline staging and explicit synthetic monitoring enablement; it accepts only synthetic sources, maximum200 observations per request. There is no public/native ingestion endpoint.

The worker consumes up to200 fresh TelemetrySample rows per transaction, ordered by observed time/ID and excluding already recorded sample IDs. It recognizes existing validated Ubiquiti/Cambium radio observations, Racom/Aviat microwave observations and MikroTik router snapshots, plus the explicit closed `down_detector_v1` envelope used by synthetic acceptance. Radio status remains one signal; unavailable interface/routing/power values are not invented. MikroTik snapshots preserve OSPF/interface state and uptime; a successful API receipt is not proof of end-user service. Unsupported/malformed formats are recorded as unknown so they cannot starve the bounded bridge or fabricate outages.

## Detection and recovery rules

Defaults require3 confirming observations spanning60s, two independent named availability signals, fresh data within180s, and3 successful recovery observations spanning60s. Signals are semantically distinct measurements; accepting a normalized envelope is not certification that its upstream producer has independent failure domains. Validate source provenance before a pilot.

- Multiple sustained availability failures identify probable device/sector/backhaul/fiber/path loss. OSPF/interface failures without device-reachability failures describe a network path, not a powered-off router.
- Mixed successful and failed availability evidence describes partial degradation; successful telemetry with ICMP/SNMP failures describes management connectivity failure.
- Collector failures become degraded monitoring episodes, never proof that equipment lost power. Missing data resets confirmation/recovery runs and leaves active outages unresolved.
- Explicit commercial-power loss confirms **commercial** power loss only. If equipment telemetry remains available, Notifications uses technician-only UPS warning evidence rather than selecting customer SMS recipients.
- Multiple failed availability signals plus an explicit UPS/battery/supply alarm support suspected tower power failure. Simultaneous device loss alone never confirms power loss. Voltage is retained without inventing a vendor threshold.
- Uptime decreases record reboot/reset evidence; they do not identify its cause.
- High utilization can generate degraded evidence; existing RF diagnostics remain the authority for RSSI/SNR/noise/modulation/capacity analysis.

Event IDs/body hashes detect identical replay and conflicting IDs. Different recorded events at the same target/source/timestamp are retained without counting the observation twice; late events are preserved without advancing confirmation/recovery counters. Gaps reset consecutive runs. Flapping cannot repeatedly close/reopen on single successful polls. A crash before commit rolls observations, counters and episode changes back together; replay resumes safely. A PostgreSQL transaction advisory lock serializes multiple detector processes; bounded statements fail rather than lose durable input.

A successful poll cannot clear an original power/routing/interface failure whose recovery is still missing: every confirmed failed signal must be explicitly true in the recovery observations. The required-signal ledger survives cause changes, including a subsequent collector failure. Commercial-power loss without affirmative service-down evidence is degraded coverage, not an offline tower. UPS/battery/supply warnings also remain degraded while service connectivity works; site counts distinguish degraded from unknown.

No incident closes until the primary meets stable recovery criteria **and every recorded correlated symptom has recovered**. Missing upstream evidence preserves the existing symptom association rather than producing new independent downstream campaigns. Incident recovery time includes the last recovered downstream symptom. Manual closure through existing shared incident workflows remains a human action, not monitoring proof; the detector's restoration-preview gate still requires fresh stable state.

## Topology and probable causes

Core POPs and existing undirected site links define recorded paths. Removing a failed link must disconnect a previously reachable site before that link can explain its downstream symptoms. A remaining recorded alternate path prevents that inference. Same-site sustained outages and explicit device parent chains also support temporal correlation; cycles cannot suppress every member. Correlation requires observations within the configurable300s default window.

A uniquely separating link takes precedence over a co-located site symptom. Multiple competing causes remain uncertain; missing core/dependency metadata is not invented. Primary episodes create one incident; simultaneously correlated symptoms share it. Original observations and episode relationships remain available for audit. A root discovered after independently created incidents does not silently erase/close historical incidents; operators must review those earlier incidents and existing notification cooldowns still apply. Multi-failure cut sets and continuous native path verification remain future work.

## Customer impact and notifications

Impact uses NOC-95's reviewed service mappings and existing redundancy/path analysis. It reports confirmed/probable/unknown/not-affected service counts, distinct potentially affected accounts, service technology totals, affected site/sector/fiber IDs and remaining recorded paths. These are dependency estimates, not end-user reachability certification. General detector responses contain no customer names, contact numbers, billing fields or credentials. Private mapping corrections remain in Customer Impact and never write Powercode.

Primary episode notification previews call NOC-96 `create_plan`; child previews are rejected. Repeated preparation deduplicates against the same incident/kind. Drafts enqueue nothing. Current mappings, evidence freshness, consent timestamps/preferences/STOP/suppression, maintenance, quiet hours, cooldown, rate/cost/recipient caps and reviewed fingerprints remain enforced at approval and processing. Restoration previews require stable primary/downstream recovery; incident closure alone is insufficient. Updates/escalation, template review and explicit simulation approval use the existing Notifications interface. No external provider is available.

Deterministic troubleshooting recommendations cite observed failing signals/event IDs: microwave/OSPF/power/collector/access-radio history or observed uptime decrease. These recommendations do not invoke an external model or claim a field-confirmed cause. Open the shared incident for existing RF analysis and technician investigation. No automatic remediation exists.

## Operator interface and audit

Down Detector is part of the existing application navigation. It displays site coverage, stale/degraded components, critical incidents, episodes, probable root/symptom relationships, observed durations, recovery, history, dependency visualization and filters for site/device/link/severity/type/technology/status. Unknown site coverage is separate from online/offline counts. Technology filters use explicit reviewed customer service mappings, never vendor guesses.

Inspect an episode for aggregate impact, original event references, recommendations, paginated timeline, shared incident events and notification approval audit. `/episodes/{id}/observations` exposes bounded original normalized observations with receipt/source timestamps. Authorized operators can acknowledge or override an assessment with an audited explanation. Overrides cannot assert confirmed power; they remain operator assessments. Credential-like notes are rejected. Viewer/anonymous actions are denied; the shared private-data authorization gate audits denied access.

History reports observed episode frequency, completed component outage durations and MTTR. **Time availability percentage is deliberately unavailable** because continuous independently verified service coverage is not established. Missing time is not counted as uptime. MTTR/history windows are bounded and do not establish a production SLA or customer-verified restoration. Cross-source interference scoring, dense coverage accounting and native power mappings are not certified here.

## Configuration and operation

```dotenv
DOWN_DETECTOR_ENABLED=false
DOWN_DETECTOR_INTERVAL_SECONDS=30
DOWN_DETECTOR_POLICY={}
```

Policy keys: confirmation_samples2–10, confirmation_seconds15–3600, recovery_samples3–10, recovery_seconds30–3600, stale_seconds30–3600, correlation_seconds30–1800, independent_signals1–5. These tune recorded-event interpretation; they never authorize transport. The detector runs in one dedicated worker thread at the configured15–300s interval, isolating it from RF claims/heartbeats and notification processing. Existing worker health and sanitized logs remain available. Disabling the flag and restarting workers stops new detector processing while preserving evidence/history. Existing integration emergency-disable and notification pause/latch controls are unchanged.

For stale/missing observations, inspect source timestamps/collector health; do not manufacture good signals. For conflicting/replayed IDs, verify producer identity and preserve existing evidence. For uncertain roots, correct reviewed topology rather than force a diagnosis. For database interruption, restore connectivity and resume the worker; never delete receipts, reset counters or bulk reapprove messages. The authoritative pending input is PostgreSQL telemetry; Redis loss cannot remove detector observations. The bridge intentionally ignores input older than the freshness window for live detection; retained raw telemetry remains available through historical RF tools.

Before0016 use existing private backup/immutable-image procedures. Restore into a NEW isolated database with matching images; rotate signing secrets/re-authenticate as described in NOC-94. Preferred rollback uses matching pre-upgrade backup and old images. Downgrade deletes the new detector tables/history and is not an operational recovery substitute. All earlier migrations/security controls are preserved.

## Validation and remaining pilot prerequisites

Tests cover device/sector/tower/confirmed-commercial/suspected-power/backhaul/fiber/path/collector/management/degradation states, unknown/stale/malformed evidence, loss/flapping/replay/reordering, redundant/unknown/parent/cyclic topology, independent failures, partial/stable recovery, reboot observations, customer impact, maintenance and notification dedupe, privacy, unauthorized actions, worker transaction replay and PostgreSQL restart. CI retains all preceding backend/frontend/RF/customer/SMS/Slack checks and adds detector tests, scale measurement, Chromium investigation/preview and durable recorded-input restart acceptance.

Actual local250/500/1000-device/40-site synthetic benchmark:753/1503/3003 observations ingested in6577.37/12924.94/22546.67ms; correlation267.54/426.60/886.63ms; repeated reconciliation79.34/210.95/420.99ms; one primary incident with250/500/1000 symptoms and no duplicate incident creation. Peak process RSS at1,000 devices98,912KiB. Each fixture ran in one rolled-back PostgreSQL transaction with zero network requests. These are recorded-ingestion/query measurements, not probe throughput, native collector accuracy or production sizing. Final exact counts/commit/CI measurements are recorded in the draft PR and validation report.

Before any controlled pilot: separately approve one device/endpoint, minimum-privilege read-only credentials, native signal and UPS/voltage mappings, routing/interface scope, source independence/timestamps, exact egress/TLS and polling budgets; validate actual redundancy/customer assignments; review late-root/ambiguous multi-failure incidents, continuous coverage/availability accounting, database capacity/retention and off-host recovery. NOC-94 host controls, NOC-95 native Powercode gaps and NOC-96 legal/native-provider gates remain. No live device, Powercode, SMS, Slack or production operation is authorized by this milestone.
