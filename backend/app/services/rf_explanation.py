"""Optional local explanation interface. No external AI client or endpoint exists.

A future Ollama adapter can implement Provider. Only an ordering of existing
checks is accepted; free-form model prose is never published as diagnostic fact.
"""
from typing import Protocol
from pydantic import BaseModel,ConfigDict,Field

class Provider(Protocol):
    def plan(self, evidence: dict) -> dict: ...

class Plan(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    rule: str
    evidence_ids: list[str]=Field(max_length=512)
    check_ids: list[str]=Field(max_length=10)


def explain(evidence,*,enabled=False,provider: Provider|None=None):
    fallback={'status':'disabled','technician_checks':evidence['technician_checks'],'freeform_model_text':False}
    if not enabled:return fallback
    if provider is None:return fallback|{'status':'unavailable'}
    # No names, IPs, credentials, raw payloads, notes, causes or metadata leave
    # this boundary. Facts are constructed by trusted numeric/enum adapters.
    facts=[{k:f[k] for k in ('id','metric','value','at','source') if k in f} for f in evidence['facts']]
    ids=[f['id'] for f in facts]
    try:
        plan=Plan.model_validate(provider.plan({'rule':evidence['rule'],'facts':facts,
            'allowed_check_ids':evidence['check_ids']}))
        if plan.rule!=evidence['rule'] or sorted(plan.evidence_ids)!=sorted(ids):raise ValueError()
        if sorted(plan.check_ids)!=sorted(evidence['check_ids']):raise ValueError()
        checks={c['id']:c for c in evidence['technician_checks']}
        return fallback|{'status':'accepted','technician_checks':[checks[key] for key in plan.check_ids]}
    except Exception:
        # Do not log exception strings, prompts or raw provider responses.
        return fallback|{'status':'rejected_or_failed'}


class LocalOllamaProvider:
    """Bounded loopback-only POST; no external hosts, redirects, proxies or pulls.

    A local, pre-provisioned model must support structured JSON. Tests inject
    MockTransport; no model server is contacted during development/CI.
    """
    def __init__(self,model,*,transport=None):
        import re
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,79}',model):raise ValueError('Invalid local model identifier')
        self.model=model;self.transport=transport

    def plan(self,evidence):
        import json,httpx,time
        deadline=time.monotonic()+3
        with httpx.Client(base_url='http://127.0.0.1:11434',transport=self.transport,
                trust_env=False,follow_redirects=False,timeout=httpx.Timeout(2,connect=.5),limits=httpx.Limits(max_connections=1)) as client:
            with client.stream('POST','/api/generate',json={'model':self.model,'stream':False,
                    'format':Plan.model_json_schema(),'system':'Return only a JSON plan selecting all supplied evidence IDs and reordering all allowed check IDs. Do not diagnose or propose actions.',
                    'prompt':json.dumps(evidence,allow_nan=False),'options':{'temperature':0,'num_predict':512}}) as response:
                response.raise_for_status();body=bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    body.extend(chunk)
                    if time.monotonic()>deadline:raise TimeoutError('Local provider time budget exceeded')
                    if len(body)>32768:raise ValueError('Provider response too large')
                envelope=json.loads(body)
                return json.loads(envelope['response'])
