# Installation verification

Run the approved checkout's `sudo ./verify.sh`, or:

```sh
sudo python3 /opt/nwo-noc/current/scripts/host/manager.py verify
```

Output is structured JSON. `PASSED` means that specific check actually succeeded;
`FAILED` requires correction and exit status1; `WARNING` identifies limited evidence;
`NOT_CONFIGURED` identifies an intentionally optional integration or missing backup
configuration. Warnings/disabled optional integrations are not converted into failures.

Checks cover Ubuntu/amd64/root, approved source inventory, private secret/data permissions,
configuration safety, Compose rendering, all six core healthy services, non-root app
runtime, no Docker-published ports, the loopback-only unprivileged ingress, one internal network, bounded container logs, PostgreSQL
connectivity/migration head/runtime privileges, worker heartbeat, Nginx syntax, frontend
and backend HTTP health/security headers, systemd and journald. It never probes equipment
or external providers. It does not print database URLs, passwords, private keys or tokens.

A configured backup recipient is not proof of an off-host restoration. Full boot,
firewall/VPN policy, disk saturation, long-duration monitoring, actual firmware, OS/native
image vulnerability scanning, Redis licensing, deployed malware scanning and key custody
remain separate acceptance gates. A userspace container rehearsal explicitly reports
systemd/boot as `WARNING`; only a disposable real systemd host can validate those services.
Neither establishes physical/server reboot recovery without an actual reboot test.

Do not publish diagnostic configuration dumps. Docker-group/root access can inspect
container environment secrets and must be restricted. The private API is never meant
to be exposed through a public WordPress reverse proxy.
