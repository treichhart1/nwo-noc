# WordPress signup deployment and operator workflow

## Build and install the plugin

Use the reviewed PR commit, never an arbitrary branch. From its clean checkout:

```sh
python3 scripts/signup/build.py --output /tmp/nwo-signup-package
cd /tmp/nwo-signup-package
sha256sum -c nwo-customer-signup.zip.sha256
```

The reproducible ZIP contains seven allowlisted members, fixed timestamps and permissions, no dependencies or credentials. Its member hashes and ZIP hash are in plugin-manifest.json and the dedicated CI artifact `wordpress-signup-plugin-and-acceptance`. Do not commit generated binaries.

In an independently approved WordPress site (6.5+, PHP8.1+ with cURL), upload nwo-customer-signup.zip through Plugins → Add New → Upload, activate per-site, then Settings → NWO customer signup. Keep disabled until gateway acceptance. Approve only the exact HTTPS public origin `https://signup.nwohiobb.com`; set branding and frame height, explicitly approve origin/DNS and run Check connection. Add `[nwo_customer_signup]` or the NWO Customer Signup block. No page or application is automatically published. A direct secure-form link supports browsers that block embedding.

Plugin upgrade: deactivate, retain settings, replace with a separately reviewed ZIP, reactivate, check TLS diagnostics and synthetic submission. Deactivation preserves configuration. Uninstall removes only plugin options; pages and NOC applications remain intact. WordPress stores configuration only, not applicant PII, quote tokens, Turnstile secrets or provider credentials.

## Dedicated gateway and DNS/HTTPS

Build reviewed images with `docker build -t reviewed-signup-web -f deploy/Dockerfile.signup .` and the existing backend Dockerfile. Audit dependencies including development dependencies, scan both exact images, generate SBOM/provenance and publish/sign immutable digests through the established controlled release procedure. A local tag is a build label, not a production-approved artifact.

Use examples/signup/compose.yml only after separate approval. The example retains reviewed pinned base images and a local build/static mount for isolated development. For deployment, an operator-reviewed Compose override must replace both services with approved registry @sha256 digests, remove the backend build and local frontend/config mounts, and use the tested signup-only web image. Do not deploy the development example without that approved override. Supply a separate SIGNUP_ENV_FILE, gateway-only SECRET_KEY/unused ADMIN_PASSWORD, restricted SIGNUP_DATABASE_URL and a read-only key directory. No private backend environment or database-owner password belongs in the public deployment. Run `docker compose -f examples/signup/compose.yml -f /etc/nwo-signup/approved-images.yml --profile signup config --quiet` without printing expanded secrets; only then `docker compose -f examples/signup/compose.yml -f /etc/nwo-signup/approved-images.yml --profile signup up -d`. These commands change the separately authorized gateway host, not the live website.

The web service binds127.0.0.1:18091; gateway has no published port. A separately approved TLS1.2/1.3 edge for signup.nwohiobb.com proxies only to that listener. Reuse the bounded, public-only examples/signup/nginx.conf location allowlist; never proxy private NOC routes. Provision DNS and trusted TLS certificates through the server operator. Do not use scripts/signup/proxy.conf in production: its second server is an isolated CI WordPress TLS wrapper. Preserve no-store, no-referrer, CSP exact WordPress frame ancestors,32KiB body limits and bounded proxy timeouts. Apply edge request limits in addition to the PostgreSQL-backed application budgets.

Run every migration privately first. As a DBA, review examples/signup/public-role.sql and grant its group to a dedicated login with a secure password; never grant noc_app/owner to that login. Supply the existing signup PII key to public/private applications via protected files, with independent encrypted recovery. The public process needs this key to encrypt submissions; isolate it from private networks. Permit only explicit PostgreSQL connectivity and approved Turnstile egress; deny device, metadata and unrelated private networks. Do not expose an unrestricted Docker bridge.

Set SIGNUP_PUBLIC_ENABLED=true only after authorization; SIGNUP_PUBLIC_TEST_MODE=false and SIGNUP_TURNSTILE_MODE to the supported live mode documented in NOC103. Register Cloudflare Turnstile for signup.nwohiobb.com, protect the secret file, configure the site key and exact SIGNUP_PUBLIC_ORIGIN. Fixture mode is only for isolated staging with synthetic monitoring. Use same-site HTTPS WordPress/signup origins so Secure SameSite=Strict CSRF cookies work inside the iframe. Do not relax cookie, origin or TLS checks for unrelated domains.

## Ordered operator workflow

1. In private Applications configure reviewed public plans: audience, technology, currency, recurring/install amounts, eligibility and optional Powercode mapping. Missing prices remain unknown. Publicly display only plans explicitly marked active/public.
2. Configure documented active/planned coverage polygons and existing tower/sector/fiber references. Wireless proximity is preliminary; terrain and connection rights require evidence. LTE/5G always require manual review until a verified carrier contract is implemented.
3. Submit synthetic requests first: separate first/last name, business name, address/unit, preferred contact/availability, privacy acknowledgement, optional unchecked SMS consent and Turnstile. Confirm retry-safe receipt and no private data in the WordPress database.
4. Review duplicate/customer matches, assign an operator, advance the canonical state and record reasons. Use qualified review rather than guaranteeing availability.
5. Create one canonical survey work order, assign a technician and schedule. The assigned field workflow records observations, equipment, mounting, cabling/fiber requirements and scanned photographs. Unassigned technicians cannot read applicant details.
6. Create immutable quote revisions with monthly/install/equipment/construction charges, one-time discount and expiration. Administrator approves and issues the current revision. Prepare private access, then retrieve the token through the audited administrator endpoint. Share it only by an independently approved private channel; never in URLs or Slack. The customer enters it on the same signup page, passes CAPTCHA and explicitly accepts or rejects. Do not treat it as automatic billing or a legal e-signature platform.
7. After acceptance, advance to installation_pending, create the existing unique installation work order and appointment. Reschedule through the canonical field work order; prepare a fresh notification draft after context changes. Complete required field checklists and notes. For a serialized spare use the documented installation-asset action with current application/asset revisions and an existing customer-premises location; inventory history and work references commit together. Additional parts use existing field consumption records.
8. Record installed/activation_pending/completed only with administrator evidence and documentation-only acknowledgement. No live device or Powercode writes occur. An approved provisioning preview includes protected applicant/plan/matching/work information for future NOC130 review, not an executable provisioning request.
9. Use Acquisition analytics for a bounded90-day default view; rates reflect current lifecycle states in the submission cohort. Revenue is configured prospective monthly revenue, not billed/collected cash. Unknown pricing is not invented.

## Notifications, backup and rollback

See SECURITY.md for prospect SMS and separate Slack approvals. Email remains draft-only; see EMAIL-PLAN.md. No delivery is authorized by saving an application, public quote acceptance or WordPress activation.

Reuse BACKUP_RESTORE.md and NOC120/124 operator procedures for encrypted PostgreSQL, config, documents, signup PII and provider identity/vault key backups. Confirm actual scanner availability and definition updates before accepting photos. Test restore into an isolated host and verify quote-response, inventory and application history. Migration0032 has no automatic destructive downgrade; if recovery is needed, stop writers and follow independently approved backup recovery. Never restore over newer data silently. Disable public signup/plugin first to stop new submissions; preserve records and failed evidence.

Troubleshooting: unavailable form→check explicit enablement/plan catalog; CSRF failures→same-site HTTPS origin/cookies; CAPTCHA rejection→registered hostname/mode/secret; quote unavailable→current approved revision, expiry, state and token;403→role or assigned technician;409→refresh revisions and review history before exact retry. No public API exposes token validity through IDs or applicant searches.
