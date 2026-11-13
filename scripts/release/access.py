"""Loopback-only development access to an internal-only Docker network.

No Docker service has an ingress or outbound network. Run as the same trusted
operator who can access the Docker socket. Keep this foreground process running.
"""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import atexit

REMOTE = """import base64,http.client,json,sys
for line in sys.stdin:
 try:
  r=json.loads(line)
  c=http.client.HTTPConnection('frontend',80,timeout=15)
  c.request(r['method'],r['path'],base64.b64decode(r['body']),r['headers'])
  response=c.getresponse()
  print(json.dumps(dict(status=response.status,headers=response.getheaders(),body=base64.b64encode(response.read()).decode())),flush=True)
  c.close()
 except Exception:
  print(json.dumps(dict(error='Internal frontend unavailable')),flush=True)
"""
ROOT = Path(__file__).resolve().parents[2]
STATE = sys.argv[1] if len(sys.argv)>1 else ''


class Tunnel:
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
    def request(self, data):
        with self.lock:
            for attempt in range(2):
                if self.process is None or self.process.poll() is not None:
                    self.process = subprocess.Popen([str(ROOT/'scripts/release/manage.sh'),STATE,
                        'exec','-T','backend','python','-u','-c',REMOTE],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
                try:
                    self.process.stdin.write(json.dumps(data)+'\n'); self.process.stdin.flush()
                    line=self.process.stdout.readline()
                    if not line: raise BrokenPipeError()
                    result=json.loads(line)
                    if 'error' in result: raise OSError(result['error'])
                    return result
                except (BrokenPipeError,ValueError):
                    self.close(); self.process=None
            raise OSError('Docker access tunnel disconnected')

TUNNEL=Tunnel()
atexit.register(TUNNEL.close)


class Handler(BaseHTTPRequestHandler):
    def forward(self):
        size=int(self.headers.get('Content-Length','0'))
        if size > 1024*1024:
            self.send_error(413); return
        data=dict(method=self.command,path=self.path,
            headers={k:v for k,v in self.headers.items() if k.lower() not in {'connection','transfer-encoding'}},
            body=base64.b64encode(self.rfile.read(size)).decode())
        try:
            response=TUNNEL.request(data)
            body=base64.b64decode(response['body'])
            self.send_response(response['status'])
            for key,value in response['headers']:
                if key.lower() not in {'connection','transfer-encoding','content-length'}: self.send_header(key,value)
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except (subprocess.SubprocessError,ValueError,OSError):
            self.send_error(503,'Isolated frontend unavailable; inspect Compose service health')
    do_GET=do_POST=do_PUT=do_PATCH=do_DELETE=do_OPTIONS=forward
    def log_message(self,*args): pass


if __name__=='__main__':
    from validate_config import validate
    validate(Path(STATE)/'acceptance.env')
    print('Isolated access on http://127.0.0.1:18090',flush=True)
    ThreadingHTTPServer(('127.0.0.1',18090),Handler).serve_forever()
