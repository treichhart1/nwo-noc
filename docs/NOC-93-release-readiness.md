# NOC-93 consolidated release readiness — 3.2.1-rc.2

## Decision and scope

**NO-GO for production or live-equipment pilot activation.** This assignment authorizes neither. A green Actions run on the exact candidate SHA establishes **GO for further isolated synthetic evaluation only**. Real firmware, one-device endpoint/credential approval, enforced egress, host hardening, operational thresholds, longer soak and recovery ownership remain prerequisites. Synthetic telemetry does not establish vendor firmware compatibility or a production SLA.

NOC-92 PR #96 / `a5f318e333e3e0fe06bf7fa29b51818ddce27e41`, CI #472 (all seven jobs passed), is the baseline. NOC-93 uses `feature/noc-93-release-consolidation`, not a fresh branch from main. Backend health/OpenAPI, frontend package and lockfile share **3.2.1-rc.2**. The old NOC-91 rehearsal image is 3.2.1-rc.1 with schema 0010; the current candidate requires **0011_monitoring_reliability**. NOC-93 adds no database migration.

## Dependency audit and merge order

All **96 outstanding PRs were draft/open** in the captured baseline. [The complete snapshot](audits/NOC-93-pr-snapshot.json) records every branch, base and full commit SHA, including RF-73 through RF-81 dependencies. Run `python3 scripts/release/audit_stack.py` after fetching all branches. It requires exact reviewed branch tips, verifies inclusion and stable patch equivalence, and makes no merges.

| PR | Milestone | Exact reviewed development tip |
|---:|---|---|
|85|RF-81|c7e7eddca8a044d1efc24d953303cb376daf0f74|
|86|NOC-82 (logical replay)|34e54e226f93159adf7d2dc52548d36aad277b46|
|87|NOC-83|2f3f5edff1925104327d1f2c06f5504bf8398ae2|
|88|NOC-84|a0161602cd013ce76048b799631dd5e2ba746310|
|89|NOC-85|07ac49c150d3d972c3326e6106370b4fa68699b1|
|90|NOC-86|3dd74d6c9c5855fc01fa6de5ff0d9f947c79970e|
|91|NOC-87|f31b35eceeb99d6167c284fa15777825206f6616|
|92|NOC-88|47ec94159ffb2e41ea8b75720566301e2d196358|
|93|NOC-89|1258347a8e24a007620270bb10a4f431bb382c98|
|94|NOC-90|b343c39a9b58ccb1a720c2c4aaaabe2b55008e68|
|95|NOC-91|bf642bdb467b696c9a3778d51bf4ce816d0f82f7|
|96|NOC-92|a5f318e333e3e0fe06bf7fa29b51818ddce27e41|

**95 tips are direct ancestors.** #86 is not: its audit commit was replayed as `ad61cde` on the corrected RF-81 tip. Stable patch IDs match. A read-only `git merge-tree --write-tree` comparison between NOC-92 and #86 produced the identical NOC-92 tree (`b1175395ac493ba5d61043e27574dd60b32aa9df`), without changing a branch or creating a merge commit. This is evidence of source inclusion, not review approval.

Future integration order, requiring separate authorization: start from the reviewed `develop` foundation (`13a737f32bcf5dad8321f5e725a169805a96caa8`), review #1 → #85 in ancestor order, reconcile #86's older ancestry without re-cherry-picking its already included audit, then #87 → #96 → NOC-93. Retarget downstream PRs only as an approved integration train advances. Prefer ancestry-preserving merges; squash/rebase requires a newly reviewed stack and rerun. Do not blindly merge each old feature branch into another feature branch or treat an earlier failed tip as an unreviewed release promotion. Main is behind this accumulated feature stack. No merge, retarget, close or branch rewrite is performed by NOC-93.

No additional upstream implementation is missing from the pinned NOC-92 candidate. The audit replay is the only duplicate patch exception. API method/path uniqueness, shared application versions and a single Alembic head are executable regression gates. Foundation `rf_observation_v1`, radio `rf_radio_v2`, microwave `rf_microwave_v1`, legacy MikroTik observations and the rebuildable analytics projection are deliberately distinct contracts, not interchangeable duplicate schemas. Preserve their capability/missing-value semantics; do not concatenate their values into fabricated aggregates.

## Completed capabilities and combined acceptance

The stack provides authenticated inventory/topology, PostgreSQL/Redis services, bounded read-only collectors, durable scheduling/outbox/receipts, RF history/diagnostics, explicit historical projection, deterministic human-reviewed recommendations, alert deduplication and probable outage context, shared incidents, audit logs and monitoring health. RF self-healing is simulation-only. Integrations are disabled by default.

NOC-93's five-vendor PostgreSQL regression exercises **scheduled synthetic collection → historical raw storage → explicit idempotent projection → diagnostics/recommendations → RF operations views**, including actual microwave endpoint/link associations. MikroTik CPU/temperature/OSPF/Ethernet observations remain non-RF evidence; absent RF values are not invented. Existing NOC-83–92 suites remain unchanged and run alongside it. Historical projection and recommendation evaluation remain **explicit jobs**, not hidden side effects of telemetry polling. A pilot runbook must schedule/review these local stages if historical charts and recommendations are expected to advance.

Final exact SHA, test counts, CI job outcomes, resource measurements and remaining blockers are recorded in the NOC-93 draft PR and validation report after execution. A pending check is never a pass. CI includes the full backend, frontend build, PostgreSQL, migrations, Compose/build/egress checks, Ubuntu installer, all earlier browser stages and real monitoring service interruption acceptance.

## Reproducible Ubuntu 24.04 installation

Use a disposable Ubuntu 24.04 host/userspace, local Docker daemon, generated nonproduction credentials and internal-only Compose project. Follow [the installation guide](NOC-84-installation-and-recovery.md). Docker/bootstrap/build downloads require approved package access; runtime application containers remain on the internal network with no published service ports. Access uses the loopback-only relay. No host firewall or production server is changed by these scripts.

```bash
scripts/release/bootstrap-ubuntu.sh
scripts/release/install.sh /absolute/private/new-state
# After required images are locally available, capture the committed candidate:
python3 scripts/release/pin_images.py /absolute/private/new-state
# Validate/restart the SAME release identity:
scripts/release/install.sh /absolute/private/new-state
```

`pin_images.py` records full application/PostgreSQL/Redis image IDs, source commit and a hash of the private configuration. Capture requires committed source unless explicitly reproducing an archived full source SHA. The private manifest is validated on subsequent installation; missing images, omitted image identities, configuration drift or silently changing mutable tags fail closed. A different release needs a new private state/manifest. The manifest is a local release record, not signed supply-chain attestation: retain reviewed source, image archives, dependency manifests, platform architecture and operator provenance separately. Docker image IDs are daemon/platform-specific; preload the reviewed archive on a recovery host rather than silently pulling a newer tag.

Do not pin the mutable WISP scenario fixture configuration midway through its scripted edits. Pin the final reviewed configuration for a reproducible release. Store environment and credential files encrypted/off-host with least privilege; they are not embedded in public release reports. The generated acceptance credentials must never be reused for a pilot or production.

## Upgrade, failed upgrade and rollback

`NOC_BUILD_CA=/approved/ca.pem scripts/release/rehearse-upgrade.sh NEW_ABSOLUTE_STATE` is an executable isolated rehearsal. It refuses existing state/projects and retains volumes, backups and restored databases. It builds the actual NOC-91 images from the pinned Git archive, starts schema 0010, seeds synthetic immutable markers, and captures old image/configuration identities. Then it:

1. Creates and validates a pre-upgrade PostgreSQL dump.
2. Stops writers and injects a transactional DDL/data failure; verifies it rolls back.
3. Rejects unsafe candidate startup configuration before serving traffic.
4. Restarts the actual old images/configuration, authenticates and checks RF operations and original data.
5. Upgrades to the current images/schema 0011 and verifies retained raw telemetry/findings and authenticated API operation.
6. Restarts candidate services and verifies health.
7. Restores the old dump to a new `restore_...` database; launches the actual old application on its matching schema and authenticates/checks data there.
8. Verifies the current candidate/original database remain unchanged by that isolated restore.

This covers transactional migration/configuration fault injection and matching-image recovery, not every future migration's failure mode. It does not downgrade a populated current database, overwrite a recovery target, switch production traffic or certify VM boot/systemd/firewall behavior.

For a future failed upgrade: disable polling and stop NOC writers; retain pending private outbox and old manifest/images/configuration; preserve the failed database for diagnosis. Restore the verified pre-upgrade backup into a **new isolated project/database**, use its matching old image/schema, validate health/auth/data and audit/outbox consistency, then seek separate approval for traffic changes. Never run an old app against an unknown new schema or blindly replay an outbox into a different restored dataset. Durability starts after fsync; disk loss requires verified off-host recovery. Archive database, outbox, environment/configuration, reviewed mappings and image identities as a coordinated recovery set. Existing [backup/restore scripts](../scripts/release/restore.sh) refuse overwrite.

## Future one-device read-only pilot procedure — not authorized now

1. Record an independent approved change, device identity/vendor/model/firmware, exact immutable candidate SHA/images and rollback owner. Select **one** management endpoint; pilot v1 is RFC1918 IPv4. No subnet scans, discovery or fleet UISP synchronization. Disable all other polling jobs and TCP monitoring targets; inspect inventory for unintended enabled targets.
2. Have the equipment administrator provision minimum-privilege monitoring credentials separately. RouterOS 7: a dedicated `read`/`rest-api` group restricted to the NOC source and certificate-verified HTTPS; exclude write/policy/reboot/sensitive permissions. Radios: SNMPv3 authPriv with approved auth/encryption algorithms and GET-only views for validated model/firmware OIDs. Actual device permission behavior/MIB support still requires validation. Never use administrator credentials or place secrets in the plan, logs, repository or browser.
3. Mount private owned mode-0600 credentials read-only, use unique secrets and rotate/disable them after the trial. Validate TLS certificates/identity; do not bypass certificate checks. Keep a short operator JWT lifetime (15 minutes recommended, no more than 30 for the pilot) and restrict roles. Logout currently removes a browser token rather than revoking already issued JWTs; explicit expiry/revocation controls remain an operational security consideration.
4. Keep the existing internal-only default. A separately reviewed pilot gateway/firewall must allow only the approved device IP and UDP 161 (SNMPv3) or TCP 443 (RouterOS HTTPS); deny other management IPs, DNS discovery, internet and metadata destinations. Do not replace this with host networking or expose PostgreSQL/Redis. Record enforcement tests. This assignment does not install those external rules or verify real egress to equipment.
5. Use exactly one explicit validated mapping/link association and one private scheduler entry; concurrency/batch 1, interval at least 60 seconds (300 recommended), bounded timeouts/backoff and existing manual/scheduled rate guards. Keep UISP fleet synchronization, explanation-provider networking and all other integrations disabled. No writes, reboots, configuration changes or RF remediation are permitted.
6. Offline consistency-check the private **metadata-only** plan with `python -m app.pilot_plan PLAN.json`. The checker rejects extra devices/secret fields, broad/nonprivate endpoints, mismatched vendor protocols, insecure or missing approval assertions and unsafe rates. It performs **zero network calls and never enables anything**. Boolean approval assertions are not proof of authorization; the operator must provide independent evidence.
7. Under separate future authorization, compare initial read-only measurements to an independently observed device UI/export, verify timestamp/units/missing capabilities, inspect receipts/last success/stale handling, explicitly run the local projection and recommendation evaluation, review diagnostics/alerts/incident context and audit trails. Operator investigations must retain uncertain root cause. Use a bounded observation window with a named observer; no production-impact conclusions from synthetic data.
8. Emergency disable: remove/revoke the single approved network allowance and monitoring credential through the independently approved operational procedure, set NOC scheduler/integration enable flags false, stop the NOC worker and confirm no monitoring connection attempts remain. Preserve the private outbox, database and audit evidence. Do not reboot equipment, change RF settings or delete pending deliveries. Restart only the isolated NOC services after reviewed recovery.

## Performance, security and open risks

The 40-site 250/500/1000-device benchmark measures actual PostgreSQL work, sample/receipt uniqueness, polling throughput, collection/alert processing, query latency and allocated source-history table/index size. It now reports Python CPU seconds, average one-core CPU percentage, resident memory and process-lifetime peak RSS. Source latency is synthetic 2ms; schedule time is virtualized. These are not real radio response times or sustained scheduling SLAs. A 1000-device/15-second fleet cadence remains unproven and previously exceeded that interval.

`resource_monitor.py` samples only the explicitly validated isolated project's Docker CPU/memory during synthetic recovery acceptance. It records observed maxima and sampling errors; sampling can miss short spikes. Python-process metrics exclude PostgreSQL and other containers, and Docker resource measurements are a separate recovery workload, not full-host sizing. Retain raw outputs and measurement scope with reported values.

Remaining risks/gaps: per-firmware documented read-only MIB/REST validation (including airMAX, ePMP 4600, Racom/Aviat carrier mappings and unsupported RouterOS 6), clock synchronization, long-duration threshold/trend-baseline tuning, redundant-path forwarding validation, outbox/source/attempt retention and disk capacity, verified off-host configuration/credential recovery, artifact provenance/security maintenance, host/VM boot and firewall hardening, and real one-device enforced egress/credential approval. The normal docker-compose.yml exposure guard is not an egress firewall. No unauthenticated API contract or missing Alembic head is accepted by release gates, but passing tests are not a general security certification.

Do not modify AI Fiber Designer or MikroTik Management; no live systems, production credentials, production databases, device commands, deployments, PR merges or automatic remediation are part of this release-validation assignment.
