#!/usr/bin/env python3
"""Read-only Ubuntu lab readiness. No installs, writes, scans or default DNS queries."""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy import Reject, require_cpu_features

GIB = 1024 ** 3
STATUSES = frozenset({'PASS', 'WARNING', 'FAIL', 'NOT VERIFIED'})


def command(argv):
    """Fixed local read commands only; bounded captured output, no shell or stderr leaks."""
    try:
        # A file-backed pipe read is bounded even for unexpectedly large local output.
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        try:
            import selectors
            import time
            data = bytearray()
            deadline = time.monotonic() + 8
            with selectors.DefaultSelector() as sel:
                sel.register(p.stdout, selectors.EVENT_READ)
                while sel.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    for key, _ in sel.select(min(remaining, .2)):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            sel.unregister(key.fileobj)
                        else:
                            data.extend(chunk)
                            if len(data) > 262144:
                                raise ValueError()
                p.wait(timeout=max(.01, deadline - time.monotonic()))
            return data.decode('utf-8', errors='strict').strip() if p.returncode == 0 else None
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
        return None


def read(path):
    try:
        return Path(path).read_text()[:262144]
    except (OSError, UnicodeError):
        return ''


def version(text):
    m = re.fullmatch(r'v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][A-Za-z0-9][A-Za-z0-9.+~:-]*)?', text or '')
    return tuple(int(v or 0) for v in m.groups()) if m else None


def memory(text):
    values = {}
    for line in text.splitlines():
        match = re.fullmatch(r'(MemTotal|MemAvailable):\s+(\d+) kB', line)
        if match:
            values[match[1]] = int(match[2]) * 1024
    return values


def addresses(raw):
    """Report counts only; do not publish management addresses or MACs."""
    values = json.loads(raw)
    private = public = 0
    for interface in values:
        if interface.get('operstate') != 'UP':
            continue
        for info in interface.get('addr_info', []):
            addr = ipaddress.ip_address(info['local'])
            if addr.is_loopback or addr.is_link_local:
                continue
            if addr.is_global:
                public += 1
            else:
                private += 1
    return private, public


def listeners(raw):
    occupied = set()
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) >= 4:
            try:
                occupied.add(int(fields[3].rsplit(':', 1)[1]))
            except ValueError:
                pass
    return occupied


def collect(*, disk_path='/', dns_name=None, port=18090, run=command, reader=read):
    rows = []
    def add(name, status, detail):
        if status not in STATUSES:
            raise ValueError("Unknown preflight status")
        rows.append({'check': name, 'status': status, 'detail': detail})
    os_release = dict(line.split('=', 1) for line in reader('/etc/os-release').splitlines() if '=' in line)
    compatible = os_release.get('ID', '').strip('"') == 'ubuntu' and os_release.get('VERSION_ID', '').strip('"') == '24.04'
    add('os', 'PASS' if compatible else 'FAIL', 'Ubuntu 24.04 required; no OS changes made')
    add('architecture', 'PASS' if platform.machine() == 'x86_64' else 'FAIL', 'Tested packaging requires Linux amd64')
    cpuinfo = reader('/proc/cpuinfo')
    try:
        require_cpu_features(cpuinfo)
        add('cpu_abi', 'PASS', 'x86-64-v2 flags present on every reported CPU')
    except Reject:
        add('cpu_abi', 'FAIL', 'x86-64-v2 required on every CPU; generic qemu64 is insufficient')
    cores = sum(1 for line in cpuinfo.splitlines() if line.split(':', 1)[0].strip() == 'processor')
    model = next((l.split(':', 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith('model name')), 'unavailable')
    add('cpu_capacity', 'PASS' if cores >= 4 else 'FAIL', f'{cores} logical CPUs; {model[:160]}; dedicated cores/oversubscription NOT VERIFIED')
    mem = memory(reader('/proc/meminfo'))
    if not mem:
        add('memory', 'NOT VERIFIED', 'Cannot read memory inventory')
    else:
        total, available = mem.get('MemTotal', 0), mem.get('MemAvailable', 0)
        add('memory', 'FAIL' if total < 7.5 * GIB else ('WARNING' if available < 4 * GIB else 'PASS'), f'Total {total / GIB:.2f} GiB; available {available / GIB:.2f} GiB; provision >=8 GiB (>=7.5 GiB OS-visible)')
    add('resource_limits', 'NOT VERIFIED', 'Confirm VM dedication/cgroup quotas and sustained CPU/IO capacity independently')
    try:
        usage = shutil.disk_usage(disk_path)
        add('disk_capacity', 'FAIL' if usage.total < 100 * GIB or usage.free < 30 * GIB else 'PASS', f'Total {usage.total / GIB:.2f} GiB; free {usage.free / GIB:.2f} GiB; lab requires 100 GiB total and 30 GiB free before images/staging/history')
    except OSError:
        add('disk_capacity', 'NOT VERIFIED', 'Select an existing intended persistent storage mount')
    fs = run(['findmnt', '--noheadings', '--output', 'FSTYPE', '--target', str(disk_path)])
    add('filesystem', 'PASS' if fs in {'ext4', 'xfs'} else ('FAIL' if fs in {'tmpfs', 'ramfs', 'overlay'} else 'NOT VERIFIED'), f'Filesystem {fs if fs in {"ext4", "xfs", "tmpfs", "ramfs", "overlay"} else "unknown/unvalidated"}; durability/flush/power-loss protection require operator verification')
    block = run(['lsblk', '--json', '--output', 'TYPE,ROTA,TRAN'])
    try:
        def flatten(items):
            for item in items:
                yield item
                yield from flatten(item.get('children', []))
        devices = list(flatten(json.loads(block)['blockdevices']))
        ssd = sum(d.get('type') == 'disk' and d.get('rota') is False for d in devices)
        nvme = sum(d.get('type') == 'disk' and d.get('tran') == 'nvme' for d in devices)
        add('storage_medium', 'WARNING', f'{ssd} non-rotating disks, {nvme} NVMe disks reported; target mapping/virtual media/PLP NOT VERIFIED')
    except (ValueError, TypeError, KeyError):
        add('storage_medium', 'NOT VERIFIED', 'Block inventory unavailable; SSD/NVMe and target mapping need operator evidence')
    docker = version(run(['docker', '--host=unix:///var/run/docker.sock', 'version', '--format', '{{.Server.Version}}']))
    compose = version(run(['docker', 'compose', 'version', '--short']))
    add('docker', 'PASS' if docker and docker >= (24, 0, 0) else 'FAIL', 'Local daemon >=24 required; inaccessible/missing/invalid version fails; no remote DOCKER_HOST used')
    add('compose', 'PASS' if compose and compose >= (2, 24, 0) and compose[0] == 2 else 'FAIL', 'Compose v2 >=2.24 required; incompatible/unknown major rejected')
    add('systemd', 'PASS' if Path('/run/systemd/system').is_dir() else 'FAIL', 'Active systemd host required for full installer; container is not VM acceptance')
    net = run(['ip', '-json', 'address', 'show'])
    try:
        private, public = addresses(net)
        add('network', 'WARNING' if public else ('PASS' if private else 'FAIL'), f'{private} private and {public} globally routable active addresses; VPN/routing/external reachability NOT VERIFIED')
    except (ValueError, TypeError, KeyError):
        add('network', 'NOT VERIFIED', 'Address inventory unavailable')
    add('static_ip_dns', 'NOT VERIFIED', 'Operator must confirm persistent IP/gateway, approved resolvers, reverse/name/TLS records; addresses are never auto-configured')
    if dns_name:
        answer = run(['getent', 'ahosts', dns_name])
        add('dns_resolution', 'PASS' if answer else 'FAIL', 'Explicit operator-selected name resolution only; returned addresses withheld')
    else:
        add('dns_resolution', 'NOT VERIFIED', 'No DNS probe by default; use --dns-name with an approved hostname')
    ntp = run(['timedatectl', 'show', '--property=NTPSynchronized', '--value'])
    add('time_sync', 'PASS' if ntp == 'yes' else ('FAIL' if ntp == 'no' else 'NOT VERIFIED'), 'Synchronized flag only; approved NTP source, offset and reboot soak remain operator checks')
    ports = run(['ss', '-H', '-lnt'])
    if ports is None:
        add('ports', 'NOT VERIFIED', 'TCP listener inventory unavailable')
    else:
        occupied = listeners(ports)
        conflicts = occupied & {port, 8000, 5432, 6379}
        add('ports', 'FAIL' if conflicts else 'PASS', 'Reserved/private TCP port conflicts: ' + (', '.join(map(str, sorted(conflicts))) or 'none; DB/Redis must not be host-published'))
    fw = run(['ufw', 'status'])
    add('firewall', 'WARNING' if fw and fw.splitlines()[0] == 'Status: active' else 'NOT VERIFIED', 'Local UFW status only; independently test IPv4/IPv6 INPUT/OUTPUT/Docker forwarding and default-deny rules; no firewall changes')
    exists = [str(p) for p in map(Path, ('/opt/nwo-noc', '/etc/nwo-noc', '/var/lib/nwo-noc')) if p.exists() or p.is_symlink()]
    add('existing_installation', 'FAIL' if exists else 'PASS', 'Managed paths present: ' + ', '.join(exists) if exists else 'No standard managed paths; confirm custom installs/Docker projects separately')
    add('permissions', 'PASS' if os.geteuid() == 0 else 'WARNING', 'Root enables local firewall/daemon inspection; nonroot omissions are NOT VERIFIED; preflight itself never needs to change permissions')
    add('security_approval', 'NOT VERIFIED', 'Lab authorization, trusted artifacts, TLS, VPN/MFA, malware scanner, encrypted backups and licensing require separate evidence; this report is not installation approval')
    return {'format': 1, 'scope': 'Read-only host readiness, not release or equipment acceptance',
            'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'checks': rows,
            'failed': sum(r['status'] == 'FAIL' for r in rows)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--disk-path', default='/', help='Existing intended persistent mount; no directories created')
    p.add_argument('--dns-name', help='Optional explicit approved hostname; triggers a bounded resolver query')
    p.add_argument('--port', type=int, default=18090)
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    if not 1024 <= a.port <= 65535:
        p.error('Unprivileged dashboard port 1024..65535 required')
    if a.dns_name and (len(a.dns_name) > 253 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', a.dns_name)):
        p.error('Only an approved hostname is accepted; no URLs or command options')
    report = collect(disk_path=a.disk_path, dns_name=a.dns_name, port=a.port)
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        for row in report['checks']:
            print(f"{row['status']:12} {row['check']}: {row['detail']}")
        print('Readiness only: NOT VERIFIED items must be resolved by the operator; no installation or network changes performed.')
    return 1 if report['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
