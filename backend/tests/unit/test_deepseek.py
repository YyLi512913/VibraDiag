import json
import httpx
import pytest
from vibradiag.infrastructure.llm import deepseek
from vibradiag.agents import graph as agent

def test_tool_wire_ids_preserved_and_arguments_serialized_without_mutation():
    messages=[{'role':'assistant','content':'','tool_calls':[{'id':'call-1','type':'function','function':{'name':'inspect_spectrum','arguments':{}}}]},
              {'role':'tool','tool_name':'inspect_spectrum','tool_call_id':'call-1','content':'{}'}]
    wire=deepseek._messages(messages)
    assert wire[0]['tool_calls'][0]['function']['arguments']=='{}'
    assert wire[1]['tool_call_id']=='call-1'
    assert 'tool_name' not in wire[1]
    assert messages[0]['tool_calls'][0]['function']['arguments']=={}

def test_missing_tool_id_rejected():
    with pytest.raises(ValueError):
        deepseek._messages([{'role':'tool','content':'{}'}])

def test_external_calls_disabled_before_network(monkeypatch):
    monkeypatch.setattr(deepseek,'external_enabled',lambda:False)
    with pytest.raises(RuntimeError,match='未启用'):
        deepseek.request('/models')

def test_api_error_does_not_echo_body_or_secret(monkeypatch):
    monkeypatch.setattr(deepseek,'external_enabled',lambda:True)
    monkeypatch.setattr(deepseek,'setting',lambda name:'unit-test-placeholder')
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,*args,**kwargs):
            return httpx.Response(401,text='unit-test-sensitive-body')
    monkeypatch.setattr(deepseek.httpx,'Client',Client)
    with pytest.raises(RuntimeError) as caught:
        deepseek.request('/models')
    assert 'unit-test-placeholder' not in str(caught.value)
    assert 'unit-test-sensitive-body' not in str(caught.value)
    assert '401' in str(caught.value)

def test_executor_carries_original_call_id():
    state={'pending':[{'id':'call-7','function':{'name':'inspect_spectrum','arguments':{}}}],
           'messages':[],'evidence':{},'trace':[],'seen':[],
           'analysis':{'features':{'rms':1},'peaks':[]}}
    result=agent.execute(state)
    assert result['messages'][0]['tool_call_id']=='call-7'

def test_json_mode_uses_local_validation_and_hides_credentials(monkeypatch):
    seen={}
    def fake_request(path,payload,timeout):
        seen.update(payload)
        return {'id':'fake-id','model':'fake-model','choices':[{'message':{'role':'assistant','content':'{}'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}
    monkeypatch.setattr(deepseek,'request',fake_request)
    message,metrics=deepseek.chat('test-model',[{'role':'user','content':'返回JSON'}],schema={'type':'object'})
    assert seen['response_format']=={'type':'json_object'}
    assert metrics['structured_output']=='json_object_with_local_schema_validation'
    assert metrics['total_tokens']==12
    assert 'Authorization' not in json.dumps(seen)
