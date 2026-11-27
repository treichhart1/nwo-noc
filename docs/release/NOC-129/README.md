# NOC-129 — Customer acquisition and installation

Base: draft PR #131, `0483a4613dccf38227b09551e25c9315d8af68e9`, tree `ae24335495910ae782ee88155456d2d77f5269f1`. Branch: `feature/noc-129-customer-signup`, stacked without merging or reapplying previous milestones. The final PR ledger records the exact delivered source and CI conclusions.

## Prerequisite acceptance

NOC-128A runs #816 / 46050235585 and #817 / 46050241731 passed all eleven jobs independently. Their 36 measured durable rounds preserved 1,000 devices / 40 POPs, all samples, unique receipts and PostgreSQL/filesystem durability. Run816: min11.737s, median12.955s, max13.892s; run817: min11.274s, median12.4595s, max13.407s. Historical run815 failure (max20.780s) remains in NOC-128 evidence. No benchmark, resource limit or threshold changes are included here.

## Existing functionality and actual gaps

NOC103 already supplies encrypted applications, configured plans/areas, preliminary wireless/fiber qualification, the private operator dashboard, surveys, scanned photographs, immutable quote revisions, canonical installation work orders, matching and documentation-only provisioning. These are reused. NOC129 adds an installable iframe WordPress wrapper, a signup-only image, public restricted quote decisions, explicit prospect recipients on the canonical notification outbox, installation inventory assignment, acquisition analytics and lifecycle Slack routing. LTE/5G are explicit manual qualification paths; no carrier coverage is invented.

Migration0032 follows0031 and preserves every historical migration. It adds an exclusive customer-or-application recipient reference, shared phone-hash suppression and append-only quote responses. Public HTTP database credentials receive execute-only quote functions, never SELECT on private quotes, users or topology. Customer tokens are random, hashed for lookup, encrypted at rest, expire with the current quote and never appear in URLs. A token proves possession, not government identity or a signed legal contract. Acceptance does not bill, provision or activate service.

## Verification

The required CI retains all eleven original jobs and adds WordPress signup and acquisition acceptance. It exercises actual disposable WordPress/PHP, PostgreSQL migrations, the restricted public ASGI service and TLS browser submission/quote response. The existing private application browser workflow, provider, Slack, installation/recovery and strict durable benchmark gates remain required. Local Python skips are not database passes. Final counts, ZIP checksum, image scans and every job conclusion belong in the final PR ledger; do not infer completion from this document.

## Release decision

NO-GO for production or live-provider activation pending final automated evidence and separately authorized website/provider/equipment/server acceptance. No live integrations were enabled. Vendor-native security disposition, Redis/dependency licensing, trusted production signing/publication, actual VPN/MFA, current malware definitions, off-site recovery and Wave compatibility blockers remain. See [deployment and workflows](OPERATIONS.md), [security and communications](SECURITY.md), and [email gap](EMAIL-PLAN.md).

## Initial acceptance corrections

Initial runs818/819 retained: strict source scanner rejected newly introduced unreviewed Compose image variables; restored the reviewed pinned example and documented a separately approved immutable deployment override. The signup fixture, mounted outside /app, lacked PYTHONPATH and exited before migration; reproduced ModuleNotFoundError locally, added /app explicitly and require actual restricted ASGI readiness before CMS startup. No security gate or assertion was weakened. Local full backend after corrections:1333 passed,1050 skipped (PostgreSQL unavailable locally); final CI remains authoritative.

Real PostgreSQL run819 exposed the legacy incident-only linked-approval assumption and service-only customer-premises guard. Added explicit current-consent prospect approval linking and an exclusive accepted-application premises reference with serving-site validation, immutable context and application-scoped installation checks. Existing incident/publication approvals and customer-service premises guards remain enforced. The disposable concurrency test now substitutes role identifiers only, preserving SQL function names; browser fixtures use reserved example.com addresses accepted by unchanged email validation.

Run821 reached actual WordPress installation and plugin activation, then its Node-side request context could not resolve the isolated browser hostname. The negative nonce assertion now executes through the authenticated browser fetch (same TLS/DNS controls), still requiring403. The failed request trace printed a disposable WordPress session cookie; that isolated CMS is destroyed at harness cleanup and has no production authority. Historical failed logs are retained; no production credential was used.
