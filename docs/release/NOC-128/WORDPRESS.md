# WordPress public network status operator guide

This package does not change nwohiobb.com. Website, DNS, public ingress and publication require separate authorization. Powercode remains the customer portal. Never install a private NOC frontend, private backend router or notification credentials on WordPress.

## Build and install

From the reviewed NOC-128 source checkout:

```sh
python3 scripts/wordpress/build.py --output /tmp/nwo-status-plugin
cd /tmp/nwo-status-plugin
sha256sum -c nwo-network-status.zip.sha256
unzip -l nwo-network-status.zip
```

The deterministic ZIP has nine allowlisted members, fixed timestamps and permissions, no dependencies or credentials. CI retains the ZIP, checksum and per-member manifest in `wordpress-status-plugin-and-acceptance`. Compare its checksum against the reviewed source build before uploading. No production release signature is implied by a plugin checksum; retain the existing release approval/signing process.

Requirements: WordPress6.5 or later, PHP8.1 or later, cURL with TLS and `CURLOPT_RESOLVE`, mbstring, trusted current CA bundle. Compatibility results must come from the dedicated CI job, not these minimum declarations. Multisite network-wide activation is rejected; per-site activation only. Current and minimum WordPress images are test fixtures; do not deploy an old vulnerable WordPress version simply because the plugin supports its API.

In WordPress administration, Plugins → Add New → Upload Plugin, choose the ZIP, install and activate. Settings → NWO Network Status requires `manage_options`. Review an HTTPS gateway such as `https://status.example.invalid/network-status-api/v2`, enable retrieval and explicitly approve its public DNS addresses. The example hostname is intentionally not deployable. Save requires a nonce and administrator capability. No NOC credentials are needed or accepted.

Create `/network-status/` using `[nwo_network_status]` or insert the **NWO Network Status** Gutenberg block. Choose full view or banner. A homepage banner can use `[nwo_network_status view="banner"]`, the block banner view, or optional `body_open`/footer placement. Placement defaults to none. Theme-independent namespaced CSS supports light/dark/system, configurable accent colors, text labels and public support links. Meaning always includes text and icons; accents are not the only indicator. Unknown cannot be relabeled healthy.

## Connection, privacy and cache

Only exact `/network-status-api/v2` and `/public/network-status/v2` paths over HTTPS port443 are accepted. URLs cannot contain credentials, queries, fragments, encoded paths or IP literals. All resolved addresses must be public; private, loopback, link-local, metadata, documentation, multicast and special ranges are rejected. Saving pins approved public addresses. Retrieval uses those addresses without DNS, verifies certificate hostname/chain and actual peer address, rejects redirects and proxy environment inheritance, honors WordPress external-request policy, and bounds connect/total time to2/5seconds and bodies to512KiB. DNS changes require explicit administrator reapproval; do not disable TLS or permit private destinations to fix connectivity.

The server validates the complete allowlisted v2 response before caching. It accepts no private identifiers, extra fields, contacts, coordinates, IPs, HTML or URLs in public prose. No customer data or management credentials are stored. Public service-area names must be independently reviewed generic community/county/regional names.

WordPress caches snapshots for15seconds and failed attempts for15seconds, with an atomic short-lived fetch lock. Browser refresh is configurable15–60seconds. Every snapshot expires independently after90seconds, even when JavaScript polling fails. Gateway freshness expires approved public information after24hours without a public update. This is administrator-approved communication, not a guarantee that every service is operating.

A failed/invalid/redirected request shows **Status unknown** immediately; it does not reuse old healthy status or old incident descriptions. A stale approved feed shows unknown with a stale warning. Initial HTML always renders unknown so full-page caches cannot freeze healthy status. Exclude `/wp-json/nwo-status/v1/feed` from all WordPress/CDN caching; it returns `no-store`. Manual refresh requires administrator capability and nonce. A changed global revision token invalidates history-page caches; withdrawals replace snapshots rather than merge them. Ordinary visibility can lag15seconds server cache plus up to60seconds browser refresh. For urgent withdrawal: publish the approved withdrawal, manually refresh plugin cache and purge any noncompliant upstream/CDN caches. No push invalidation is claimed.

## Separately deployed public gateway

Apply the complete NOC migration history through `0031_wordpress_status` in the private application, then run the existing restricted-role provisioning procedure in `docs/customer-status/NOC-109.md`. The public gateway receives only `noc_status_public` credentials, independent rate key and PostgreSQL CA. It must never receive the private application database role, JWT key, provider vault or internal frontend bundle.

```sh
# Operator supplies these absolute paths and the reviewed immutable image digest.
export NOC_STATUS_BACKEND_IMAGE='registry.example.invalid/noc/backend@sha256:<reviewed-64-hex-digest>'
export NOC_STATUS_PROXY_IMAGE='registry.example.invalid/noc/status-proxy@sha256:<reviewed-64-hex-digest>'
export NOC_STATUS_DB_URL_FILE=/secure/operator/status-db-url
export NOC_STATUS_RATE_KEY_FILE=/secure/operator/status-rate-key
export NOC_STATUS_PG_CA_FILE=/secure/operator/postgresql-ca.pem
export NOC_STATUS_TLS_CERT_FILE=/secure/operator/status-chain.pem
export NOC_STATUS_TLS_KEY_FILE=/secure/operator/status-key.pem
python3 scripts/wordpress/gateway_preflight.py
# Review resolved configuration locally; never publish secrets or its output.
docker compose -f deploy/compose.customer-status.yml -f deploy/compose.customer-status.proxy.yml config --quiet
# Only after separate gateway host and exposure authorization:
docker compose -f deploy/compose.customer-status.yml -f deploy/compose.customer-status.proxy.yml up -d
```

The digest placeholder is operator-specific; mutable tags fail the preflight. Use the existing approved signed release manifest, independently trusted key, SBOM and exact-image scans. Build the dedicated proxy using `docker build -f deploy/Dockerfile.status-proxy -t noc-status-proxy:reviewed .`; it contains no private frontend bundle. CI scans its exact image ID with the existing fail-closed HIGH/CRITICAL gate and retains the complete report. Both gateway and proxy registry digest, security evidence and signing/publication still require operator approval.

Secret files must have correct container ownership: gateway uid1000, proxy TLS key readable only by uid101; restricted modes0600. Compose local secrets inherit host ownership, so review ownership rather than making keys world-readable. The gateway bootstrap enforces owned0600 secrets and PostgreSQL `sslmode=verify-full`; no insecure fallback. Host certificate CA trust and PostgreSQL DNS/SAN must match. Connect only approved gateway/database peers on an isolated private network; the compose network is internal by default and intentionally does not discover or attach a production DB. Operator must review a DB TLS reachability/network attachment plan. No public PostgreSQL ports.

The proxy example exposes only hostloopback18443, v2 GET/HEAD and process `/health`; every other path404. For the final separately authorized public edge, use `nginx-v2.conf.example` with operator-approved hostname/certificates/firewall. Do not proxy `/api`, Slack/SMS callbacks, signup admin endpoints or private NOC routes. No automatic DNS/firewall/Let's Encrypt changes. TLS1.2/1.3, request30/minute plusburst10, application global1200/minute and peer60/minute, bounded SQL2seconds, no error/stale proxy caching. The application bounds responses512KiB, rows200, history pages50. WordPress uses20 per page and at most ten pages. Gateway `/health` is process liveness; `/ready` internally verifies approved projection/DB and returns503 when disabled or unavailable. Do not expose readiness as operational network status.

## Publication and related communications

Use the private Customer Communications screen. Administrator approves generic public service areas (county/technology grouping supported), reviews policy baseline and prepares a canonical incident or maintenance case. Draft public text, affected areas and interruption classification; independently request/approve/publish every revision, including resolution and withdrawal. Recovery retains the existing stability and evidence requirements. Monitoring absence never approves restoration.

Prepare SMS drafts through the existing recipient/consent workflow; Twilio/Telnyx provider activation and release remain separate. **Prepare related Slack draft only** creates a deduplicated existing notification plan tied to the same canonical incident or maintenance ID. Native Slack source/provider approvals remain separate. Public publication never approves SMS or Slack delivery. No new queue or incident model is introduced.

## Diagnostics, upgrade and rollback

Settings display connection state and last successful retrieval; generic errors omit URLs, credentials and bodies. Check public DNS approval, CA trust, firewall allowlist, exact path, restricted DB role/migrations and public policy approval. A403/503 is not proof of a network outage. Missing MIBs/telemetry do not affect publication authority.

Upgrade by reviewing a new ZIP/checksum and replacing via WordPress plugin upload. Preserve settings; after DNS change explicitly reapprove. Rollback to the previously reviewed plugin ZIP compatible with v2. Disable retrieval or deactivate to show no live plugin data. Deactivation clears caches; deletion removes only this plugin's options/cache keys, never pages, NOC records or historical migrations. To withdraw public information, use an independently approved NOC withdrawal; deleting a plugin is not an incident-history deletion.

Official contracts reviewed: [WordPress nonces](https://developer.wordpress.org/apis/security/nonces/), [dynamic block registration](https://developer.wordpress.org/reference/functions/register_block_type/), [safe HTTP retrieval](https://developer.wordpress.org/reference/functions/wp_safe_remote_get/), [cURL address pinning](https://curl.se/libcurl/c/CURLOPT_RESOLVE.html). Nonces supplement capability checks; they are not authentication.
