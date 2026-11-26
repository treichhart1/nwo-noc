"""Private CI SNTP setup before privilege drop; no sudo in hardened controller."""
import os
from pathlib import Path
import select
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time


def control_root(path):
    root=Path(path);st=root.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:
        raise RuntimeError('Private original-user CI clock control required')
    return root


class LabTime:
    def __init__(self):
        if os.environ.get('GITHUB_ACTIONS')!='true' or os.getuid()==0:
            raise RuntimeError('Unprivileged original disposable job user required')
        self.root=Path(tempfile.mkdtemp(prefix='noc123-clock-',dir=os.environ['RUNNER_TEMP']))
        self.process=subprocess.Popen(['sudo','--preserve-env=GITHUB_ACTIONS','python3',str(Path(__file__).resolve()),'--execute-disposable-controller',str(self.root),str(os.getpid())],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if not select.select([self.process.stdout],[],[],10)[0] or self.process.stdout.readline()!=b'READY\n':
            self.close();raise RuntimeError('Private CI time fixture unavailable; no synchronization waiver')

    @classmethod
    def adopt(cls):
        result=cls.__new__(cls);result.root=control_root(os.environ['NOC_CI_TIME_ROOT']);result.process=None
        return result

    def close(self):
        stop=self.root/'STOP'
        if not stop.exists():
            fd=os.open(stop,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
        if self.process:
            self.process.wait(timeout=10);self.process.stdout.close();self.process.stderr.close()


def serve():
    if len(sys.argv)!=4 or sys.argv[1]!='--execute-disposable-controller' or os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('Explicit disposable CI controller only')
    uid,gid=int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID']);parent=int(sys.argv[3])
    if uid<=0 or gid<=0 or parent<=1:raise RuntimeError('Original job identity required')
    server=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);server.bind(('127.0.0.1',123));server.settimeout(.2)
    os.setgroups([]);os.setgid(gid);os.setuid(uid)
    root=control_root(sys.argv[2]);deadline=time.monotonic()+10800
    def stamp():
        now=time.time()+2208988800;whole=int(now)
        return struct.pack('!II',whole,int((now-whole)*2**32))
    print('READY',flush=True)
    try:
        while time.monotonic()<deadline and not (root/'STOP').exists():
            try:os.kill(parent,0)
            except ProcessLookupError:break
            except PermissionError:pass
            try:
                packet,address=server.recvfrom(512)
                if len(packet)<48 or packet[0]&7!=3:continue
                response=bytearray(48);response[:4]=bytes((0x24,1,4,236));response[12:16]=b'LOCL'
                response[16:24]=stamp();response[24:32]=packet[40:48];response[32:40]=stamp();response[40:48]=stamp()
                server.sendto(response,address)
            except socket.timeout:continue
    finally:server.close()


if __name__=='__main__':serve()
