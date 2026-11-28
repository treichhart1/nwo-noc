# AI NOC / MikroTik Management boundary

AI NOC owns read-only monitoring, health, advisory IPAM observations, history, alerts, canonical inventory and cellular GIS. NOC134 adds no management command endpoint, provisioning hooks or remote execution.

The separate MikroTik Management application owns reviewed router configuration, customer provisioning/speeds, WireGuard/firewall configuration, configuration backups and approved changes. Its repository was not accessed or modified.

Future separately reviewed integration must use canonical NOC Device ID plus stable external identifier, exact source system/revision and operator-reviewed mapping; never merge by display name or transient IP alone. Proposed read-only exchange: versioned signed observation envelope, device identity, collection time, normalized evidence/units/unavailable values, source provenance/digest, idempotency key, strict allowed fields, separate credentials and verified TLS. Unknown identity or stale/conflicting receipt quarantined. No shared administrative keys; NOC cannot authorize changes by sending an alert. Actual exchange endpoints/auth/schema remain unimplemented until both applications' verified contracts are approved. This document is a boundary, not an invented native API.
