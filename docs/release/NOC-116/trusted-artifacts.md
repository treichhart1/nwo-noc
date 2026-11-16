# Trusted artifact procedure — A111-16 remains BLOCKED operationally

NOC116 adds **mandatory offline Ed25519 verification for purpose=release** approvals.
The explicit controlled-test path remains synthetic and cannot become a release.
Root-owned approval JSON already binds exact commit/tree, migration head, supported
upgrade sources and backend/frontend immutable registry digests. Mutable tags/local
image IDs remain rejected for releases. Signature verification precedes artifact
installation; it does not waive clean checkout, ancestry, OCI revision, UID, source
inventory, migration or image checks. Openssl is a supported Ubuntu dependency; an
unavailable verifier fails closed, never falls back to unsigned approval.

No production key, signed release, registry publication, attestation or operational
trust infrastructure has been created. Tests use ephemeral test-only Ed25519 keys.
These demonstrate cryptographic verification, not publisher identity or firmware trust.

## Independent release authority procedure (NOT EXECUTED for production)

1. Review exact final commit/tree, clean full dependency chain, immutable build
   digests, OCI revision labels and platform, generated SBOM, vulnerability/license
   reports, test evidence and supported upgrade list. No development tag is approval.
2. Build in a trusted isolated builder. Retain build logs, pinned build tools/images,
   source SHA/tree, architecture, dependencies, SBOM SHA256 and image digests. Publish
   only through separately authorized registry credentials. Signing and publishing
   are not authorized in this milestone. Reproducible dependency locks do not prove
   bit-identical Docker images, SLSA provenance or absence of vulnerabilities.
3. Use a protected Ed25519 signing key held by the release authority, never the NOC
   host or CI repository. Provision the verified public-key fingerprint out of band
   over an independently trusted administrative channel. Do not trust a public key
   downloaded beside the image/manifest itself. Define key rotation/revocation and
   downgrade/expired-release policy externally before approval.
4. Sign the exact reviewed manifest bytes on the signing host:

```sh
openssl pkeyutl -sign -inkey /protected/release-signing.key -rawin \
  -in reviewed-release.json -out reviewed-release.json.sig
openssl pkey -in /protected/release-signing.key -pubout -out release.pub
openssl pkeyutl -verify -pubin -inkey release.pub -rawin \
  -in reviewed-release.json -sigfile reviewed-release.json.sig
```

5. After verifying the fingerprint independently, install public verification
   material on the isolated acceptance server (never a private signing key):

```sh
sudo install -m 0600 release.pub /etc/nwo-noc-release.pub
sudo install -m 0600 reviewed-release.json /etc/nwo-noc-release.json
sudo install -m 0600 reviewed-release.json.sig /etc/nwo-noc-release.json.sig
sudo ./install.sh --backup-recipient <approved-public-age-recipient> \
  --tls-cert /root/noc-cert.pem --tls-key /root/noc-key.pem --tls-ca /root/noc-ca.pem
sudo ./verify.sh
```

Paths must be root-owned regular files in protected parents; symlinks/public
permissions fail. The signature file follows any custom --approval path as .sig;
the independently trusted public key is always /etc/nwo-noc-release.pub. Keep the
original signed manifest/signature and public trust fingerprint off-host with
recovery documentation. Restore does not automatically trust a key in an archive.

The signature proves authorization of the approved manifest bytes; it is not an
OCI image signature. OCI digests plus checked source revision bind approved images;
registry/build provenance, image signatures/attestations, key custody/revocation and
published production artifacts still require independent operator acceptance.

## Supply-chain evidence

- Existing hashes-only Python installation, npm integrity lock, pinned action and
  runtime base images, pip-audit/npm audit and reviewed Bandit/Ruff gates remain.
- scripts/release/sbom.py emits deterministic SPDX2.3 source-lock inventory, including
  runtime/test/audit/npm closures. NOASSERTION is a required review, not an approved
  license. Historical license metadata is used only for exactly matching versions.
- Compose CI uses digest-pinned Trivy0.69.3 to scan the *built* images and database/
  Redis images. It resolves immutable local IDs, checks returned scan identity, and
  fails on scanner errors/empty malformed evidence. Native image CVE/license findings
  are retained in Actions artifacts and summarized as PASS/FAIL by severity. This is
  an explicitly **informational evidence job**, not a waiver of HIGH/CRITICAL results.
  Existing dependency gates remain enforced. A green job only means scanning executed.
- Scan tooling has network access to approved public package/vulnerability registries
  during CI, not production equipment. Applications remain offline. Scanner database
  freshness and image-specific findings must be reviewed for each published release.
- Redis Python client licensing differs from Redis server licensing. Record exact
  server version/image digest and selected permitted license/commercial terms with
  legal/operator approval. Do not silently substitute Valkey/another major release.

Unreviewed HIGH/CRITICAL image findings, unknown licenses, Redis legal decision,
trusted publisher/registry/signing, native-library coverage and deployed scanning
remain release gates. No dependency was blindly upgraded to force green CI.
