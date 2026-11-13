"""Every HTTP operation in these tests uses an in-memory MockTransport."""
import httpx
import pytest
from app.integrations.read_only import ReadClient,ReadPolicy,UISP_PATHS,MIKROTIK_PATHS,IntegrationFailure
from app.core.config import Settings

BASE='https://uisp.example.invalid'


def policy(**overrides):
    return ReadPolicy(**(dict(base_url=BASE,allowed_origins=(BASE,),paths=UISP_PATHS,enabled=True,admin_approved=True)|overrides))


@pytest.mark.parametrize('url',['http://uisp.example.invalid','https://user:password@uisp.example.invalid',
    BASE+'/path',BASE+'?token=secret',BASE+'#fragment','https://other.example.invalid'])
def test_endpoint_allowlist_rejects_before_request(url):
    with pytest.raises(IntegrationFailure):ReadClient(policy(base_url=url),transport=httpx.MockTransport(lambda req:pytest.fail('network')))


@pytest.mark.parametrize('changes',[{'enabled':False},{'admin_approved':False}])
def test_disabled_and_unapproved_are_fail_closed(changes):
    with pytest.raises(IntegrationFailure):ReadClient(policy(**changes))


@pytest.mark.parametrize('method,path',[('POST','/rest/system/reboot'),('PATCH','/rest/interface'),
    ('DELETE','/nms/api/v2.1/devices'),('GET','/rest/ip/firewall/filter'),('GET','https://other.invalid')])
def test_mutations_commands_and_foreign_paths_never_reach_transport(method,path):
    with ReadClient(policy(),transport=httpx.MockTransport(lambda req:pytest.fail('request sent'))) as client:
        with pytest.raises(IntegrationFailure):client.request(path,method=method)


def test_paginated_reads_never_follow_server_urls():
    requests=[]
    def respond(req):
        requests.append(req)
        return httpx.Response(200,json={'items':[{'id':str(len(requests))}], 'nextPage':2 if len(requests)==1 else None})
    with ReadClient(policy(),transport=httpx.MockTransport(respond),sleep=lambda x:None) as client:
        assert len(client.pages('/nms/api/v2.1/devices'))==2
    assert all(r.method=='GET' and str(r.url).startswith(BASE+'/') for r in requests)
    with ReadClient(policy(),transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'items':[],'nextPage':'https://other.invalid'}))) as client:
        with pytest.raises(IntegrationFailure,match='pagination_invalid'):client.pages('/nms/api/v2.1/devices')


@pytest.mark.parametrize('status,code',[(401,'authentication_failed'),(403,'authentication_failed'),
    (302,'endpoint_denied'),(429,'rate_limited'),(503,'unavailable')])
def test_sanitized_error_codes_and_retry_budget(status,code):
    requests=[]
    def respond(req):
        requests.append(req);return httpx.Response(status,text='password=must-not-leak',headers={'Location':'https://other.invalid','Retry-After':'0'})
    with ReadClient(policy(),transport=httpx.MockTransport(respond),sleep=lambda x:None) as client:
        with pytest.raises(IntegrationFailure) as error:client.request('/nms/api/v2.1/devices')
    assert str(error.value)==code
    assert len(requests)==(3 if status in (429,503) else 1)
    assert 'must-not-leak' not in str(error.value)


def test_rate_limit_recovery_and_pacing():
    calls=[];delays=[]
    def respond(req):
        calls.append(req);return httpx.Response(429,headers={'Retry-After':'1'}) if len(calls)==1 else httpx.Response(200,json=[])
    with ReadClient(policy(),transport=httpx.MockTransport(respond),clock=lambda:0,sleep=delays.append) as client:
        assert client.request('/nms/api/v2.1/devices')[0]==[]
    assert 1 in delays and .2 in delays


@pytest.mark.parametrize('payload',[b'not-json',b'['*10000])
def test_malformed_payload_is_safe(payload):
    with ReadClient(policy(),transport=httpx.MockTransport(lambda req:httpx.Response(200,content=payload))) as client:
        with pytest.raises(IntegrationFailure,match='invalid_response'):client.request('/nms/api/v2.1/devices')


def test_response_page_item_and_retry_after_bounds():
    for value in [b'x'*100,'unbounded']:
        response=httpx.Response(200,content=value) if isinstance(value,bytes) else httpx.Response(429,headers={'Retry-After':'99999'})
        with ReadClient(policy(max_bytes=10),transport=httpx.MockTransport(lambda req:response)) as client:
            with pytest.raises(IntegrationFailure):client.request('/nms/api/v2.1/devices')
    with ReadClient(policy(max_items=1),transport=httpx.MockTransport(lambda req:httpx.Response(200,json=[{},{}]))) as client:
        with pytest.raises(IntegrationFailure,match='limit_exceeded'):client.pages('/nms/api/v2.1/devices')


def test_timeout_retries_are_bounded():
    calls=[]
    def respond(req):calls.append(req);raise httpx.ReadTimeout('token=must-not-leak',request=req)
    with ReadClient(policy(),transport=httpx.MockTransport(respond),sleep=lambda x:None) as client:
        with pytest.raises(IntegrationFailure,match='timeout'):client.pages('/nms/api/v2.1/devices')
    assert len(calls)==3


def test_whole_client_deadline_prevents_new_requests():
    time=[0]
    with ReadClient(policy(),transport=httpx.MockTransport(lambda req:pytest.fail('request sent')),clock=lambda:time[0]) as client:
        time[0]=31
        with pytest.raises(IntegrationFailure,match='timeout'):client.request('/nms/api/v2.1/devices')


def test_settings_secrets_are_redacted_and_activation_requires_admin():
    base=dict(_env_file=None,app_env='staging',secret_key='a'*40,admin_password='b'*40,database_url='postgresql+psycopg://test/test')
    cfg=Settings(**base,uisp_token='canary-token',mikrotik_password='canary-password')
    assert not cfg.uisp_enabled and not cfg.mikrotik_enabled
    assert 'canary-token' not in repr(cfg) and 'canary-password' not in repr(cfg)
    with pytest.raises(ValueError):Settings(**base,uisp_enabled=True)
    with pytest.raises(ValueError):Settings(**base,offline_mode=True,mikrotik_enabled=True)


def test_secret_file_permissions_and_no_symlinks(tmp_path):
    base=dict(_env_file=None,app_env='staging',secret_key='a'*40,admin_password='b'*40,database_url='postgresql+psycopg://test/test')
    path=tmp_path/'token';path.write_text('file-canary');path.chmod(0o600)
    cfg=Settings(**base,uisp_token_file=str(path))
    assert cfg.uisp_token.get_secret_value()=='file-canary' and 'file-canary' not in repr(cfg)
    assert 'uisp_token_file' not in cfg.model_dump()
    path.chmod(0o644)
    with pytest.raises(ValueError):Settings(**base,uisp_token_file=str(path))
    path.chmod(0o600);link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(ValueError):Settings(**base,uisp_token_file=str(link))


def test_tls_redirect_and_timeout_configuration(monkeypatch):
    calls=[]
    class FakeClient:
        def __init__(self,**kwargs):calls.append(kwargs)
        def close(self):pass
    monkeypatch.setattr('app.integrations.read_only.httpx.Client',FakeClient)
    with ReadClient(policy()):pass
    assert calls[0]['verify'] is True and calls[0]['follow_redirects'] is False
    assert calls[0]['timeout'].connect==3 and calls[0]['timeout'].read==5


def test_wildcard_control_char_origins_rejected_and_ipv6_is_exact():
    from app.integrations.read_only import origin
    for value in ['https://*.example.invalid','https://ui\nsp.example.invalid','https://uisp.example.invalid\\evil']:
        with pytest.raises(IntegrationFailure):origin(value)
    assert origin('https://[2001:db8::1]:8443')=='https://[2001:db8::1]:8443'


def test_startup_validation_does_not_echo_secret_values():
    base=dict(_env_file=None,app_env='staging',secret_key='a'*40,admin_password='b'*40,database_url='postgresql+psycopg://test/test')
    with pytest.raises(ValueError) as error:Settings(**base,uisp_enabled=True,uisp_token='canary-must-not-appear')
    assert 'canary-must-not-appear' not in str(error.value)
