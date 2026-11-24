# Operator-controlled trusted release publication

**Preparation only. No production signing key, registry publication or release approval has been created.**
The production-purpose installer fails closed while material blockers remain. Disposable CI signatures are test evidence, not production trust.

## Required immutable inputs

Choose an independently reviewed exact commit/tree and complete inherited migration chain. Obtain a clean full checkout. Record repository, platform linux/amd64, x86-64-v2 requirement, builder identity, invocation URL, pinned build materials and supported upgrade_from commits. Never use a branch/tag as source authority.

Build backend, frontend and hardened PostgreSQL from that source using existing Dockerfiles; preserve Node22/Python3.12/PostgreSQL16/Redis7. Obtain registry publication permission separately. Only after authorization, publish reviewed images and resolve their **actual registry RepoDigests**, then pull and rescan those exact digests. Include Redis’s exact approved repository/digest; do not replace a config ID with a fabricated registry name.

Required four-role set: backend, frontend, postgres, redis. Workers reuse the backend image; shared vulnerabilities are not double counted. Retain image platform, UID, OCI source/revision labels and full native inventory. CI local-image IDs and their unsigned provenance cannot be silently relabeled as published registry evidence.

## Evidence collection commands

Use the tools at the chosen source, with only the reviewed builder’s approved package/registry egress. Run existing full dependency audits (including development dependencies), tests and all ten CI jobs. Record tool/database versions and timestamps. The following tools **collect evidence**, not release approval:

```sh
python3 scripts/release/sbom.py > /protected/review/source.spdx.json
pip-audit -r backend/requirements-test.lock -r backend/requirements-audit.lock \
  --vulnerability-service pypi --format json --output /protected/review/pip-audit.json
(cd frontend && npm ci && npm audit --json --audit-level=low > /protected/review/npm-audit.json)
python3 scripts/release/image_security.py --require-no-high --output /protected/review/scans \
  APPROVED_BACKEND_REPODIGEST APPROVED_FRONTEND_REPODIGEST \
  APPROVED_POSTGRES_REPODIGEST APPROVED_REDIS_REPODIGEST
python3 scripts/release/image_sbom.py /protected/review/scans
python3 scripts/release/build_provenance.py /protected/review/scans
```

Use the locked audit runtime from CI, not unpinned substitute audit tooling. Create protected review paths beforehand; scanner output must be new. Do not treat the informational Compose baseline scan job as a waiver. Preserve full raw findings, including MEDIUM/LOW and historical HIGH risks. Current primary vendor status, supported package fixed versions and runtime exposure require independent review even with0HIGH/CRITICAL.

The generated build-provenance.json deliberately remains **unsigned CI provenance with open blockers**. A release authority must independently verify source/materials/registry subjects, build identity and actual execution. Do not automatically convert local IDs to registry digests or replace open_blockers with [] merely to make a verifier pass.

## Reproducible unsigned evidence assembly

Use a private directory outside the checkout with these0600 JSON files, owned by the preparing operator:

* manifest.json: deploy/host/approved-release.example.json with all placeholders replaced using reviewed facts; format1, repository treichhart1/nwo-noc, purpose release, actual commit/tree, migration_head, approval_reference, explicit upgrade_from and all four immutable registry images.
* sbom.payload.json: {"source": full source SPDX2.3 document, "images": {"backend": full SPDX2.3, "frontend": full SPDX2.3, "postgres": full SPDX2.3, "redis": full SPDX2.3}}.
* dependencies.payload.json: {"pip": full pip-audit report, "npm": full npm audit-v2 report}. Keep complete development closure and vulnerability rows.
* containers.payload.json: map the four roles to their **full Trivy SchemaVersion2 reports**, with actual matching RepoDigests and ImageID/package inventory, not summaries.
* provenance.payload.json: verified source_commit/source_tree, subjects equal the four approved registry references, actual builder/invocation_id, immutable materials, and independent risk_review. Retain all unresolved blockers. Only the independent authority can approve a genuinely empty blocker list after closure/disposition.

These descriptions are schemas, not literal substitute payloads. No production-complete payload bundle exists today. A small offline assembler reuses the installer’s exact evidence verifier:

```sh
python3 scripts/release/prepare_publication.py \
  --source /path/to/clean-approved-checkout \
  --inputs /protected/review/inputs \
  --output /protected/review/new-unsigned-bundle
```

The parent must be private/owned; inputs are protected regular files, bounded64MiB. Output must not exist. The tool verifies clean exact source/tree, upgrade ancestry, complete source/image SBOMs, development-inclusive dependency reports, actual registry identity,0HIGH/CRITICAL and no unresolved risk blockers. It writes four hashed source/image-bound envelopes plus **unsigned-release.json**, all0600 under0700. It does not sign, publish, change open blockers, contact a registry or generate a key. Refusal with today’s unresolved vendor/operator blockers is expected. Failed preparation does not authorize installation.

## Separate authority signing and trust distribution

After the authority has independently approved the complete bundle and only with separately authorized protected key custody, sign the **unchanged exact bytes** on the signing host:

```sh
openssl pkeyutl -sign -inkey /protected/authority/release-signing.key -rawin \
  -in unsigned-release.json -out unsigned-release.json.sig
openssl pkeyutl -verify -pubin -inkey /protected/authority/release.pub -rawin \
  -in unsigned-release.json -sigfile unsigned-release.json.sig
openssl pkey -pubin -in /protected/authority/release.pub -outform DER | sha256sum
```

No production key-generation command is part of this handoff. Tests use disposable Ed25519 keys. The signature authenticates manifest bytes; it is not an OCI image signature or a claim of SLSA certification.

Transfer the public key through a separately authenticated channel; compare its SPKI fingerprint with the independent authority’s record. A key downloaded beside a bundle or restored from that same backup is not independently trusted. Record key rotation, revocation, withdrawal, downgrade and expiry procedures with the authority; the current verifier does not implement a remote revocation service or manifest expiration. Distribute only public verification material and approved evidence to the server, never the signing key.

Install public key as /etc/nwo-noc-release.pub and the exact signed manifest/signature as /etc/nwo-noc-release.json and .sig, root-owned0600 with protected parents. Copy all envelopes beside the manifest without altering signed bytes; filenames/hashes must match. Perform offline signature verification and existing installer approval verification before state changes. See [installation guide](INSTALLATION-GUIDE.md).

## Negative acceptance and retention

With disposable keys only, run existing signature/evidence regressions and new test_release_handoff.py: changed manifest/signature/key, missing evidence, mismatched source/digest, omitted development audit, HIGH/CRITICAL findings, incomplete SBOM and open risk blockers must fail. Do not bypass those assertions for a production bundle.

Retain exact manifests, signatures, independently recorded public fingerprints, complete reports, SBOM, build logs/provenance, source/tree, registry digests and approval record off-host. Production stage captures release-trust/ for encrypted backups, but restoration still compares against independent trust. Approve recovery/key rotation before replacing the trusted key. No public CI artifact may contain private keys, credentials or decrypted customer backups.

## NOC120 Redis artifact requirement

New-source installation/upgrade requires the supported-library-patched Redis7.4.12 derivative from deploy/Dockerfile.redis, not the unchanged upstream base. The upstream source digest remains pinned as build material; the approved runtime digest is a separately reviewed immutable derivative. Require matching NOC source revision, original base identity, Redis7.4.12 binary, UID999/GID1000 and the fail-closed runtime-library probe. Four-role approvals and exact-image scans must be regenerated/reviewed and signed independently; do not relabel an old signature or use a mutable tag. Actual queue/AOF backup/restore and second-host recovery must pass on the derivative. Redis licensing remains an independent gate.

## Exact-source evidence context

Code snapshot C9 (`c9b8f42daee75eff33a56cfcee21319bd2214aad`) was validated by push739 and PR740; see ACCEPTANCE-EVIDENCE.json. The PR job actually builds GitHub's synthetic merge revision, even when the workflow API reports the branch head. Equal trees establish code equivalence, not equal source-revision labels or registry digests. Select canonical **push** artifacts whose OCI source revision exactly matches the approved immutable source. Rebuild and revalidate all evidence for the selected final release commit; never relabel an earlier image. The final documentation descendant's CI and exact SHA are recorded in draft PR124. Publication remains operator-controlled and unauthorized until every required blocker/approval is resolved.
