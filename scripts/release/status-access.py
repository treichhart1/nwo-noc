#!/usr/bin/env python3
"""Loopback-only CI bridge to public ASGI inside the existing isolated backend.

No private routes, cookies, credentials or arbitrary proxy destinations accepted.
"""
import sys,subprocess,json
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
STATE=Path(sys.argv[1]).resolve()
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*args,**kw):super().__init__(*args,directory=str(ROOT/'frontend/dist-status'),**kw)
 def log_message(self,*args):pass
 def do_GET(self):
  if self.path=='/health' or self.path=='/public/network-status/v1' or self.path.startswith('/public/network-status/v1?'):
   # Internal service destination/path are fixed; request headers are not forwarded.
   script="import urllib.request,urllib.error;u='http://127.0.0.1:8002"+self.path.replace("'",'%27')+"';\ntry:\n r=urllib.request.urlopen(u,timeout=5);print(r.status);print(r.read().decode())\nexcept urllib.error.HTTPError as e:\n print(e.code);print(e.read().decode())"
   # Query is strictly bounded to public pagination numbers before it reaches Python code.
   from urllib.parse import urlsplit,parse_qs
   q=parse_qs(urlsplit(self.path).query)
   if set(q)-{'history_limit','history_offset'} or any(len(v)!=1 or not v[0].isdigit() for v in q.values()):self.send_error(400);return
   if len(self.path)>150:self.send_error(400);return
   result=subprocess.run([str(ROOT/'scripts/release/manage.sh'),str(STATE),'exec','-T','backend','python','-c',script],capture_output=True,text=True,timeout=12)
   lines=result.stdout.splitlines()
   if result.returncode or not lines or not lines[0].isdigit():self.send_error(503);return
   data='\n'.join(lines[1:]).encode();self.send_response(int(lines[0]));self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
  if self.path.split('?')[0] in {'/','/network-status.html'}:self.path='/network-status.html';return super().do_GET()
  if self.path.startswith('/assets/') and '..' not in self.path:return super().do_GET()
  self.send_error(404)
ThreadingHTTPServer(('127.0.0.1',18092),Handler).serve_forever()
