import os
import time
import httpx

BASE_URL = os.environ.get('VIBRADIAG_OLLAMA_URL', 'http://127.0.0.1:11434')

def call(path: str, payload: dict | None = None, timeout: float = 120) -> dict:
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.get(BASE_URL + path) if payload is None else client.post(BASE_URL + path, json=payload)
        response.raise_for_status()
        return response.json()

def metadata(model: str) -> dict:
    tags = call('/api/tags')['models']
    tag = next((item for item in tags if item['name'] == model), None)
    if tag is None:
        raise ValueError(f'本地模型不存在：{model}')
    shown = call('/api/show', {'model': model})
    return {'model': model, 'digest': tag['digest'], 'size_bytes': tag['size'],
            'details': shown.get('details'), 'capabilities': shown.get('capabilities')}

def embed(model: str, texts: list[str]) -> tuple[list[list[float]], dict]:
    started = time.perf_counter()
    result = call('/api/embed', {'model': model, 'input': texts, 'truncate': False,
                  'keep_alive': '5m', 'options': {'num_gpu': 0, 'num_thread': 8}})
    return result['embeddings'], {'wall_seconds': time.perf_counter()-started,
        'load_seconds': result.get('load_duration', 0)/1e9,
        'prompt_tokens': result.get('prompt_eval_count')}

def chat(model: str, messages: list[dict], tools: list[dict] | None = None, json_output: bool = False, timeout: float = 120, schema: dict | None = None) -> tuple[dict, dict]:
    payload = {'model': model, 'messages': messages, 'stream': False, 'think': False,
               'keep_alive': '5m', 'options': {'temperature': 0, 'seed': 42,
               'num_ctx': 4096, 'num_predict': 512}}
    if tools:
        payload['tools'] = tools
    if schema is not None:
        payload['format'] = schema
    elif json_output:
        payload['format'] = 'json'
    started = time.perf_counter()
    result = call('/api/chat', payload,timeout=timeout)
    return result['message'], {'wall_seconds': time.perf_counter()-started,
        'load_seconds': result.get('load_duration', 0)/1e9,
        'input_tokens': result.get('prompt_eval_count'), 'output_tokens': result.get('eval_count'),
        'done_reason': result.get('done_reason')}
