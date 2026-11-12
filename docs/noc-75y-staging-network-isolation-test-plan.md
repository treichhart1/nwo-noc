# NOC-75Y — Staging network isolation acceptance plan

**Status:** Operator-run test plan only. No network tests, firewall enforcement, staging provisioning, or production changes have been performed.

## Scope and prerequisites

Run only on a separately approved, disposable staging VM or network. Obtain operator authorization, record the staging host/VM ID and intended interfaces, and verify that tests cannot affect the production network. Do not connect staging to management VLANs, production VPNs, or production credentials. Use synthetic services and data.

The offline manifest in `docs/noc-75x-staging-egress-policy.json` is documentation, **not** an installed firewall. Validate its syntax with `python3 scripts/validate_staging_egress_policy.py docs/noc-75x-staging-egress-policy.json`, but never treat that as evidence of actual isolation.

## Acceptance tests (performed and signed off by operator)

| Test | Required outcome | Evidence to retain |
| --- | --- | --- |
| VM NIC/VLAN/route inspection | No path to production management networks | Sanitized interface, route, and hypervisor network configuration |
| Host and container firewall inspection | Outbound deny-by-default for IPv4 and IPv6; explicit approved exceptions only | Sanitized effective firewall rules and container network policy |
| Production destination reachability | No successful connections to any approved **non-sensitive test targets** representing production routes; never probe live management devices without permission | Timestamped blocked-connection results and firewall counters |
| DNS resolution and alternate paths | No DNS or alternate route bypass to management endpoints | Sanitized resolver, routes, and denied-attempt logs |
| IPv6 and dual-stack escape | IPv6 outbound remains blocked even if IPv4 is denied | Sanitized IPv6 routes and blocked-attempt results |
| Inbound exposure | Backend/frontend loopback or VPN-only; DB, Redis and worker not exposed | Effective listener and port mapping inventory |
| Integration controls | No UISP tokens, live MikroTik credentials, scheduled RF collection/pruning, or outbound alert delivery | Redacted environment/config audit and application startup logs |
| Restore/rollback | Disposable staging snapshot can be restored without touching production | Restore drill outcome, duration, and operator signoff |

## Stop conditions

Immediately stop staging tests and isolate the staging VM if any live production connection succeeds, a credential is exposed, an unapproved route appears, or outbound integration traffic is detected. Preserve redacted logs and record the incident. Do not remediate by changing production firewalls or live device configuration.

## Signoff record

- Date and operator:
- Staging VM/network identity:
- Branch and commit SHA:
- Effective network/firewall policy evidence:
- Test results (pass/fail/not run) and artifacts:
- Exceptions and remediation:
- Rollback/restore result:
- Operator approval for **staging only**:

**Acceptance rule:** Any unrun, failed, or undocumented test blocks isolation signoff. Passing CI, a policy manifest, or static configuration lint alone is insufficient. This plan never grants production deployment approval.
