"""Bounded, exact-origin, GET-only transport. No generic command facility."""
from dataclasses import dataclass
import json
import threading
import time
from urllib.parse import urlsplit
import httpx

UISP_PATHS=frozenset('/nms/api/v2.1/'+name for name in ('devices','sites','links','sectors'))
MIKROTIK_PATHS=frozenset(('/rest/system/identity','/rest/system/resource','/rest/interface',
                        '/rest/system/health','/rest/routing/ospf/neighbor'))
ERROR_CODES=frozenset(('disabled','configuration_invalid','endpoint_denied','operation_denied',
    'authentication_failed','rate_limited','unavailable','timeout','invalid_response','response_too_large',
    'pagination_invalid','limit_exceeded','unsupported','sync_failed','partial_sync','rf_failed','overlap_skipped'))


class IntegrationFailure(RuntimeError):
    def __init__(self,code):
        self.code=code if code in ERROR_CODES else 'sync_failed'
        super().__init__(self.code)


def secret(value):
    return value.get_secret_value() if hasattr(value,'get_secret_value') else str(value or '')


def origin(value):
    try:
        if not isinstance(value,str) or any(ord(c)<33 or ord(c)==127 for c in value) or '*' in value or '\\' in value:raise ValueError()
        parsed=urlsplit(value)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('','/') or '%' in parsed.netloc):
            raise ValueError()
        host=parsed.hostname.lower()
        if ':' in host:host='['+host+']'
        return f'https://{host}' + (f':{parsed.port}' if parsed.port and parsed.port!=443 else '')
    except (ValueError,TypeError):raise IntegrationFailure('endpoint_denied') from None


@dataclass(frozen=True)
class ReadPolicy:
    base_url: str
    allowed_origins: tuple[str,...]
    paths: frozenset[str]
    enabled: bool=False
    admin_approved: bool=False
    max_pages: int=50
    max_items: int=10000
    max_bytes: int=2_000_000
    retries: int=2
    interval: float=.2

    def validate(self):
        if not self.enabled or not self.admin_approved:raise IntegrationFailure('disabled')
        base=origin(self.base_url)
        if base not in {origin(value) for value in self.allowed_origins}:raise IntegrationFailure('endpoint_denied')
        if not self.paths or not self.paths.issubset(UISP_PATHS|MIKROTIK_PATHS):raise IntegrationFailure('operation_denied')
        if not (1<=self.max_pages<=100 and 1<=self.max_items<=10000 and 1<=self.max_bytes<=2_000_000
                and 0<=self.retries<=2 and .2<=self.interval<=5):raise IntegrationFailure('configuration_invalid')
        return base


class ReadClient:
    def __init__(self,policy,*,headers=None,auth=None,transport=None,sleep=time.sleep,clock=time.monotonic):
        self.policy=policy;self.base=policy.validate();self.sleep=sleep;self.clock=clock
        self.client=httpx.Client(verify=True,follow_redirects=False,
            timeout=httpx.Timeout(5,connect=3,pool=3,write=3),headers=headers,auth=auth,transport=transport)
        self.deadline=clock()+30
        self.last_request=None;self.lock=threading.Lock()

    def close(self):self.client.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def request(self,path,*,method='GET',params=None):
        from app.services.integration_interlock import disabled
        if disabled():raise IntegrationFailure('disabled')
        if method!='GET' or path not in self.policy.paths:raise IntegrationFailure('operation_denied')
        if params and (path not in UISP_PATHS or set(params)-{'page','limit'}):raise IntegrationFailure('operation_denied')
        if params and any(isinstance(v,bool) or not isinstance(v,int) or not 1<=v<=(self.policy.max_pages if k=='page' else 100) for k,v in params.items()):raise IntegrationFailure('operation_denied')
        for attempt in range(self.policy.retries+1):
            if self.clock()>=self.deadline:raise IntegrationFailure('timeout')
            request_deadline=min(self.deadline,self.clock()+10)
            with self.lock:
                current=self.clock()
                if self.last_request is not None:self.sleep(max(0,self.policy.interval-(current-self.last_request)))
                self.last_request=self.clock()
            try:
                if disabled():raise IntegrationFailure('disabled')
                with self.client.stream('GET',self.base+path,params=params) as response:
                    status=response.status_code
                    if status in (401,403):raise IntegrationFailure('authentication_failed')
                    if status==404:raise IntegrationFailure('unsupported')
                    if 300<=status<400:raise IntegrationFailure('endpoint_denied')
                    if status==429 or status>=500:
                        code='rate_limited' if status==429 else 'unavailable'
                        if attempt==self.policy.retries:raise IntegrationFailure(code)
                        try:delay=float(response.headers.get('Retry-After',str(.2*2**attempt)))
                        except ValueError:raise IntegrationFailure(code) from None
                        if not 0<=delay<=2:raise IntegrationFailure(code)
                        self.sleep(delay);continue
                    if status!=200:raise IntegrationFailure('unavailable')
                    body=bytearray()
                    for chunk in response.iter_bytes():
                        if self.clock()>=request_deadline:raise IntegrationFailure('timeout')
                        body.extend(chunk)
                        if len(body)>self.policy.max_bytes:raise IntegrationFailure('response_too_large')
                    try:return json.loads(body),dict(response.headers)
                    except (ValueError,UnicodeError,RecursionError):raise IntegrationFailure('invalid_response') from None
            except (httpx.TimeoutException,httpx.TransportError) as exc:
                if attempt==self.policy.retries:
                    raise IntegrationFailure('timeout' if isinstance(exc,httpx.TimeoutException) else 'unavailable') from None
                self.sleep(.2*2**attempt)
        raise IntegrationFailure('unavailable')

    def pages(self,path):
        result=[];page=1
        for _ in range(self.policy.max_pages):
            payload,headers=self.request(path,params={'page':page,'limit':100})
            if isinstance(payload,list):items=payload;next_page=headers.get('x-next-page')
            elif isinstance(payload,dict) and isinstance(payload.get('items'),list):
                items=payload['items'];next_page=payload.get('nextPage')
            else:raise IntegrationFailure('invalid_response')
            result.extend(items)
            if len(result)>self.policy.max_items:raise IntegrationFailure('limit_exceeded')
            if next_page in (None,'',False):return result
            if isinstance(next_page,bool) or str(next_page)!=str(page+1):raise IntegrationFailure('pagination_invalid')
            page+=1
        raise IntegrationFailure('limit_exceeded')
