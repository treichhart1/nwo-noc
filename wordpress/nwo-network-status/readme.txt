=== NWO Network Status ===
Requires at least: 6.5
Requires PHP: 8.1
Stable tag: 128.0.0
License: GPL-2.0-or-later

Displays approved public NOC status only. Requires PHP cURL/TLS and mbstring.
No credentials, private endpoints, redirects or TLS verification bypasses.

== Installation ==
Upload the reproducibly built nwo-network-status.zip through Plugins > Add New.
Activate per-site, then Settings > NWO Network Status. An administrator must
approve a public HTTPS gateway URL and its public DNS addresses. Reapprove
when DNS changes. Create a page with [nwo_network_status], or insert the
NWO Network Status Gutenberg block. Banner: [nwo_network_status view="banner"].

== Cache ==
Approved snapshots are cached for 15 seconds. Retrieval failure immediately
shows unknown, without stale healthy status or withdrawn incident text.
Browsers expire snapshots independently. Page HTML always starts unknown.
Refresh can be configured between 15 and 60 seconds. Disable page/CDN caching
for /wp-json/nwo-status/v1/feed. DNS is pinned to approved public addresses.

== Uninstall ==
Deactivation clears caches. Uninstall deletes only this plugin's settings,
diagnostics and bounded cache keys; pages and NOC records remain untouched.
Network-wide multisite activation is intentionally unsupported.
