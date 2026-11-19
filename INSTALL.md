# Ubuntu24.04 installation

This is the **complete integrated AI NOC**, based on the unmerged NOC112 stack.
It is not a demo: no synthetic sites, customers or telemetry are seeded. AI Fiber
Designer and MikroTik Management remain separate products. Current release reports
still prohibit production/live-device pilot activation. This package is for controlled
acceptance until the release/security/operator gates are approved.

## Requirements and trust

Use a dedicated, disposable Ubuntu Server24.04LTS amd64 machine for acceptance.
Minimum controlled-test floor:2 CPU cores,4GiB RAM and10GiB free disk, plus room for
images, database growth and at least twice the persistent state for backups. This
is not a production sizing guarantee. Baseline1,000-device polling exceeds15seconds;
size collection intervals with measured headroom and perform a sustained soak.

The host needs supported Ubuntu repositories, Python3.12, python3-yaml, age,
ca-certificates, Docker>=24 and Compose>=2.24. The installer installs missing supported
packages and respects an existing compatible Docker service/configuration. Application
Python3.12/PostgreSQL16/Redis7/Nginx run in approved containers. Node22 is used by the
release frontend build; prebuilt frontend deployment does not need host Node/npm.
No second native PostgreSQL/Redis/Nginx installation overwrites host services.

**Do not run arbitrary development code with sudo.** Obtain an independently approved
source commit and complete backend/frontend images from the trusted release process.
Verify the publisher signature/attestation through your release authority. The package
checks commit/tree, ancestry, image digests, OCI revision labels and runtime UIDs;
these consistency checks do not replace independent artifact authenticity verification.
There is no production-approved manifest or published image registry in this draft PR.

An authorized operator must create `/etc/nwo-noc-release.json`, root-owned0600,
outside the checkout, from `deploy/host/approved-release.example.json`. Supply the exact
repository,40-character commit/tree, approved image digests, migration head, supported
upgrade source commits and a real approval reference. Replace every placeholder.
The default `release` purpose requires registry `name@sha256:...` references. Explicit
`--controlled-test` accepts a matching controlled-test approval and local immutable
image IDs for disposable acceptance only. The installer never approves its own source.

```sh
git clone <approved-release-repository-url> nwo-noc
cd nwo-noc
git checkout --detach <independently-approved-commit>
sudo ./install.sh
sudo ./verify.sh
```

The checkout must be clean and match the approval exactly. The full dependency/migration
manifest must validate; no subset of modules is installed. Installer output and logs do
not include secret values. Repository `.env` files are never read for host deployment.

## Administrator and access

The installer prompts privately for a new16–72-byte password twice. There is no default
administrator password, public setup endpoint or setup token. Consequently token expiry
is not applicable: no token is issued or retained. Privileged automation may explicitly
use `--password-stdin` with a protected credential broker; never put a password in argv,
shell history, an environment variable or CI output. The bootstrap transaction refuses
an existing administrator and records an audit event. Services never run `app.seed`.
The generated `ADMIN_PASSWORD` configuration value is an unused random Settings
compatibility secret, **not** the operator's login password; it cannot reset an account.

Default dashboard URL: `http://127.0.0.1:18090`. Reach it through an approved private
SSH/VPN tunnel, for example `ssh -L 18090:127.0.0.1:18090 approved-noc-host`. Do not
forward on0.0.0.0, open public firewall rules or point public DNS at the dashboard.
Only the bounded host ingress binds loopback in this package; no Docker service publishes a port. The ingress forwards raw TCP/TLS to Nginx over a private Unix socket without Docker access or request replay. Direct VPN-interface publishing requires separate review. Public status/signup gateways are not installed or exposed.

Optional operator-provided TLS:

```sh
sudo ./install.sh --tls-cert /root/approved-cert.pem --tls-key /root/approved-key.pem --tls-ca /root/private-ca.pem
```

Inputs must be protected root-owned files. Certificates must match `localhost` for the
local verification URL; a VPN reverse-proxy hostname/QR base URL needs a separately
approved certificate/access design. No public ACME/Let's Encrypt/DNS action occurs.

## Layout and services

- `/opt/nwo-noc/releases/<commit>`: immutable source snapshot, generated Compose/Nginx config and source inventory.
- `/opt/nwo-noc/current`: atomically selected release.
- `/etc/nwo-noc`: root-only runtime/migration secrets, separate bootstrap DB secret, private application encryption keys, optional TLS and public age recipient.
- `/var/lib/nwo-noc`: installation/recovery journal and private documents, monitoring spool, signup payloads and terrain data.
- PostgreSQL/Redis: uniquely named Docker volumes, following the existing Compose architecture; inspect the stored Compose file to locate them. They are not disposable release directories.
- `/var/log/nwo-noc`: sanitized installer events; container logs are bounded10MiB×3; host orchestration logs to journald; application container logs use bounded Docker logging.
- `/var/backups/nwo-noc`: encrypted local backups, mode0600, never served by Nginx.

Host account/group `nwo-noc`, UID/GID10001, has no login and no Docker group membership. Runtime
backend/worker containers run10001:10001; frontend Nginx runs101:101. A root systemd
unit performs narrow Docker orchestration because access to the Docker socket is itself
privileged. The separate ingress unit runs as nwo-noc with no capabilities or Docker access, at most32 connections,64KiB buffers per direction,30s idle and300s lifetime limits. It does not parse or log credentials. Host orchestration does not run application code as host root. All application containers
retain capability drops, read-only filesystems, restricted tmpfs and an internal-only
network. Existing Nginx sites, databases, DNS and host firewall configuration are untouched.

```sh
sudo systemctl status nwo-noc.service
sudo systemctl restart nwo-noc.service
sudo systemctl stop nwo-noc.service
sudo journalctl -u nwo-noc.service --since today
sudo ./verify.sh
```

The core worker and the bounded local terrain-planning worker each start once. The maintenance scheduler runs inside the existing core worker. The Fiber integration profile, external providers, public endpoints and optional network schedules remain off. See CONFIGURATION.md before any change. A clean install refuses existing
managed paths and never silently becomes an upgrade.

## NOC116 signed release prerequisite

Production-purpose install/upgrade now fails closed unless the exact approved JSON
has a detached Ed25519 signature at <approval-path>.sig and an independently trusted
root-owned0600 public key at /etc/nwo-noc-release.pub. No private signing key belongs
on the NOC host. /usr/bin/openssl must be available before release verification. See
[trusted-artifact procedure](docs/release/NOC-116/trusted-artifacts.md). Explicit
controlled-test approval remains isolated and does not authorize a release/live pilot.

## NOC117 PostgreSQL image prerequisite

New production-purpose approvals must also include the hardened PostgreSQL registry
digest and matching NOC source-revision label. Do not reuse a two-image NOC116 approval
or silently substitute the upstream base. Sign a reviewed three-image target manifest,
retain supported upgrade_from ancestry and test encrypted backup/restore first. See
[container security report](docs/release/NOC-117/README.md). This is not production
authorization; all unresolved native/npm findings and operator gates remain.
