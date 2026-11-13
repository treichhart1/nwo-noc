## 3.2.1-rc.1 (unreleased)

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
