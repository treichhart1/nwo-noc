"""Loopback-only synthetic signup access. Never exposes private NOC paths or bundles."""
import sys,json,base64,importlib.util,http.client
from pathlib import Path
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
ROOT=Path(__file__).resolve().parents[2]
LOCAL='--local' in sys.argv
if not LOCAL:
    spec=importlib.util.spec_from_file_location('noc_access',Path(__file__).with_name('access.py'))
    access=importlib.util.module_from_spec(spec);spec.loader.exec_module(access)
    from validate_config import validate
    validate(Path(sys.argv[1])/'acceptance.env')
    access.REMOTE=access.REMOTE.replace("'frontend',FRONTEND_PORT_VALUE","'127.0.0.1',8001")
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kw):super().__init__(*args,directory=str(ROOT/'frontend/dist-signup'),**kw)
    def log_message(self,*args):pass
    def api(self):
        if not self.path.startswith('/public/signup/'):self.send_error(404);return
        size=int(self.headers.get('Content-Length','0'))
        if size>32000:self.send_error(413);return
        data=dict(method=self.command,path=self.path,headers={k:v for k,v in self.headers.items() if k.lower() not in {'connection','transfer-encoding'}},body=base64.b64encode(self.rfile.read(size)).decode())
        try:
            if LOCAL:
                c=http.client.HTTPConnection('127.0.0.1',18196,timeout=10);c.request(data['method'],data['path'],base64.b64decode(data['body']),data['headers']);r=c.getresponse();result={'status':r.status,'headers':r.getheaders(),'body':base64.b64encode(r.read()).decode()};c.close()
            else:result=access.TUNNEL.request(data)
            raw=base64.b64decode(result['body']);self.send_response(result['status'])
            for k,v in result['headers']:
                if k.lower() not in {'connection','transfer-encoding','content-length'}:self.send_header(k,v)
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        except Exception:self.send_error(503,'Isolated signup unavailable')
    def do_GET(self):
        if self.path.startswith('/public/signup/'):return self.api()
        path=self.path.split('?')[0]
        if path=='/':self.path='/signup.html'
        elif not(path=='/signup.html' or path.startswith('/assets/')):self.send_error(404);return
        candidate=(ROOT/'frontend/dist-signup'/self.path.lstrip('/').split('?')[0]).resolve()
        if not candidate.is_relative_to((ROOT/'frontend/dist-signup').resolve()):self.send_error(404);return
        return super().do_GET()
    do_POST=api
print('Synthetic signup at http://127.0.0.1:18091 (loopback only)',flush=True)
ThreadingHTTPServer(('127.0.0.1',18091),Handler).serve_forever()
