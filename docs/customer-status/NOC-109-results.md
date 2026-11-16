# NOC-109 local validation and activation status

Baseline: draft PR112 at0097b4eda5f77ad691470459007f93f5cd4fb7e1,
with CI567/568 passing. All upstream source/dependency relationships are retained.

## Actual local evidence

* Complete backend run:1,648 passed,58 existing dependency warnings,460.77s.
  This run preceded the final eight regressions. Final focused module:64 passed,
  2 dependency warnings,30.49s; includes those regressions and final security checks.
* Combined new status/native notification suites:179 passed before the final three
  unit-only privacy/credential regressions. Suites overlap and are not additive.
* Native/public browser acceptance:5 passed,12.8s. Covers disabled public defaults,
  mobile approval/publish flow, public privacy/WordPress contract, independent SMS
  draft/recovery and manipulated endpoint rejection. No live WordPress changes.
* Private, public-status and existing signup frontend builds passed. Existing
  bundle-size advice remains non-failing. Public bundle has no NOC auth/customer UI.
* PostgreSQL clean migration, schema drift and28→27→28 roundtrip passed.
* Optional internal-only public Compose configuration parsed successfully; it was
  not deployed. Restricted DB-role tests deny source/customer/private revision reads.
* A populated synthetic backup restored into a new isolated database:1 public case,
  1 public revision,1 message bridge,12 communication events,200 accounts/services,
  1 native notification plan,3 incidents,3 assets and5 financial entries retained.
  Approved public projection returned1 announcement. Original DB/backup preserved.

Initial browser validation found a selector/accessibility issue: the baseline
select's label included option text. An explicit accessible name fixed it; all five
workflows then passed. Initial benchmark setup reused a unique network-link name;
synthetic names were made unique and all benchmark transactions were rolled back.
No production bug or test assertion was hidden by weakening validation.

## Measured40-POP benchmark

| Customer services | Eligible contacts in selected tower | Impact preview ms (3 runs) | SMS draft ms | Python peak RSS KiB |
| --- | --- | --- | --- | --- |
|250|7|88.06 /24.87 /36.16|108.54|84,556|
|500|13|56.04 /52.92 /46.88|99.39|93,044|
|1,000|25|77.36 /55.11 /90.56|84.28|105,252|

Synthetic core-to-tower topology and Powercode-shaped customer/service fixtures,
zero provider calls, no published announcements in the benchmark, all transactions
rolled back. These are single-process application timings, not internet traffic,
real customer connectivity, carrier delivery capacity or PostgreSQL server-memory
measurements. Denser/uncertain topology fails closed at documented budgets.

## Decision and remaining gates

Suitable for isolated synthetic acceptance after final CI passes. No production
activation authorization or production readiness claim. Gates: approved public
wording/areas/baseline, real Powercode contract/mapping/consent review, production
provider idempotency and reconciliation, carrier/compliance/segmentation review,
public WordPress/HTTPS/CSP integration, private DB TLS and role review, edge caching
and purge procedure, independent secrets, backup/restore, retention and sustained
concurrent load testing. No live equipment, production Powercode, real messages,
customer account creation, merges or deployment occurred. RF remediation remains
simulation-only. Exact final commit, CI links/counts and any failures are recorded
in the draft PR after all GitHub Actions finish.

Final queue review added a real concurrent PostgreSQL delivery/cancellation regression.
Cancellation now uses the native queue lock before refreshing the plan; busy workers
return a retryable conflict instead of allowing accepted receipts to be overwritten.
The private SMS browser workflow passed again after clearing case-specific form drafts.
