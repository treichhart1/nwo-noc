## 3.2.1-rc.1 (unreleased)

- NOC-89: add indexed historical RF analytics projection/migration 0008, bounded
  PostgreSQL baselines/comparisons, suspected-interference/congestion evidence,
  transparent scores/confidence, recurrence and filtered analytics charts.
- Keep projection/retention separate from collection; provide synthetic-only
  bounded retention, source preservation and 250/500/1000-device query checks.

- NOC-88: add disabled GET-only SNMPv3 Racom Ray3/Aviat microwave contracts,
  dual/multi-carrier history, original values/units, engineered-reference fade
  checks, hysteresis diagnostics, topology correlation and backhaul charts.
- Keep RF, Ethernet and collection failures distinct; retain uncertainty and
  simulation-only incident/remediation behavior. No verified vendor OIDs ship.

- NOC-87: add disabled, explicit SNMPv3 authPriv GET-only radio polling with
  administrator-validated firmware/MIB maps; no guessed vendor OIDs or endpoints.
- Add Ubiquiti airMAX AC/legacy and Cambium ePMP 4600/legacy capability profiles,
  typed historical RF values/units, counter continuity, consecutive diagnostics,
  synthetic incident correlation and radio history charts.

- Extend NOC-86 with typed RF observations, explicit-unit Ubiquiti/Cambium/Racom/
  Aviat export collectors, availability/provenance and RF dashboard details.
- Preserve legacy RF diagnostics and block unsupported RouterOS 6 REST fallback.

- NOC-86: default-disabled GET-only UISP/RouterOS integrations, exact HTTPS
  allowlists, bounded retries/pagination, secret files and credential-free status.
- Add source bindings, guarded reconciliation/provenance and integration migration
  0007; retain RF savepoint and operational acceptance coverage.

- NOC-85: fresh-check correlation and operational dashboard; root/maintenance
  suppression, synthetic incident lifecycle and 40-site WISP fixtures.
- Batch concurrent monitoring with transaction-level deduplication, stable outage
  timestamps, no false initial recovery, and actual worker scale/restart checks.

- NOC-84: isolated Ubuntu bootstrap/installation, runtime/browser acceptance,
  guarded backup/restore, offline configuration/monitoring and bundled map assets.
- Require operator/admin roles for mutating APIs; add worker Redis heartbeat health.
- Add Ubuntu container rehearsal and recovery/browser checks to CI release gate.

- Reconcile RF-81's corrected test with the NOC-82 audit stack.
- Require three consecutive severe RF samples for simulation preview; invalid,
  healthy, and mild intervening samples block a proposal.
- Align API/health/frontend versions; pin release CI to Ubuntu 24.04 and require
  frontend success in the final offline acceptance gate.
- Use the npm lockfile in Docker and portable frontend validation.
- Document isolated installation, rollback, dependency audit and validation limits.

# Changelog

## 3.1.0
- Added built-in TCP reachability monitoring for UISP and manually managed devices.
- Added configurable host, port, interval and timeout per device.
- Added two-failure down-state protection to reduce false alarms.
- Added 30-day monitor history and state-change event storage.
- Added monitoring summary, manual check and event acknowledgement APIs.
- Added monitoring controls and status in the device drawer.
- Worker now runs monitoring every 15 seconds while UISP synchronization remains independently scheduled.

## 3.0.0
- WISP operations, incidents, maintenance and telemetry foundation.
