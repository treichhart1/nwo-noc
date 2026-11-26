# Explicitly authorized isolated Ubuntu lab

This procedure is **controlled-test**, not an approved production release. It uses the existing installer without changing production signature checks. Only execute after the operator approves a new disposable Ubuntu24.04 VM, its isolated network, bootstrap egress and synthetic data. It is not authorization to install on an NWO operational server. Keep the console and a hypervisor snapshot; never target a host with an existing installation or unrelated workloads.

## Prerequisites and authorization record

Record owner/approval, exact candidate commit/tree and PR, Ubuntu image provenance, CPU model/ABI, RAM/disk, static private address/gateway, approved DNS/NTP, VPN/MFA and SSH sources, allowed bootstrap package/registry destinations, backup custody and expiration/destruction of this disposable lab. Require minimum4modern dedicated x86-64-v2 cores,8GiB RAM,100GiB persistent SSD. Apply [preflight](SERVER-PREFLIGHT.md) and resolve FAIL/NOT VERIFIED security checks before installation. Containers without systemd are not full VM acceptance.

An independently approved operator prepares the host using supported Ubuntu repositories. Check actual versions before installing missing packages; do not overwrite Docker daemon settings or replace compatible services:

```sh
cat /etc/os-release
lscpu
free -h
df -h /
timedatectl show -p NTPSynchronized
sudo sshd -t
sudo ufw status verbose
docker --host=unix:///var/run/docker.sock version
docker compose version
python3.12 --version
```

Needed: git, Docker>=24/Composev2>=2.24, Python3.12, python3-yaml, age/age-keygen, OpenSSL, CA certificates, logrotate and systemd. The installer already provisions missing supported dependencies; the operator must approve its bounded repository egress. Prebuilt deployment does not require host Node, another host PostgreSQL or Redis. Release frontend builds retain Node22. Do not blindly paste firewall/SSH/network changes; use console-protected operator procedures in [PILOT-CHECKLIST](PILOT-CHECKLIST.md).

## Exact source and lab-only images

Use the **final reviewed NOC122 commit from its draft PR ledger**, not this document's historical base or a moving branch. Below placeholders must be replaced by the operator:

```sh
git clone https://github.com/treichhart1/nwo-noc.git nwo-noc-lab
cd nwo-noc-lab
git fetch origin '+refs/heads/*:refs/remotes/origin/*'
git checkout --detach REVIEWED_40_CHARACTER_NOC122_COMMIT
git status --porcelain
git rev-parse HEAD
git rev-parse HEAD^{tree}
python3 scripts/host/preflight.py --json
```

A clean tree is required. Fetch retains side-history used by the integration audit, including the reviewed non-ancestor replay; a HEAD-only shallow archive is insufficient. Operator-approved local lab builds use the actual four Dockerfiles and source labels. These tags are only build handles; the approval below resolves immutable local IDs. It is not production publication/provenance:

```sh
LAB_SOURCE=$(git rev-parse HEAD)
docker build --no-cache --label "org.opencontainers.image.revision=$LAB_SOURCE" -t noc122-lab-backend backend
docker build --no-cache --label "org.opencontainers.image.revision=$LAB_SOURCE" -t noc122-lab-frontend frontend
docker build --no-cache --label "org.opencontainers.image.revision=$LAB_SOURCE" -t noc122-lab-postgres -f deploy/Dockerfile.postgres .
docker build --no-cache --label "org.opencontainers.image.revision=$LAB_SOURCE" -t noc122-lab-redis -f deploy/Dockerfile.redis .
```

Review complete SBOM/dependency/container reports for **these exact image IDs**, with the same pinned scanning tools/gates as CI, before accepting them. The Dockerfile rejects Expat below2.6.1-2ubuntu0.7. Old CI ImageIDs are neither registry digests nor scans of a new local build. Do not suppress new findings or use mutable tags in an approval. Production publication still requires approved registry digests, complete evidence, detached Ed25519 signature and independently trusted key; see [NOC120 PUBLICATION](../NOC-120/PUBLICATION.md). No production private key is used here.

## Lab-only approval, backup recipient and TLS

Only on the separately authorized empty VM, create a protected directory **outside** the source. Abort if it exists; never overwrite an earlier lab. The following root shell prompts for an approval reference; image IDs/source/tree are resolved by fixed commands. It writes a controlled-test manifest which the release-purpose installer deliberately rejects. No production signing gate is bypassed because it is not used as a release:

```sh
sudo bash
set -Eeuo pipefail
umask 077
# First cd to the exact reviewed source checkout; confirm pwd and HEAD.
mkdir -m 700 /root/noc122-lab
python3 - <<'PY'
import json, pathlib, subprocess
root=pathlib.Path('/root/noc122-lab')
def out(*args):return subprocess.check_output(args,text=True).strip()
assert not out('git','status','--porcelain','--untracked-files=all'), 'Clean reviewed source required'
print('Enter the independent isolated-lab authorization reference: ',end='',flush=True)
with open('/dev/tty') as tty:ref=tty.readline().strip()
assert ref and 'REPLACE' not in ref
images={role:out('docker','--host=unix:///var/run/docker.sock','image','inspect','--format','{{.Id}}','noc122-lab-'+role) for role in ('backend','frontend','postgres','redis')}
a={'format':1,'repository':'treichhart1/nwo-noc','purpose':'controlled-test','approval_reference':ref,'commit':out('git','rev-parse','HEAD'),'tree':out('git','rev-parse','HEAD^{tree}'),'migration_head':'0028_customer_status','upgrade_from':[],'images':images}
with (root/'approval.json').open('x') as f:json.dump(a,f,indent=2)
PY
age-keygen -o /root/noc122-lab/backup.key
chmod 600 /root/noc122-lab/backup.key
age-keygen -y /root/noc122-lab/backup.key > /root/noc122-lab/backup.recipient
openssl req -x509 -newkey rsa:3072 -sha256 -days 7 -nodes \
  -subj /CN=localhost -addext subjectAltName=DNS:localhost \
  -keyout /root/noc122-lab/tls.key -out /root/noc122-lab/tls.crt
chmod 600 /root/noc122-lab/approval.json /root/noc122-lab/tls.key /root/noc122-lab/tls.crt
exit
```

Run the root shell from the exact checkout. If root Git reports dubious ownership, independently approve only that exact checkout using Git's safe.directory mechanism; never wildcard trust. Never change HOME for convenience or expose keys. The one-week self-signed certificate is **lab-only**, explicitly trusted for localhost, not a real host PKI deployment. Move the **backup private identity** into independently protected lab recovery custody before relying on offhost recovery; retain only its public recipient where practical. Do not log its contents. `age-keygen` reports the public recipient, not a password. Production requires approved PKI/key custody, not these disposable keys.

## Actual installation and dashboard

```sh
sudo ./install.sh --controlled-test --approval /root/noc122-lab/approval.json \
  --backup-recipient "$(sudo cat /root/noc122-lab/backup.recipient)" \
  --tls-cert /root/noc122-lab/tls.crt --tls-key /root/noc122-lab/tls.key \
  --tls-ca /root/noc122-lab/tls.crt
sudo ./verify.sh --controlled-test
sudo systemctl status nwo-noc.service nwo-noc-ingress.service
sudo ss -lntup
```

The only command substitution is a **public age recipient**. Administrator password is privately prompted twice,16–72 UTF-8 bytes, no default. Do not put it in argv/environment/history. Protected state lives in /etc/nwo-noc, /var/lib/nwo-noc, /var/log/nwo-noc, /var/backups/nwo-noc; releases in /opt/nwo-noc with unprivileged application containers and restricted permissions. PostgreSQL16/Redis7 are private Compose services, not host-public ports. Both workers and frontend must be healthy; migration head0028_customer_status; background jobs/integrations disabled as specified by policy. An intentionally disabled optional integration is NOT_CONFIGURED, not a failed install or compatibility proof.

Dashboard: from an approved VPN/SSH client:

```sh
ssh -L 18090:127.0.0.1:18090 APPROVED_LAB_SSH_HOST
```

Import only the lab certificate through an authenticated channel and verify its SHA256 fingerprint independently. Open **https://localhost:18090/** with that certificate trusted. Do not use curl -k/browser certificate exceptions or expose18090 publicly. After login follow the ordered administrator checklist. Record actual health/login/permission results; no synthetic account is a Powercode login.

## Synthetic operation, backup, reboot and upgrade

Keep OFFLINE_MODE=true and every external collector/provider/notification/public publishing switch false. Do not enable SYNTHETIC_MONITORING_ENABLED merely because this is a lab: seed approved synthetic records through existing authenticated UI/fixture tests; no generated signed deployment file edits. Review topology, inventory, IPAM, work orders and incident evidence with non-customer sample data.

After approving temporary downtime, test a real encrypted backup:

```sh
sudo python3 /opt/nwo-noc/current/scripts/host/manager.py backup --controlled-test --quiesce
sudo ./verify.sh --controlled-test
```

Record ciphertext/hash metadata securely, copy to independent protected storage, verify transfer and conduct an empty-host restore using the reviewed [BACKUP_RESTORE](../../../BACKUP_RESTORE.md) procedures. Do not overwrite databases or imply a turnkey production recovery tool exists. Existing tests/installer/pilot_vms.py automates same-hypervisor two-VM acceptance with source powered off; it is guarded GitHub CI, not a production restore command. Physical offsite recovery remains operator-owned.

Separately approve a lab VM reboot, record kernel boot ID before/after and run verify again. Supported lab upgrade requires a separately reviewed target checkout and new **controlled-test** approval with `upgrade_from` containing the installed commit and matching immutable four-role images. Then use `sudo ./upgrade.sh --controlled-test --approval /root/APPROVED-LAB-TARGET.json`, followed by verify. A source snapshot is not an automatically approved upgrade. If migration starts and fails, retain recovery-required.json, releases, backups and newer data; never delete the guard or automatically downgrade schema. Restore the matching database/release into a **different empty VM** and obtain cutover approval.

## Executable disposable regression alternative

On a dedicated throwaway systemd Ubuntu24.04 VM **only**, the existing harness creates synthetic credentials/approvals/data, runs install/verify/upgrades/negative tests and checks encrypted backup/restore. It performs privileged service installation, so this is not a readiness probe:

```sh
docker build -t noc113-backend:base backend
docker build -t noc113-frontend:base frontend
docker build -t noc117-postgres:rc -f deploy/Dockerfile.postgres .
docker build -t noc120-redis:rc -f deploy/Dockerfile.redis .
sudo python3 tests/installer/test_ubuntu_host.py --workspace /var/tmp/noc122-acceptance --extended
```

These are the existing CI harness tag contracts, not historical image reuse. Use a new nonexistent workspace and empty host; never rerun to overwrite an installation. This automated regression is distinct from a long-lived interactive lab. The independent two-VM harness and ten required CI jobs provide separate evidence; inspect exact final commit results in this PR.

Stop on any bad signature/digest, source mismatch, new unreviewed vulnerability, public binding, failed service/verify, time drift, insecure scanner or wrong permissions. Preserve diagnostic/audit metadata without runtime.env, resolved Compose secrets, private keys, customer data or passwords. Snapshot/stop the disposable VM under operator authority; do not remove historical migrations, unrelated services or data. No phase here authorizes live equipment, Powercode or customer communications.
