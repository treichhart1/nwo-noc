"""Private CI SNTP fixture: standard UDP123, controller UTC, no external NTP."""
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import threading
import time


class LabTime:
    def __init__(self):
        self.process=subprocess.Popen(['sudo','--preserve-env=GITHUB_ACTIONS','python3',str(Path(__file__).resolve()),'--execute-disposable-controller'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        if not select.select([self.process.stdout],[],[],10)[0] or self.process.stdout.readline()!=b'READY\n':
            self.close();raise RuntimeError('Private CI time fixture unavailable; no synchronization waiver')

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:self.process.stdin.close()
        self.process.wait(timeout=10)
        self.process.stdout.close()


def serve():
    if sys.argv[1:]!=['--execute-disposable-controller'] or os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('Explicit disposable CI controller only')
    uid,gid=int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID'])
    if uid<=0 or gid<=0:raise RuntimeError('Unprivileged controller identity required')
    server=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);server.bind(('127.0.0.1',123));server.settimeout(.2)
    os.setgroups([]);os.setgid(gid);os.setuid(uid)
    stopped=threading.Event()
    def stop_on_eof():
        sys.stdin.buffer.read();stopped.set()
    threading.Thread(target=stop_on_eof,daemon=True).start()
    def stamp():
        now=time.time()+2208988800;whole=int(now)
        return struct.pack('!II',whole,int((now-whole)*2**32))
    print('READY',flush=True)
    try:
        while not stopped.is_set():
            try:
                packet,address=server.recvfrom(512)
                if len(packet)<48 or packet[0]&7!=3:continue
                response=bytearray(48);response[:4]=bytes((0x24,1,4,236));response[12:16]=b'LOCL'
                response[16:24]=stamp();response[24:32]=packet[40:48];response[32:40]=stamp();response[40:48]=stamp()
                server.sendto(response,address)
            except socket.timeout:continue
    finally:server.close()


if __name__=='__main__':serve()
