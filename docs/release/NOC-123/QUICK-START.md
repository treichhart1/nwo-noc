# Separately authorized empty Ubuntu24.04 lab

**Not a production release.** Obtain named isolated-lab authorization first. Use4modern dedicated x86-64-v2 cores,8GiB RAM,100GiB persistent SSD and approved private DNS/NTP/VPN/MFA/SSH/firewall/TLS/backup arrangements. The wrapper does not configure networks or install missing host packages.

Supported prerequisites: Ubuntu24.04 amd64/systemd, Git, Python3.12/python3-yaml, Docker>=24, Composev2>=2.24, OpenSSL, age/age-keygen, CA certificates and logrotate. Operator installs missing supported packages through approved repositories; do not overwrite compatible services. Build/audit egress needs approved Ubuntu/PyPI/npm/Docker endpoints; target installation can use a checked bundle offline. No device-management network egress is needed.

Copy the **exact final commit and tree from the NOC123 draft PR ledger**, independently reviewed by the operator. Replace both intentional placeholders; do not select a moving branch or derive an approval from an unreviewed checkout:

```sh
LAB_COMMIT=REVIEWED_40_CHARACTER_NOC123_COMMIT
LAB_TREE=REVIEWED_40_CHARACTER_NOC123_TREE
git clone https://github.com/treichhart1/nwo-noc.git nwo-noc-lab
cd nwo-noc-lab
git fetch origin '+refs/heads/*:refs/remotes/origin/*'
git checkout --detach "$LAB_COMMIT"
git status --porcelain
git rev-parse HEAD
git rev-parse HEAD^{tree}
python3 scripts/host/preflight.py --json
python3 scripts/host/lab.py plan --commit "$LAB_COMMIT" --tree "$LAB_TREE"
```

Plan does not authorize changes. Read every FAIL/WARNING/NOT VERIFIED result; a zero exit code is not release approval. Before prepare, create an **independently held** lab recovery identity using the approved recovery workstation/broker, retain its private age identity there and transfer only the public age1 recipient through a trusted channel. No private key is entered into the guided prompts. Approve an encrypted backup destination separately; this utility records it but does not configure automatic offsite delivery.

On the approved empty lab host, prepare images and evidence. It prompts for authorization reference, private SSH hostname, backup destination description/public recipient, reviewed isolation/access/risk/custody confirmations and the exact phrase **PREPARE ISOLATED LAB**:

```sh
sudo python3 scripts/host/lab.py prepare --execute \
  --commit "$LAB_COMMIT" --tree "$LAB_TREE" --work /root/nwo-noc-lab
```

Preparation builds/audits/scans all four images and preserves evidence. Do not rerun over an existing preparation directory or edit its checksums. On failure inspect protected logs, correct the verified cause and use a new approved directory. No application is installed yet.

Then separately confirm **INSTALL ISOLATED LAB**. The existing initializer privately prompts twice for the new administrator password (16–72 UTF-8 bytes). Never put it in shell history, arguments, environment or metadata:

```sh
sudo python3 scripts/host/lab.py install --execute \
  --commit "$LAB_COMMIT" --tree "$LAB_TREE" --work /root/nwo-noc-lab
sudo ./verify.sh --controlled-test
sudo systemctl status nwo-noc.service nwo-noc-ingress.service
```

The wrapper emits **https://localhost:18090/** and the approved tunnel command. Verify the seven-day lab certificate fingerprint out of band and trust only that lab certificate; do not bypass TLS checks:

```sh
sudo openssl x509 -in /root/nwo-noc-lab/lab.crt -noout -fingerprint -sha256
ssh -L 18090:127.0.0.1:18090 APPROVED_PRIVATE_LAB_SSH_HOST
```

Open https://localhost:18090/ on the trusted tunnel client. Do not publish18090, backend8000, PostgreSQL5432 or Redis6379, configure public DNS, enable anonymous Grafana or deploy the public signup/status gateway. Follow the ordered [initial configuration](LAB-INSTALLATION.md) with synthetic data only.

Actual backup, after explicitly approving quiescence:

```sh
sudo python3 /opt/nwo-noc/current/scripts/host/manager.py backup --controlled-test --quiesce
sudo ./verify.sh --controlled-test
```

Copy encrypted backup plus verified metadata to the approved independent destination and execute the reviewed empty-host restore procedure. Separately authorize a VM reboot, record boot ID before/after and rerun verify. Keep production signing, licensing, native-risk and real-equipment gates blocked. This guide grants no installation on an NWO operational server or equipment access.
