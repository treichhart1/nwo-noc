# Packaged configuration and ownership

The root-owned release approval is separate from root-only `/etc/nwo-noc`. No repository
`.env` is imported. Generated configuration contains no production endpoints or default
administrator credential. Never commit/export these files or print `docker compose config`
without `--quiet`: resolved Compose output can contain runtime secrets.

| Resource | Purpose and access |
|---|---|
| `/etc/nwo-noc/runtime.env` | Application Settings; noc_app URL, JWT key, internal Redis URL, explicit disabled integration/job flags;0600 root |
| `migration.env` / `db-owner-password` | Non-superuser noc_owner credentials, one-shot migrations only; never given to backend/worker |
| `db-bootstrap-password` | Dedicated internal cluster bootstrap administrator; mounted only in PostgreSQL |
| `app-password` | Restricted runtime role secret; root-owned role preparation input |
| `keys/` | Generated signup PII, notification vault, Fiber integration vault and public-status rate keys; read-only app mounts, UID10001/0400 under root-private configuration parent |
| `tls-cert.pem`, `tls-key.pem` | Optional supplied certificate/key, mounted read-only to UID101 Nginx; root-private host parent |
| `backup-recipient` | Public age recipient only; private recovery identity remains off-host |
| `service.env` | Host unit environment hook; contains no application secrets |
| `installation.json`, `recovery-required.json` | Privileged state, current source/schema, interrupted-upgrade guard |

PostgreSQL is dedicated to this installation. noc_owner owns migrations without cluster
administration; noc_app has no superuser/create-role/create-db/schema-create privileges.
Neither PostgreSQL nor Redis has a published host port. The cluster bootstrap role is
not the application runtime identity. Existing host databases are not discovered or reused.

The complete source Settings inventory remains in the NOC110 integration manifest and
`backend/app/core/config.py`. Current package mode is APP_ENV=production, OFFLINE_MODE=true,
SYNTHETIC_MONITORING_ENABLED=false. No demo data or fixture adapters are seeded. Optional
MikroTik/UISP/RF/Powercode/Fiber Designer/Grafana/SMS/public signup/status integrations are
not activated. No second polling pipeline or customer authentication platform is installed.

The bounded offline planning worker is started once, and the existing core worker owns maintenance scheduling. Tower-health and Down Detector local processing may be configured with explicit boolean settings. The Fiber worker profile and live/network schedules remain disabled. Changing live integration activation requires a separately reviewed policy; verification fails closed if external safety flags change. This assignment does not authorize it.
Configuring a secret alone does not grant endpoint approval, read-only vendor permissions,
VPN/TLS/egress access, customer consent or notification/publication approval.

Core private dashboards/APIs are installed, including inventory/IPAM, RF history,
maintenance/documents, customer qualification, financial analytics and communications.
Unsupported live adapter capabilities remain unsupported. Private field QR URLs require
an operator-approved HTTPS FIELD_PUBLIC_BASE_URL/certificate/hostname setup; blank is not
a promise that QR labels will resolve before that setup. Public status publishing stays
separately isolated and approval-controlled; no website or public gateway is configured.
