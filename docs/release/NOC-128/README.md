# NOC-128 — WordPress public network status

Base draft PR130, exact commit `3c49888f7a4e023f1ea544db28cd7978f4449f06`, tree `84b37f5e025587d7485defa82162a0b0f7249734`. The NOC127 branch remains authoritative for the existing native Slack work, with NOC126 SMS and the earlier stacked chain preserved. No merges, production access or live website changes are authorized.

## Implementation and boundaries

- Standalone installable plugin `wordpress/nwo-network-status`, shortcode, dynamic Gutenberg block, full/banner views, responsive namespaced presentation, administrator settings/diagnostics, explicit endpoint approval, TLS/IP pinning, bounded JSON validation, safe unknown/stale behavior and scoped uninstall.
- Reproducible ZIP/checksum/member manifest via `scripts/wordpress/build.py`; CI artifact `wordpress-status-plugin-and-acceptance`. No new npm/composer dependencies.
- Existing restricted public gateway gains additive `/public/network-status/v2`, approved public area metadata, UUID-based service-area relationships with unknown legacy ambiguity, explicit baseline freshness/expiry, bounded projection and global withdrawal-aware revision token. Both API versions read policy, announcements and areas in one PostgreSQL statement/MVCC snapshot, preventing split-read withdrawal races without increasing privileges. Withdrawals retain their approved public update timestamp and require independent baseline review before healthy status can return. The v1 schema remains compatible and rejects unexpected fields; stale/future approvals fail closed. Its legacy browser widget now labels stale approvals unknown and expires retrieved snapshots.
- Additive migration `0031_wordpress_status` after0030; no historical migration edits. Reuses canonical cases, incidents, maintenance, immutable approval revisions and restricted security-barrier views. No duplicate customer, incident or queue tables.
- Existing SMS workflows remain unchanged. Related Slack draft preparation reuses canonical notification plans and deduplication, with separate provider/source authorization. Public approval never authorizes external delivery.
- Restricted TLS proxy and immutable gateway preflight examples; no private application routers or provider credentials on the public boundary.

Deployment instructions: [WORDPRESS.md](WORDPRESS.md). All ten existing release gates, strict durable monitoring workload and independent Ubuntu/recovery harness remain required; an additional WordPress acceptance job is required by the aggregate gate.

## Evidence policy

Local environment: Debian13 userspace, full root filesystem, writable RAM checkout, no Docker daemon or PHP CLI. This prevents local WordPress, PostgreSQL, container and Ubuntu execution. Local projection/package tests, communication unit regressions, frontend typecheck/build and static checks are executable; skipped database tests are not passes. Dedicated CI uses actual minimum, supported6.x and current WordPress PHP8.3 images and MariaDB, actual admin ZIP installation, real TLS/DNS pinning against an isolated synthetic gateway, browser rendering and negative cases. The required RF PostgreSQL job validates actual approved snapshots, v1 compatibility and communication relationships. Synthetic gateway data is not a live website acceptance result.

Supplemental local PHP8.3.36 (official WordPress Playground WASM) compiled the plugin with inert registration hooks and passed43 explicit pure validation assertions; this reproduced and corrected a missing parenthesis in the CIDR expression that prevented activation. This is not native WordPress installation evidence. [Local evidence](LOCAL-VALIDATION.json) records scope and tooling limits; [plugin manifest](PLUGIN-MANIFEST.json) records the reproducible ZIP and member hashes.

Final exact commit, all job conclusions, artifact checksum and executed counts belong in the PR acceptance ledger after CI finishes. No base-CI result substitutes for this change's CI. Preserve failed attempts and any remaining unexecuted checks.

## Remaining acceptance

Production/website deployment remains NO-GO until independent operator approval, approved public hostname/DNS/TLS/firewall/egress, WordPress current security baseline, exact gateway/proxy image scans and signatures, restricted DB role validation, CDN cache exclusion/purge behavior, public-area privacy review, accessibility/theme acceptance and publication rehearsal. Existing vendor-native, licensing, production signing, VPN/MFA, malware definitions, physical off-site recovery, equipment and provider acceptance blockers remain unchanged. No real incident, SMS or Slack delivery was performed.

## Durable performance follow-up

[NOC-128A investigation and correction](PERFORMANCE-FOLLOW-UP.md) preserves the original failed benchmark and all earlier comparison rounds, documents the observed durable-I/O stalls and removes one redundant inventory query per poll. Final independent acceptance results remain required in PR131; historical green checks do not waive any slow round.
