# Read-only server readiness

Run from the exact reviewed candidate, before installing anything:

```sh
python3 scripts/host/preflight.py --disk-path /
python3 scripts/host/preflight.py --disk-path /var/lib --json
```

Use an existing mount path. No file/directory is created by the utility. Save stdout only to an operator-approved protected destination, never a public status API. If explicitly approved, test a single internal name:

```sh
python3 scripts/host/preflight.py --dns-name noc-lab.example.invalid --json
```

Replace the intentionally unusable .invalid name. This is the **only** optional name-resolution request; no default external DNS lookup, scanning, radio connection, ping or registry request occurs. Default probes query local /proc, local Unix Docker socket and fixed local inventory commands. No shell, sudo invocation, install, chmod, firewall/network modification, secret/configuration file read or Docker mutation. Commands have8s deadlines and256KiB stdout budgets; stderr is discarded. DOCKER_HOST/DOCKER_CONTEXT cannot redirect the daemon probe. Run as an ordinary user first; a separately approved `sudo python3 scripts/host/preflight.py --json` can inspect local firewall/Docker permissions without modifying them. Docker group access is effectively root; do not grant membership merely to silence a warning.

|Result|Meaning|
|---|---|
|PASS|Only the specific observed check passed, not release approval|
|WARNING|Review required; e.g. rootless inspection, UFW active without rule/forwarding proof, virtual SSD indication|
|FAIL|Known blocker; unsupported OS/CPU, insufficient capacity, conflict, unsynchronized clock, missing/inaccessible required daemon|
|NOT VERIFIED|Tool/permissions/evidence absent or inherently operator-owned; never healthy by default|

Exit0 means **no observed FAIL**, not all items accepted. Exit1 means at least one FAIL. Invalid arguments exit2. JSON includes format, UTC timestamp, checks, failed count. No management addresses, MACs, DNS answers, command stderr, tokens or secret files are emitted.

Checks: Ubuntu24.04, amd64 and existing shared x86-64-v2 contract on every CPU, logical core/model inventory (dedication not inferable),8GiB provisioned RAM (>=7.5GiB visible to account for firmware reservations), available memory,100GiB total/30GiB free on the selected mount, ext4/xfs vs RAM/overlay/unvalidated filesystem, reported rotational/NVMe hints, Docker>=24, Composev2>=2.24, active systemd, private/public address counts, optional DNS, NTP synchronized flag, reserved TCP ports18090/8000/5432/6379, UFW observation, standard existing-install paths and effective permissions. Unknown future Compose majors fail for review. A DB/Redis port conflict must be investigated; never stop an unrelated service automatically.

The preflight minimum is stricter than the historical installer rejection floor, intentionally not a replacement for it. Fresh install uses loopback18090;80/443 are not opened. Re-run for separately mounted /opt, /var/lib and backup storage, inspect `findmnt`/`lsblk` locally to map virtual disks to real persistent SSDs. Reported non-rotating media does not establish PLP, fsync integrity, RAID-cache safety or acceptable IOPS. Cgroup quotas, VM overcommit, static IP persistence, approved DNS/NTP source/offset, disk health, default-deny INPUT/OUTPUT/Docker/IPv6 forwarding, VPN/MFA and external reachability require operator evidence even when related inventory passes.

Troubleshooting: unsupported CPU → select a reviewed x86-64-v2 virtual CPU before installation; unavailable Docker → inspect daemon/version/socket permissions, install only supported packages after authorization; unsynchronized clock → configure approved private time source, no unrestricted public fallback; occupied ports/managed paths → inspect ownership and existing installation, use the supported upgrade path if applicable, never delete paths/volumes; low disk → add approved persistent capacity, do not delete databases/backups; missing UFW/ip/ss/systemd → NOT VERIFIED or FAIL as shown, never automatically install or claim the check passed. The developer cloud overlay is full and is not accepted as the lab host.
