# NOC-108 validation evidence

Development baseline: NOC107 draft PR111 at d0078b5b2e7307575d6d0864eac87160c3532e5a,
with passing upstream CI555/556. All upstream development ancestors are retained.
Nothing has been merged or deployed.

## Measured local acceptance

* Focused PostgreSQL analytics suite: final counts recorded in the PR after validation.
* Native mobile browser workflows:3 passed,1 unrelated RF test skipped by its own
  fixture guard. Recorded CPU report/chart, unavailable RF data, CSV download,
  synthetic health labeling, provisioning export, secure links and invalid UID rejection.
* Private and public/signup frontend production builds passed. Existing bundle-size
  advisory remains non-failing.
* Clean PostgreSQL16 schema migrated through27; Alembic drift check found no new
  operations; empty-schema27→26→27 roundtrip passed.
* Optional Grafana Compose configuration validated without launching a deployment.
* Actual isolated Grafana12.3.3 container, pinned digest
  `sha256:62a54c76afbeea0b8523b7afcd9e7ee1f0e39806035fd90ffc333a19e9358f2f`:
  10 provisioned dashboards,61 successful panel queries, datasource health OK,
  anonymous dashboard denied, approved recorded query passed, raw telemetry table
  query denied using the dedicated reader. This test used synthetic data, private
  Docker networking and loopback HTTP. It does not validate production TLS/SSO,
  embedding or compatibility of every Grafana release.

Initial local checks exposed an unpublished migration-generation mismatch and
untyped optional PostgreSQL filter parameters. The migration was rebuilt against
an empty disposable schema; filter casts and a regression were added. A browser
link assertion was corrected to the actual safe dashboard route including its
constant slug. No security assertions or functionality were removed.

## Synthetic performance

40 POPs, no external calls, all benchmark transactions rolled back:

| Devices | Recorded samples | Ingest s | Report latency ms (3 runs) | Hourly rollup s | Derived rows | Python peak RSS KiB |
| --- | --- | --- | --- | --- | --- | --- |
| 250 | 3,000 | 0.241 | 70.81 /68.36 /55.74 | 0.436 | 2,500 | 92,568 |
| 500 | 6,000 | 1.313 | 93.74 /158.36 /130.21 | 1.690 | 5,000 | 108,040 |
| 1,000 | 12,000 | 2.710 | 184.31 /210.80 /196.27 | 2.856 | 10,000 | 135,440 |

These single-process synthetic measurements exclude PostgreSQL/Grafana server
memory and are not sustained multi-user production capacity claims. Native
monitoring regressions retain their existing worker/recovery/load checks.

## Release decision

Suitable for isolated, synthetic acceptance after all CI jobs pass. **No-go for
unapproved production use**. Gates: current patched image/dependency review,
private DNS/firewall/VPN and PostgreSQL TLS, Viewer role and data-source privilege
review, credential file ownership, validated backup/restore, bounded historical
backfill and retention/load sizing, real source-data quality/firmware acceptance.
SSO/embedding and arbitrary long-history late-data repair remain unverified or
manual as described in the operator guide. Financial information remains in the
administrator-only NOC module; it is deliberately excluded from Grafana metrics.

Exact commit, full regression counts, Ubuntu24.04 service/browser results and
GitHub Actions links are recorded in the draft PR once those checks complete.
