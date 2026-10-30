import json
import copy
import time
import httpx
from vibradiag.core.config import setting,external_enabled

BASE_URL='https://api.deepseek.com'

def request(path:str,payload:dict|None=None,timeout:float=60)->dict:
    if not external_enabled():
        raise RuntimeError('外部模型调用未启用；请检查本地配置')
    key=setting('DEEPSEEK_API_KEY')
    if not key:
        raise RuntimeError('未配置DeepSeek API密钥')
    try:
        with httpx.Client(timeout=timeout,trust_env=False) as client:
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
            response=client.get(BASE_URL+path,headers=headers) if payload is None else client.post(BASE_URL+path,headers=headers,json=payload)
    except httpx.TimeoutException:
        raise RuntimeError('DeepSeek请求超时') from None
    except httpx.HTTPError:
        raise RuntimeError('DeepSeek网络连接失败') from None
    if response.status_code!=200:
        labels={401:'认证失败，请检查本地密钥',402:'余额不足',403:'访问被拒绝',429:'调用限流'}
        # Never echo response bodies, request headers or credentials into traces.
        raise RuntimeError(f'DeepSeek HTTP {response.status_code}：'+labels.get(response.status_code,'请求失败，请检查接口配置'))
    return response.json()

def models():
    result=request('/models')
    return [{'id':m['id'],'name':m.get('name'),'owned_by':m.get('owned_by')} for m in result['data']]

def _messages(messages):
    converted=[]
    for message in messages:
        item=copy.deepcopy({k:v for k,v in message.items() if k in ('role','content','tool_calls','tool_call_id','name')})
        if item['role']=='assistant' and item.get('tool_calls'):
            item['content']=item.get('content') or ''
            for call in item['tool_calls']:
                if not isinstance(call['function']['arguments'],str):
                    call['function']['arguments']=json.dumps(call['function']['arguments'],ensure_ascii=False)
        if item['role']=='tool' and not item.get('tool_call_id'):
            raise ValueError('DeepSeek工具结果缺少原始tool_call_id')
        converted.append(item)
    return converted

def chat(model,messages,tools=None,json_output=False,timeout=120,schema=None):
    payload={'model':model,'messages':_messages(messages),'stream':False,'max_tokens':1024,
             'temperature':0,'thinking':{'type':'disabled'}}
    if tools:
        payload['tools']=tools
    if schema is not None or json_output:
        payload['response_format']={'type':'json_object'}
        # Standard endpoint provides JSON mode, not Ollama's native schema constraint.
        payload['messages'].append({'role':'user','content':'输出符合以下JSON schema的对象，不要输出schema本身：'+json.dumps(schema or {},ensure_ascii=False)})
    started=time.perf_counter()
    result=request('/chat/completions',payload,timeout)
    choice=result['choices'][0];usage=result.get('usage',{})
    message={k:v for k,v in choice['message'].items() if k in ('role','content','tool_calls') and v is not None}
    return message,{'provider':'deepseek','requested_model':model,'served_model':result.get('model'),
        'response_id':result.get('id'),'wall_seconds':time.perf_counter()-started,
        'input_tokens':usage.get('prompt_tokens'),'output_tokens':usage.get('completion_tokens'),
        'total_tokens':usage.get('total_tokens'),'done_reason':choice.get('finish_reason'),
        'structured_output':'json_object_with_local_schema_validation' if schema else None}
