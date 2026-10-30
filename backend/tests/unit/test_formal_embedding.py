from dataclasses import replace
import json
from threading import Event, Thread

import httpx
import numpy as np
import pytest

from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.retrieval.embedding import (
    EmbeddingIdentity, LocalEmbedding, MAX_RESPONSE, validate_vectors)

IDENTITY = EmbeddingIdentity('bge-m3:567m', 'a' * 64)
VECTOR = [1.0] + [0.0] * 1023


def provider(handler=None):
    def respond(request):
        if handler:
            override = handler(request)
            if override is not None:return override
        path = request.url.path
        if path == '/api/tags':return httpx.Response(200, json={'models': [{'name': IDENTITY.model, 'digest': IDENTITY.digest}]})
        if path == '/api/version':return httpx.Response(200, json={'version': '0.12.0'})
        if path == '/api/show':return httpx.Response(200, json={'capabilities': ['embedding']})
        if path == '/api/embed':
            payload = json.loads(request.content)
            assert payload['truncate'] is False and payload['options']['num_gpu'] == 0
            assert payload['options']['num_thread'] == 4
            return httpx.Response(200, json={'model': IDENTITY.model, 'embeddings': [VECTOR] * len(payload['input'])})
        raise AssertionError(path)
    return LocalEmbedding(IDENTITY, transport=httpx.MockTransport(respond))


def test_identity_inspection_and_normalized_float32_no_query_prefix():
    sent=[]
    def capture(request):
        if request.url.path=='/api/embed':sent.extend(json.loads(request.content)['input'])
    service=provider(capture)
    assert service.inspect()['fingerprint']==IDENTITY.fingerprint
    vectors,timing=service.encode(['轴承方法', '# 原文\n<script>text</script>'])
    assert sent==['轴承方法', '# 原文\n<script>text</script>']
    assert vectors.shape==(2,1024) and vectors.dtype==np.float32
    assert np.allclose(np.linalg.norm(vectors,axis=1),1)
    assert timing['identity_fingerprint']==IDENTITY.fingerprint


@pytest.mark.parametrize('field,value', [('digest','bad'),('model','bge-m3:latest'),('dimensions',768),
    ('input_policy','qwen-prefix'),('normalization','none'),('metric','l2')])
def test_identity_contract_rejects_incompatible_configuration(field,value):
    with pytest.raises(ValueError):replace(IDENTITY,**{field:value})


@pytest.mark.parametrize('url',['https://127.0.0.1:11434','http://example.com','http://127.0.0.1.evil',
    'http://user:secret@localhost','http://localhost/api','http://localhost?token=secret'])
def test_loopback_only_no_credentials_or_paths(url):
    with pytest.raises(ValueError):LocalEmbedding(IDENTITY,base_url=url)


@pytest.mark.parametrize('texts',[[],[' '],['x'*2001],['x']*9,['x\x00'],[42],'text'])
def test_invalid_input_never_calls_model(texts):
    def forbidden(request):raise AssertionError('network call for invalid input')
    with pytest.raises(MeasurementError,match='Pinned local') as caught:provider(forbidden).encode(texts)
    assert caught.value.code=='EMBEDDING_INPUT_INVALID'


@pytest.mark.parametrize('vectors',[None,[],[VECTOR,VECTOR],[[1.0]],[[0.0]*1024],
    [[True]+[0.0]*1023],[[float('nan')]+[0.0]*1023],[[float('inf')]+[0.0]*1023],
    [[1e300]+[0.0]*1023],[[10**1000]+[0.0]*1023], [['1']+[0.0]*1023]])
def test_invalid_vectors_rejected(vectors):
    with pytest.raises(MeasurementError) as caught:validate_vectors(vectors,1)
    assert caught.value.code=='EMBEDDING_OUTPUT_INVALID'


@pytest.mark.parametrize('when',['before','after'])
def test_changed_model_tag_fails_closed(when):
    seen=0;embedded=0
    def mutate(request):
        nonlocal seen,embedded
        if request.url.path=='/api/embed':embedded+=1
        if request.url.path=='/api/tags':
            seen+=1
            if seen==(1 if when=='before' else 2):
                return httpx.Response(200,json={'models':[{'name':IDENTITY.model,'digest':'b'*64}]})
    with pytest.raises(MeasurementError) as caught:provider(mutate).encode(['query'])
    assert caught.value.code=='EMBEDDING_IDENTITY_MISMATCH'
    assert embedded==(0 if when=='before' else 1)


@pytest.mark.parametrize('response,code',[(httpx.Response(500,text='private server input'), 'EMBEDDING_SERVICE_UNAVAILABLE'),
    (httpx.Response(302,headers={'location':'http://example.invalid'}),'EMBEDDING_SERVICE_UNAVAILABLE'),
    (httpx.Response(200,content=b'x'*(MAX_RESPONSE+1)),'EMBEDDING_RESPONSE_TOO_LARGE'),
    (httpx.Response(200,content=b'not json'),'EMBEDDING_SERVICE_UNAVAILABLE'),
    (httpx.Response(200,json=[]),'EMBEDDING_OUTPUT_INVALID'),
    (httpx.Response(200,json={'model':'other','embeddings':[VECTOR]}),'EMBEDDING_IDENTITY_MISMATCH')])
def test_untrusted_response_bounded_and_redacted(response,code):
    def corrupt(request):
        if request.url.path=='/api/embed':return response
    with pytest.raises(MeasurementError) as caught:provider(corrupt).encode(['secret query'])
    assert caught.value.code==code
    assert 'secret' not in str(caught.value) and 'private' not in str(caught.value)
    assert provider().encode(['healthy'])[0].shape==(1,1024)  # slot is released after failure


def test_network_timeout_is_controlled_and_slot_reusable():
    def timeout(request):raise httpx.ReadTimeout('secret URL')
    with pytest.raises(MeasurementError) as caught:provider(timeout).encode(['query'])
    assert caught.value.code=='EMBEDDING_SERVICE_UNAVAILABLE'
    assert provider().encode(['query'])[0].shape==(1,1024)
    def nested(request):return httpx.Response(200,content=b'['*2000+b'0'+b']'*2000)
    with pytest.raises(MeasurementError) as malformed:provider(nested).encode(['query'])
    assert malformed.value.code=='EMBEDDING_SERVICE_UNAVAILABLE'
    assert provider().encode(['query'])[0].shape==(1,1024)


def test_busy_request_rejected_without_waiting_or_second_model_call():
    entered=Event();release=Event();errors=[]
    def block(request):
        if request.url.path=='/api/embed':entered.set();assert release.wait(5)
    def first():
        try:provider(block).encode(['query'])
        except Exception as error:errors.append(error)
    thread=Thread(target=first);thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(MeasurementError) as caught:provider().encode(['second'])
        assert caught.value.code=='EMBEDDING_BUSY' and caught.value.status==429
    finally:release.set();thread.join(5)
    assert not thread.is_alive() and not errors


def test_deadline_checked_after_slow_response_and_releases_slot(monkeypatch):
    from vibradiag.infrastructure.retrieval import embedding
    clock=[0.0]
    monkeypatch.setattr(embedding,'monotonic',lambda:clock[0])
    def delayed(request):
        if request.url.path=='/api/embed':clock[0]=61.0
    with pytest.raises(MeasurementError) as caught:provider(delayed).encode(['query'])
    assert caught.value.code=='EMBEDDING_DEADLINE_EXCEEDED'
    clock[0]=0.0
    assert provider().encode(['query'])[0].shape==(1,1024)


def test_inspection_rejects_non_embedding_capability():
    def no_embedding(request):
        if request.url.path=='/api/show':return httpx.Response(200,json={'capabilities':['completion']})
    with pytest.raises(MeasurementError) as caught:provider(no_embedding).inspect()
    assert caught.value.code=='EMBEDDING_CAPABILITY_MISMATCH'
