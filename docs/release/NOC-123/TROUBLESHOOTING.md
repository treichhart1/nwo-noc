# Guided lab troubleshooting

Stop on any failed required check. Preserve evidence; do not delete databases, volumes, releases, keys or unrelated services. Commands are for a separately authorized isolated lab only.

|Failure|Safe action|
|---|---|
|Wrong/dirty source|Compare independently reviewed commit/tree and `git status --porcelain`; preserve unfinished changes in the existing checkout, obtain a clean exact-source clone. Never reset other work to pass|
|Existing managed paths|Fresh installation is refused. Inspect ownership/history; use the separately approved upgrade or empty-host recovery procedure. Do not remove paths to bypass detection|
|Unsupported OS/CPU|Require Ubuntu24.04 amd64/systemd and x86-64-v2 on every CPU. Review VM CPU model; no automatic host/hypervisor changes|
|Memory/storage/private network/time fail|Meet actual target minimum and persistent storage; review private addressing and approved NTP synchronization. Unknown controls are not healthy. No filesystem/durability/threshold waiver|
|Docker/Compose/dependency unavailable|Inspect supported local daemon/socket/version and Python3.12/python3-yaml/OpenSSL/age. Operator installs missing signed packages under independent approval; wrapper does not overwrite services|
|Preparation path already exists|Inspect protected logs. Use a new approved path after correcting the verified defect. Preserve the failed directory|
|Python/npm audit failure|Inspect private pip-audit.json/npm-audit.json and logs. All dependencies including development are required; no force/ignore/omit-dev fallback|
|Scanner failure or HIGH/CRITICAL finding|Keep full raw reports and SPDX. No clean scan is inferred. Fix supported packages or retain blocker; no advisory suppression|
|Missing/mismatched/altered/stale evidence|Verify source/image IDs/locks/transport hashes; regenerate audits with prepare. Do not edit prepared checksums or relabel images|
|Authorization/purpose rejected|Obtain independent isolated-lab approval, exact operation confirmation and correct protected file. A production-purpose approval is intentionally rejected by this wrapper|
|No controlling terminal|Use an interactive secure terminal or root0600 nonsecret authorization file under reviewed automation. Do not place passwords/private keys in it|
|TLS browser error|Verify localhost certificate fingerprint/trust through an authenticated channel. Use the private tunnel; never -k or a public listener. Lab certificate expires after7days|
|Install/startup/verify fails|Review protected preparation logs and journald, retain recovery state and inspect exact tested image identity. Do not disable health checks or silently extend deadlines|
|Backup recipient or recovery failure|Check the approved public recipient and independent private-identity custody, ciphertext/checksums and matching release/DB/config/docs. Never restore over newer data|

Useful existing commands:

```sh
python3 scripts/host/preflight.py --json
sudo ./verify.sh --controlled-test
sudo systemctl status nwo-noc.service nwo-noc-ingress.service
sudo journalctl -u nwo-noc.service -u nwo-noc-ingress.service --since today
sudo ss -lntup
```

Do not publish runtime.env, resolved Compose configuration, administrator input, lab.key, recovery identities, customer contacts or private telemetry. Guided build/audit logs are root0600 under a root0700 directory; retain them for the authorized reviewer. The controlled-test manifest/local image IDs are not production publisher signatures or registry digests.

The CI time fixture is a loopback-only unprivileged responder after root binds UDP123, using controller UTC. Actual server time sources/VPN/MFA/current malware definitions and physical offsite restore require independent operator evidence. A passing fixture never authorizes a live pilot. Wave official MIB/secure protocol compatibility remains blocked; supply documented exact firmware/protocol evidence rather than adding insecure fallbacks.
