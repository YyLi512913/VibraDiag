import json
import httpx
import pytest
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.llm.formal_gateway import Gateway,configuration,MAX_RESPONSE


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    monkeypatch.setenv('VIBRADIAG_ALLOW_EXTERNAL_LLM','true');monkeypatch.setenv('DEEPSEEK_API_KEY','unit-test-only')


def gateway(provider='deepseek',override=None):
    cfg=configuration(provider);requests=[]
    def handle(request):
        requests.append(request)
        if override:
            result=override(request)
            if result is not None:return result
        if request.url.path=='/models':return httpx.Response(200,json=dict(data=[dict(id=cfg.model)]))
        if request.url.path=='/api/tags':return httpx.Response(200,json=dict(models=[dict(name=cfg.model,digest=cfg.digest)]))
        if request.url.path=='/api/version':return httpx.Response(200,json=dict(version='0.12.0'))
        payload=json.loads(request.content);assert payload['stream'] is False and 'tools' not in payload
        assert payload['messages'][0]['role']=='system' and payload['messages'][1]['role']=='user'
        if provider=='deepseek':
            assert request.headers['authorization']=='Bearer unit-test-only'
            assert payload['response_format']=={'type':'json_object'} and payload['max_tokens']==512
            return httpx.Response(200,json=dict(model=cfg.model,choices=[dict(message=dict(role='assistant',content='{"ok":true}'),finish_reason='stop')],usage=dict(prompt_tokens=10,completion_tokens=4,total_tokens=14)))
        assert 'authorization' not in request.headers and payload['options']['num_gpu']==0 and payload['think'] is False
        return httpx.Response(200,json=dict(model=cfg.model,done=True,done_reason='stop',message=dict(role='assistant',content='{"ok":true}'),prompt_eval_count=10,eval_count=4))
    return Gateway(cfg,transport=httpx.MockTransport(handle)),requests


@pytest.mark.parametrize('provider',['deepseek','ollama'])
def test_explicit_provider_envelopes_and_safe_identity(provider):
    client,requests=gateway(provider);raw,trace=client.invoke('decision',dict(question='fixture'),dict(type='object'))
    assert raw=='{"ok":true}' and trace['provider']==provider
    assert trace['requested_model']==configuration(provider).model and trace['usage']['completion_tokens']==4
    assert not trace['provider_weight_version_attested']
    assert all(r.url.host==('api.deepseek.com' if provider=='deepseek' else '127.0.0.1') for r in requests)


def test_external_disable_no_request_and_no_fallback(monkeypatch):
    client,requests=gateway();monkeypatch.setenv('VIBRADIAG_ALLOW_EXTERNAL_LLM','false')
    with pytest.raises(MeasurementError) as caught:client.invoke('decision',{},dict(type='object'))
    assert caught.value.code=='MODEL_EXTERNAL_DISABLED' and not requests


@pytest.mark.parametrize('status,code',[(401,'MODEL_AUTH_FAILED'),(402,'MODEL_BALANCE_UNAVAILABLE'),(403,'MODEL_ACCESS_DENIED'),(429,'MODEL_RATE_LIMITED'),(500,'MODEL_PROVIDER_UNAVAILABLE'),(302,'MODEL_PROVIDER_UNAVAILABLE')])
def test_provider_errors_redacted_no_redirect_or_fallback(status,code):
    client,requests=gateway(override=lambda r:httpx.Response(status,text='private provider detail',headers={'Location':'https://example.invalid'}))
    with pytest.raises(MeasurementError) as caught:client.invoke('decision',{},dict(type='object'))
    assert caught.value.code==code and 'private' not in str(caught.value) and len(requests)==1


@pytest.mark.parametrize('body',[b'x'*(MAX_RESPONSE+1),b'not json',b'[]',b'['*2000+b'0'+b']'*2000])
def test_bad_or_oversize_transport_json_rejected(body):
    client,_=gateway(override=lambda r:httpx.Response(200,content=body))
    with pytest.raises(MeasurementError):client.invoke('decision',{},dict(type='object'))
    assert gateway()[0].invoke('decision',{},dict(type='object'))[0]


@pytest.mark.parametrize('change',[{'finish_reason':'length'},{'message':{'role':'assistant','content':' '}},
    {'message':{'role':'assistant','content':'{}','tool_calls':[{'function':'forbidden'}]}},
    {'message':{'role':'tool','content':'{}'}}])
def test_truncated_empty_and_tool_output_rejected(change):
    def broken(request):
        if request.url.path=='/chat/completions':return httpx.Response(200,json=dict(model='deepseek-flash',choices=[dict(message=dict(role='assistant',content='{}'),finish_reason='stop')|change]))
    with pytest.raises(MeasurementError):gateway(override=broken)[0].invoke('decision',{},dict(type='object'))


def test_local_digest_mismatch_does_not_infer():
    def wrong(request):
        if request.url.path=='/api/tags':return httpx.Response(200,json=dict(models=[dict(name='qwen3.5:2b',digest='0'*64)]))
    client,requests=gateway('ollama',wrong)
    with pytest.raises(MeasurementError) as caught:client.invoke('decision',{},dict(type='object'))
    assert caught.value.code=='MODEL_IDENTITY_MISMATCH' and len(requests)==1


def test_guard_cancellation_and_capacity_are_not_swallowed():
    client,_=gateway()
    class Cancelled(Exception):pass
    def stop():raise Cancelled()
    with pytest.raises(Cancelled):client.invoke('decision',{},dict(type='object'),guard=stop)
    from vibradiag.infrastructure.llm.formal_gateway import SLOT
    SLOT.acquire()
    try:
        with pytest.raises(MeasurementError) as caught:client.invoke('decision',{},dict(type='object'))
        assert caught.value.code=='MODEL_CAPACITY'
    finally:SLOT.release()


def test_network_failure_and_context_cap_release_slot():
    def timeout(request):raise httpx.ReadTimeout('private URL detail')
    with pytest.raises(MeasurementError):gateway(override=timeout)[0].invoke('decision',{},dict(type='object'))
    client,requests=gateway()
    with pytest.raises(MeasurementError) as caught:client.invoke('decision',dict(question='x'*25000),dict(type='object'))
    assert caught.value.code=='MODEL_CONTEXT_TOO_LARGE' and not requests
    assert client.invoke('decision',{},dict(type='object'))[0]


def test_local_byte_budget_rejects_before_inference():
    client,requests=gateway('ollama')
    with pytest.raises(MeasurementError) as caught:client.invoke('decision',dict(question='x'*6001),dict(type='object'))
    assert caught.value.code=='MODEL_LOCAL_CONTEXT_TOO_LARGE' and not requests


def test_local_tag_changed_after_inference_rejects_response():
    count=0
    def changed(request):
        nonlocal count
        if request.url.path=='/api/tags':
            count+=1
            if count==2:return httpx.Response(200,json=dict(models=[dict(name='qwen3.5:2b',digest='0'*64)]))
    with pytest.raises(MeasurementError) as caught:gateway('ollama',changed)[0].invoke('decision',{},dict(type='object'))
    assert caught.value.code=='MODEL_IDENTITY_MISMATCH'


def test_external_revoked_between_metadata_and_generation(monkeypatch):
    def revoke(request):
        if request.url.path=='/models':monkeypatch.setenv('VIBRADIAG_ALLOW_EXTERNAL_LLM','false')
    client,requests=gateway(override=revoke)
    with pytest.raises(MeasurementError) as caught:client.invoke('decision',{},dict(type='object'))
    assert caught.value.code=='MODEL_EXTERNAL_DISABLED' and len(requests)==1


def test_response_after_deadline_is_not_success(monkeypatch):
    from vibradiag.infrastructure.llm import formal_gateway
    clock=[0.];monkeypatch.setattr(formal_gateway,'monotonic',lambda:clock[0])
    def delayed(request):
        if request.url.path=='/chat/completions':clock[0]=61.
    with pytest.raises(MeasurementError) as caught:gateway(override=delayed)[0].invoke('decision',{},dict(type='object'))
    assert caught.value.code=='MODEL_DEADLINE'
