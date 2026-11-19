# NOC117 dependency-security follow-up — 2026-11-18

Decision: **NO-GO** for operational release/live pilot. No compatible published
picomatch remediation was verified. The required npm security gate remains failing.

## Repository and inherited execution verified

Starting local/PR121 HEAD is `7117397f7c2ad0d2604e6798ff7c542add08b24a`;
tree `f292cea8f391a021226807c5735af108abe2d571`, clean worktree, branch
`feature/noc-117-container-security`, base PR120/NOC116. No AGENTS.md found at
root, workspace, repository or tracked subdirectories. Existing work preserved.

[Run616](https://github.com/treichhart1/nwo-noc/actions/runs/44323312809)
completed with seven successful jobs: backend, frontend, database-migrations,
rf-postgres-integration, compose-validation, Ubuntu24.04 approved host package,
Ubuntu24.04 isolated installation/recovery. dependency-security failed npm audit;
RF offline release acceptance correctly propagated that failure. This is verified
execution of the starting source, not execution of this later documentation/CI change.

## Exact dependency chain and investigation

`frontend/package.json` pins Vite7.3.8 and plugin-react5.2.0; installed/locked tree:

```text
@vitejs/plugin-react@5.2.0 -> vite@7.3.8 (deduplicated)
vite@7.3.8 -> picomatch@4.0.7
vite@7.3.8 -> fdir@6.5.0 -> picomatch@4.0.7 (deduplicated)
vite@7.3.8 -> tinyglobby@0.2.17 -> picomatch@4.0.7 (deduplicated)
vite@7.3.8 -> tinyglobby@0.2.17 -> fdir@6.5.0 (deduplicated)
```

One installed picomatch package is reached through several dependencies. The audit
reports four HIGH affected dependency entries (picomatch, fdir, tinyglobby, vite),
not four distinct primary vulnerabilities. [GHSA-fc5q-39xc-vjpf / CVE-2026-112366](https://github.com/advisories/GHSA-fc5q-39xc-vjpf)
describes repeated-extglob ReDoS; affected <=4.0.7, patched versions: none when
reviewed. The registry still publishes picomatch latest4.0.7, tinyglobby0.2.17
(picomatch^4.0.4/fdir^6.5.0), fdir6.5.0 (picomatch peer^3||^4). Vite latest is8.3.8, a major migration;
the latest observed compatible7.x is7.3.8.
Vite's supported Node range includes Node22>=22.12.0. Raw current metadata,
integrity fields, lock SHA256, npm explain and complete fresh audit are retained in
`picomatch-follow-up.json`. Local inspection used Node24.22.1/npm11.9.0; it is not
reported as Node22 acceptance. CI and container build continue using Node22.

No available compatible patched release was established. npm's proposed Vite6.2.7
is a major downgrade and does not establish a supported fix. No force/override,
advisory suppression, audit dependency omission, unreviewed git fork or manual
node_modules edit was adopted. Changing regex options in application code would
not patch Vite's internal dependency. Static production assets do not run Vite,
but build/dev risk remains in scope. Node22, source, frontend behavior, package
manifest, lockfile and all security assertions are unchanged.

Next action: verify a released compatible patched picomatch and upstream dependency
ranges, perform a targeted lockfile update, run npm ci/full audit with development
dependencies, all three builds and browser regressions. If upstream requires a
major build-tool migration, review that separately; do not force it for green CI.

## Changes in this follow-up

CI now retains npm ls, npm explain, exact Node version and lock SHA256 alongside
the full audit and source SPDX/pip evidence. A clean manifest/lock diff is required
after npm ci. Existing audit level=low and its original exit status remain intact.
No new registry lookups are required for these CI evidence steps. Security matrix
now records actual starting-source Ubuntu evidence and contrary performance runs.

## Starting-source security and compatibility evidence

Final Go1.27.4/xsys0.44.0 derivative was actually rebuilt/rescanned in616:
PostgreSQL16.16, gosu1.20, postgres UID/GID70, **0 findings**. Frontend0;
backend0CRITICAL/51HIGH/237total (no FixedVersion in those HIGH rows); unchanged
Redis7.4.12 has0HIGH/CRITICAL/1other. Neither official PostgreSQL16 baseline nor
investigated16/Alpine3.23 alternative fixed gosu: each1CRITICAL/25HIGH/62total.
Full raw baseline/candidate scans, licenses and runtime identities:
[artifact14434924186](https://github.com/treichhart1/nwo-noc/actions/runs/44323312809/artifacts/14434924186),
ZIP SHA256 `a53ea7bc98fd46920fccffbb5540aa0c89d144a85bcb50d9b64340f989977073`.
Source SPDX282records/pip-audit0/npm4HIGH:
[artifact14435931990](https://github.com/treichhart1/nwo-noc/actions/runs/44323312809/artifacts/14435931990),
ZIP SHA256 `5f1877d86e5cc5072c1307f13b7054c2a4d637f3a5826c28153ae97b1eaa0664`.

616backend855passed/955DBskips; PostgreSQL1103passed plus overlapping43/64/54/36
suites;69browser cases;105host assertions and11historical upgrade/restore checks.
Actual Ubuntu24.04 hosted systemd install57.279s, private TLS, disabled integrations,
DB/Redis/worker restart recovery, encrypted backup/isolated restore/data preservation,
safe upgrade/failure gates passed. Not minimumISO VM or independent offhost DR.

Same-source1000device/sixround synthetic2ms/8thread benchmark:616median9.912s
(9.457–10.410s),615median16.366s(15.194–17.229s). Both preserve6000unique
samples/receipts; health queries33.55ms/46.11ms. **Consistent15s acceptance FAILS**.
Do not select only the faster runner or infer real-device performance.

## Final follow-up execution

The final pushed source must rerun all applicable CI, including a fresh derivative
build/rescan and actual Ubuntu install/upgrade/recovery using that derivative.
Exact source/job/artifact results are recorded in PR121's final follow-up ledger.
Starting-source counts above must not be relabeled as this follow-up execution.
Local disk remains <1GiB free; existing VFS images/data are retained and not pruned.
Full container/Ubuntu validation therefore executes on disposable CI runners.

Remaining blockers: npm4HIGH, backend51HIGH, inconsistent15s performance,
independent trusted production signing/publication/key custody, license/legal
acceptance (Redis RSALv2 OR SSPLv1 and unknown licenses), minimumISO bootstrap,
offhost disaster recovery, actual VPN/MFA/egress/AV deployment and separately
authorized device/provider/Powercode compatibility. No live authorization implied.

No migrations removed/changed, production/device/Powercode access, messages, public
incidents, deployment, PR merge, other repository changes or network writes.
RF self-healing remains simulation-only.
