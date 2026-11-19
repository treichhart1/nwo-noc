# NOC117B — Frontend dependency security

## Decision and provenance

Continue PR121 on `feature/noc-117-container-security`, starting from
`fa83bf80f18fbd8b5e456bf3e23d0f43831813ad`. Working tree was clean; no applicable
AGENTS.md found at root/workspace/repository or tracked subdirectories. Read README
and FOLLOW-UP. Verified PR121 remains draft/stacked on PR120. Run620 failed only
npm audit and its correctly propagated aggregate gate; those historical results
remain evidence, not execution of this new source.

**Select the unchanged Vite7 toolchain.** The specific advisory was withdrawn by
upstream, and actual full audits now return0vulnerabilities. This is a security
catalogue disposition, not a package code patch, local waiver or proof that all
regex/glob use is safe. No test/audit gate or dependency category is removed.
Operational release/live pilot remains **NO-GO** for the other material blockers.

## Original issue and new primary evidence

GHSA-fc5q-39xc-vjpf / CVE-2026-112366 reported repeated-extglob ReDoS in picomatch
<=4.0.7. Original npm audit had four HIGH affected dependency entries, one primary
advisory, on this deduplicated chain:

```
plugin-react5.2.0 -> vite7.3.8
vite7.3.8 -> picomatch4.0.7
vite7.3.8 -> fdir6.5.0 -> picomatch4.0.7
vite7.3.8 -> tinyglobby0.2.17 -> picomatch4.0.7 / fdir6.5.0
```

On2026-11-19, GitHub's canonical advisory-database JSON records withdrawal at
**2026-11-18T15:38:56Z**. Its human-readable reason is internally incongruous;
we do not infer non-exploitability from that prose. The structured withdrawal and
actual registry audit are the evidence for clearing this specific audit finding.
Full canonical JSON is retained in `frontend-advisory-withdrawn.json`, from
https://raw.githubusercontent.com/github/advisory-database/main/advisories/github-reviewed/2026/10/GHSA-fc5q-39xc-vjpf/GHSA-fc5q-39xc-vjpf.json .
Human-readable advisory: https://github.com/advisories/GHSA-fc5q-39xc-vjpf .
Maintainer discussion: https://github.com/micromatch/picomatch/pull/235 .

Picomatch remains4.0.7 with no published patched version established. Its original
code has not been declared fixed. Treat untrusted patterns as unsafe; do not allow
public callers to supply build-tool globs/regexes. No such functionality was added.
Keep auditing every development dependency so later advisories fail the same gate.
The historical four-HIGH JSON is preserved; no history is rewritten as clean.

## Supported solutions evaluated

1. Compatible Vite7 patch: registry still supplies7.3.8/picomatch4.0.7; no package
   patch found. Full audit on unchanged lock now0 due upstream withdrawal.
2. Controlled Vite8.3.8: detached worktree `/tmp/noc117b-vite8`, never installed into
   selected branch. Node22.23.4 satisfies its `^20.19.0 || >=22.12.0` engine;
   existing React plugin5.2.0 explicitly peers with Vite8, so no React/plugin major
   change needed. TypeScript5.7.3 typechecks; existing three configs/builds succeed.
   Audit0, but it **still depends on picomatch4.0.7/tinyglobby0.2.17/fdir6.5.0**.
   It does not independently remediate this issue. Rolldown/Oxc and newer default
   browser targets change build output/compatibility assumptions; no security
   benefit justifies adopting that extra migration. Candidate browser/container
   acceptance NOT EXECUTED; it is not a release candidate. See Vite migration
   guidance https://vite.dev/guide/migration .
3. Another build tool/fork/downgrade: unnecessary now the supported existing lock
   audits clean. No unreviewed fork, forced downgrade, overrides or ignore adopted.

## Exact selected versions before and after

Unchanged: Node22 deployment/CI (local test22.23.4), npm local11.9.0,
Vite7.3.8, React plugin5.2.0, TypeScript5.7.3, React/ReactDOM18.3.1,
picomatch4.0.7, tinyglobby0.2.17, fdir6.5.0, Playwright1.62.1.
The npm version actually supplied by setup-node in final CI is recorded by its
logs; local npm version is not falsely asserted as CI's version.
Lockfile SHA256 remains
`6176030e45518fd02ca72178ce2f0a8e275508d2f113f3cb2334d7fd74cf1f82`.
No application source/config, API, migration, manifest or lockfile changed.
The frontend runtime base is patched as described below. Git diff after clean npm ci verifies lock/manifest integrity.

## Executed local validation and full results

Under actual Node22.23.4: npm ci; full npm audit --json --audit-level=low including
dev dependencies; npm ls; npm explain picomatch; typecheck and NOC/status/signup
production builds all passed for selected Vite7.3.8. The same clean-install/audit/
typecheck/three-build experiment passed for unselected Vite8.3.8. Complete audits,
chains, explanations, registry integrity/engine/peer metadata and lock hash are
in `frontend-security-evidence.json`. Both audits:0info/low/moderate/high/critical.
There is no configured frontend unit-test script or standalone unit-test runner;
this is **NOT CONFIGURED**, not a claimed unit-test pass. Existing Playwright
regressions are the executable frontend behavioral suite.

Full final-source browser acceptance, frontend container build, native vulnerability
scan, migrations/backend/cross-module and actual Ubuntu installation/upgrade/restore
are rerun by existing CI. Exact final source SHA, each job conclusion, test counts,
image identities/full scan artifacts and archive digests are appended to PR121's
NOC117B final ledger. Earlier run620/other NOC117 results are not relabeled as new
execution. Local Docker VFS has<1GiB free; existing images/data are preserved and
not pruned, so container/Ubuntu checks use disposable CI hosts.

Browser coverage must include authenticated NOC/RF/tower/IPAM/inventory/maintenance/
documents/field/mobile/signup/public status/FiberDesigner workflows. No live provider,
radio, customer notification or production Powercode connection is authorized.

## Remaining risks and advancement

Clearing this withdrawn npm advisory does not clear backend51HIGH native findings,
consistent15s monitoring capacity, trusted production publishing/signing/key custody,
Redis/full license approval, minimumISO independent VM/bootstrap/reboot, offhost DR,
AV/VPN/MFA/egress deployment or real vendor/provider acceptance. Full final scans may
change counts: use actual final ledger/artifacts. Do not authorize operational
release/live pilot from a green CI run alone. NOC117B can close its dependency gate
only after final full audit/CI confirms these results; overall NOC117 release still
requires the remaining blocker procedures. All PRs remain unmerged.

## Newly reproduced native Nginx findings and patch

First NOC117B source37487f5/run622 reproduced a new native frontend scan result:
0CRITICAL/5HIGH/4MEDIUM, all in nginx1.30.6-r1 with fixed version1.30.7-r0.
Full raw nine findings, old/new OCI metadata and image history are preserved in
`frontend-container-remediation.json`; complete raw archive artifact14494036488,
SHA25685dde6cb4c13504d3e01224f67e286d0e0e6ae31c98fa0868dc094b22258b2bb.
HIGH CVEs:2026-113425,2026-121810,2026-123007,2026-123633,2026-123634.
MEDIUM:2026-118899,2026-120967,2026-123640,2026-125283. Nothing suppressed.
Official fixed-version evidence: https://nginx.org/en/security_advisories.html .

The immutable official stable-alpine index is updated from
901e593c95dc85f5a729f5765516321366ef4ac401821431d408897e95287d0a to
3bc3836dc0be3b811f238210360bf8e7ca8a2d0effdae46160bf1f3578710051.
Both old/new amd64 base histories use **Alpine3.24.4**; Nginx changes only
1.30.6→1.30.7, the supported stable patch, with unchanged NJS1.0.2.
No distro/major migration. Official OCI revisiona2a986d19aac4a79a330e6e0b5171aae4e521926;
ACME support package changes0.4.1→0.4.2 but is not enabled by NOC configs.
Read-only unprivileged UID101, port8080, Nginx configs/headers, package security
updates, healthcheck and all application output are preserved. Image lock and
Dockerfile and signup example are updated together; build records actual nginx -v.
Initial strict validation caught the example still using the old digest; that
inconsistent reference is corrected, with the validator unchanged. Final scan/browser/
container/host tests must confirm the patch; old scan is never relabeled final.
