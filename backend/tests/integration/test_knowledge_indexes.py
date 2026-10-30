from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from uuid import UUID,uuid4
import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services import knowledge_index_service as service
from vibradiag.services import knowledge_document_service as documents
from test_knowledge_documents import knowledge,created,grant,publish,metadata,action
from test_tool_runs import login

PREFIX='/api/v1/knowledge-indexes'


@pytest.fixture
def model(monkeypatch):
    class Fake:
        calls=[];error=None;hook=None;runtime='fixture-embedding-v1'
        def inspect(self):return dict(ollama_version=self.runtime)
        def encode(self,texts):
            self.calls.append(list(texts))
            if self.hook:self.hook(texts)
            if self.error:raise self.error
            vectors=np.zeros((len(texts),1024),dtype=np.float32)
            for i,t in enumerate(texts):vectors[i,1 if 'unrelated' in t else 0]=1
            return vectors,dict(fixture_model=True)
    fake=Fake();monkeypatch.setattr(service,'provider',lambda *args,**kwargs:fake)
    return fake


def live(data,**kwargs):
    assert grant(data).status_code==201
    return publish(data,created(data,**kwargs))


def build(data,revision,version=0,key=None,**extra):
    return data['client'].post(PREFIX+'/revisions/'+revision['id']+'/build',json=dict(
        expected_version=revision['row_version'],expected_document_version=revision['document_version'],
        expected_index_version=version,reason='Synthetic index acceptance',**extra),headers={'Idempotency-Key':key or str(uuid4())})


def built(data,revision,**kwargs):
    response=build(data,revision,**kwargs);assert response.status_code==201,response.text
    assert response.json()['status']=='ready',response.text
    return response.json()


def search(data,client=None,**extra):
    return (client or data['client']).post(PREFIX+'/search',json=dict(query='bearing conditions',**extra))


def test_build_requires_explicit_reviewer_and_active_publication(knowledge,model):
    revision=created(knowledge)
    assert build(knowledge,revision).status_code==403
    assert grant(knowledge).status_code==201
    assert build(knowledge,revision).status_code==409
    assert not model.calls


def test_ready_generation_real_postgres_vectors_and_developer_exclusion(knowledge,model,monkeypatch):
    revision=live(knowledge);run=built(knowledge,revision)
    assert run['vector_count']==2 and run['index_version']==1 and run['is_active']
    assert len(model.calls)==1
    assert search(knowledge).json()['hits']==[]
    result=search(knowledge,include_developer=True).json()
    assert len(result['hits'])==2 and result['retrieval_mode']=='developer_verification'
    assert all(not h['formal_retrieval_available'] and h['similarity']==pytest.approx(1) for h in result['hits'])
    with transaction() as c:
        assert c.execute(text('SELECT count(*) FROM knowledge_chunk_vectors WHERE run_id=:id AND vibradiag_extensions.vector_dims(embedding)=1024'),dict(id=UUID(run['id']))).scalar_one()==2
    from vibradiag.infrastructure.retrieval import embedding
    def invalid_config():raise ValueError('invalid local model configuration')
    monkeypatch.setattr(embedding,'configured_identity',invalid_config)
    source=knowledge['client'].get('/api/v1/knowledge-documents/chunks/'+result['hits'][0]['id'])
    assert source.status_code==200 and source.json()['vector_index']=='identity_mismatch'
    assert source.json()['source_sha256']==revision['source_sha256']


def test_declared_internal_note_normal_reader_and_scoped_receipt(knowledge,model):
    body=metadata(knowledge);body['content_origin']='internal_note'
    revision=live(knowledge,body=body);run=built(knowledge,revision)
    with login(knowledge,'owner') as reader:
        response=search(knowledge,client=reader);assert response.status_code==200,response.text
        result=response.json();assert len(result['hits'])==2 and result['model_called']
        assert all(h['formal_retrieval_available'] and not h['relevance_qualified'] and not h['diagnostic_probability'] for h in result['hits'])
        receipt=reader.get(PREFIX+'/retrievals/'+result['id']).json()
        assert receipt['historical_receipt_only'] and len(receipt['matches'])==2
        assert knowledge['client'].get(PREFIX+'/retrievals/'+result['id']).status_code==404
        assert search(knowledge,client=reader,include_developer=True).status_code==403
        assert reader.get('/api/v1/knowledge-documents/revisions/'+revision['id']).json()['vector_index']=='ready'
    assert run['identity']['dimensions']==1024


def test_sql_cosine_ranking_and_authorized_content_deduplication(knowledge,model):
    first=live(knowledge,raw=b'# Shared\nidentical content\n# A\nunrelated\n');built(knowledge,first)
    second=publish(knowledge,created(knowledge,raw=b'# Shared\nidentical content\n# B\nother bearing content\n'));built(knowledge,second)
    result=search(knowledge,include_developer=True).json()
    assert len(result['hits'])==3
    assert len({h['text_sha256'] for h in result['hits']})==3
    assert result['hits'][-1]['similarity']==pytest.approx(0)
    assert result['hits'][0]['similarity']==pytest.approx(1)


def test_failed_rebuild_preserves_active_and_same_key_never_reexecutes(knowledge,model):
    revision=live(knowledge);original=built(knowledge,revision);key=str(uuid4())
    model.error=MeasurementError('EMBEDDING_SERVICE_UNAVAILABLE','safe fixture',status=503)
    failed=build(knowledge,revision,version=1,key=key).json()
    assert failed['status']=='failed' and failed['active_index_id']==original['id']
    calls=len(model.calls);replay=build(knowledge,revision,version=1,key=key)
    assert replay.status_code==200 and replay.json()['id']==failed['id'] and len(model.calls)==calls
    state=knowledge['client'].get('/api/v1/knowledge-documents/revisions/'+revision['id']).json()
    assert state['vector_index']=='ready' and state['latest_index_status']=='failed'
    model.error=None
    assert len(search(knowledge,include_developer=True).json()['hits'])==2
    rebuilt=built(knowledge,revision,version=1)
    assert rebuilt['index_version']==2 and rebuilt['active_index_id']!=original['id']
    assert not knowledge['client'].get(PREFIX+'/runs/'+original['id']).json()['is_active']


def test_build_cas_unknown_fields_and_key_conflict(knowledge,model):
    revision=live(knowledge);key=str(uuid4());built(knowledge,revision,key=key)
    assert build(knowledge,revision).status_code==409
    assert build(knowledge,revision,version=1,key=key).status_code==409
    assert build(knowledge,revision,version=1,identity='user override').status_code==422
    assert search(knowledge,limit=6).status_code==422
    assert search(knowledge,include_developer='true').status_code==422


def test_concurrent_build_capacity_and_processing_replay(knowledge,model):
    revision=live(knowledge);entered=Event();release=Event();key=str(uuid4())
    def pause(texts):entered.set();assert release.wait(10)
    model.hook=pause
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(build,knowledge,revision,key=key)
        try:
            assert entered.wait(10)
            same=build(knowledge,revision,key=key)
            assert same.status_code==200 and same.json()['status']=='processing'
            assert build(knowledge,revision).status_code==429
        finally:release.set()
        result=future.result(timeout=10)
    assert result.status_code==201 and result.json()['status']=='ready',result.text


def test_withdrawal_during_embedding_discards_generation(knowledge,model):
    revision=live(knowledge)
    def withdraw(texts):assert action(knowledge,revision,'withdraw').status_code==200
    model.hook=withdraw
    result=build(knowledge,revision);assert result.status_code==201,result.text
    assert result.json()['status']=='failed' and result.json()['active_index_id'] is None
    with transaction() as c:assert c.execute(text('SELECT count(*) FROM knowledge_chunk_vectors WHERE run_id=:id'),dict(id=UUID(result.json()['id']))).scalar_one()==0


def test_review_revoked_during_embedding_preserves_old_generation(knowledge,model):
    revision=live(knowledge);old=built(knowledge,revision)
    def revoke(texts):
        with transaction() as c:c.execute(text('UPDATE knowledge_review_grants SET enabled=false,row_version=row_version+1 WHERE user_id=:peer'),knowledge)
    model.hook=revoke
    response=build(knowledge,revision,version=1);assert response.status_code==201,response.text
    assert response.json()['status']=='failed' and response.json()['active_index_id']==old['id']
    assert response.json()['error_code']=='KNOWLEDGE_PERMISSION_REQUIRED'


def test_asset_org_equipment_and_component_acl_before_model_and_ranking(knowledge,model):
    body=metadata(knowledge);body.update(visibility='asset',asset_id=str(knowledge['asset']))
    revision=live(knowledge,body=body);built(knowledge,revision);model.calls.clear()
    assert search(knowledge,include_developer=True).json()['hits']==[] and not model.calls
    assert len(search(knowledge,include_developer=True,asset_id=str(knowledge['asset'])).json()['hits'])==2
    model.calls.clear()
    assert search(knowledge,include_developer=True,asset_id=str(knowledge['asset']),equipment='pump').status_code==422
    assert search(knowledge,include_developer=True,asset_id=str(knowledge['asset']),component='rotating_assembly').json()['hits']==[]
    with login(knowledge,'outsider','other_org') as outsider:
        assert search(knowledge,client=outsider).json()['hits']==[]
        assert outsider.get(PREFIX+'/runs/'+built_id(knowledge,revision)).status_code==404
    assert not model.calls


def built_id(data,revision):return data['client'].get(PREFIX+'/revisions/'+revision['id']).json()['active_index_id']


def test_asset_revoked_during_query_no_result_or_success_receipt(knowledge,model):
    body=metadata(knowledge);body.update(visibility='asset',asset_id=str(knowledge['asset']))
    revision=live(knowledge,body=body);built(knowledge,revision)
    def revoke(texts):
        with transaction() as c:c.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer AND asset_id=:asset'),knowledge)
    model.hook=revoke
    response=search(knowledge,include_developer=True,asset_id=str(knowledge['asset']))
    assert response.status_code==404
    with transaction() as c:assert c.execute(text('SELECT count(*) FROM knowledge_retrieval_runs WHERE organization_id=:org'),knowledge).scalar_one()==0


def test_withdrawal_during_query_excludes_old_blocks_but_history_stays(knowledge,model):
    revision=live(knowledge);built(knowledge,revision)
    before=search(knowledge,include_developer=True).json();chunk=before['hits'][0]
    def withdraw(texts):assert action(knowledge,revision,'withdraw').status_code==200
    model.hook=withdraw
    response=search(knowledge,include_developer=True);assert response.status_code==200,response.text
    assert response.json()['hits']==[]
    history=knowledge['client'].get('/api/v1/knowledge-documents/chunks/'+chunk['id']).json()
    assert history['status']=='withdrawn' and history['text']==chunk['text']
    assert knowledge['client'].get(PREFIX+'/retrievals/'+before['id']).json()['matches'][0]['chunk_id']==chunk['id']


def test_failed_commit_rolls_back_all_vectors_and_pointer(knowledge,model,monkeypatch):
    revision=live(knowledge);old=built(knowledge,revision)
    original=documents._event
    def fail(c,current,kind,*args,**kwargs):
        if kind=='index_ready':raise RuntimeError('private failure details must not leak')
        return original(c,current,kind,*args,**kwargs)
    monkeypatch.setattr(documents,'_event',fail)
    failed=build(knowledge,revision,version=1).json()
    assert failed['status']=='failed' and failed['active_index_id']==old['id']
    assert failed['error_code']=='KNOWLEDGE_INDEX_FAILED'
    with transaction() as c:assert c.execute(text('SELECT count(*) FROM knowledge_chunk_vectors WHERE run_id=:id'),dict(id=UUID(failed['id']))).scalar_one()==0


def test_vectors_and_generations_immutable_after_ready(knowledge,model):
    revision=live(knowledge);run=built(knowledge,revision)
    for sql in ['UPDATE knowledge_chunk_vectors SET input_sha256=\'changed\' WHERE run_id=:id',
                'DELETE FROM knowledge_chunk_vectors WHERE run_id=:id',
                'DELETE FROM knowledge_index_runs WHERE id=:id',
                "UPDATE knowledge_index_runs SET status='processing',row_version=row_version+1 WHERE id=:id"]:
        with pytest.raises(DBAPIError),transaction() as c:c.execute(text(sql),dict(id=UUID(run['id'])))


def test_no_eligible_content_skips_model_and_capacity_is_explicit(knowledge,model,monkeypatch):
    assert search(knowledge).json()['model_called'] is False and not model.calls
    revision=live(knowledge);built(knowledge,revision);model.calls.clear()
    monkeypatch.setattr(service,'MAX_ELIGIBLE',1)
    result=search(knowledge,include_developer=True)
    assert result.status_code==422 and result.json()['error']['code']=='KNOWLEDGE_SEARCH_CAPACITY'
    assert not model.calls


def test_runtime_change_does_not_mix_old_index_with_new_query(knowledge,model):
    revision=live(knowledge);built(knowledge,revision)
    model.runtime='fixture-embedding-v2'
    result=search(knowledge,include_developer=True);assert result.status_code==200,result.text
    assert result.json()['hits']==[] and result.json()['model_called']


def test_expired_interrupted_generation_can_be_retried_with_new_key(knowledge,model):
    revision=live(knowledge);run=built(knowledge,revision);interrupted=uuid4()
    with transaction() as c:
        c.execute(text('''INSERT INTO knowledge_index_runs(id,organization_id,revision_id,created_by,key_hash,request_hash,identity,identity_sha256,
            source_sha256,text_sha256,chunk_manifest_sha256,chunk_policy,chunk_count,expected_document_version,expected_revision_version,expected_index_version,reason,expires_at)
            SELECT :id,organization_id,revision_id,created_by,:key,request_hash,identity,identity_sha256,
            source_sha256,text_sha256,chunk_manifest_sha256,chunk_policy,chunk_count,expected_document_version,expected_revision_version,1,'Synthetic abandoned generation',clock_timestamp()-interval '1 second'
            FROM knowledge_index_runs WHERE id=:old'''),dict(id=interrupted,old=UUID(run['id']),key='b'*64))
    new=built(knowledge,revision,version=1)
    abandoned=knowledge['client'].get(PREFIX+'/runs/'+str(interrupted)).json()
    assert abandoned['status']=='failed' and abandoned['error_code']=='KNOWLEDGE_INDEX_INTERRUPTED'
    assert new['index_version']==2


def test_build_chunk_cap_preserves_publication(knowledge,model):
    revision=live(knowledge,raw=''.join(f'# H{i}\ntext\n' for i in range(129)).encode())
    response=build(knowledge,revision)
    assert response.status_code==422 and response.json()['error']['code']=='KNOWLEDGE_INDEX_CHUNK_LIMIT'
    assert not model.calls
    assert knowledge['client'].get('/api/v1/knowledge-documents/revisions/'+revision['id']).json()['status']=='published'


def test_search_csrf_admin_role_and_failed_model_audit(knowledge,model):
    oversized=b'{"query":"'+b'x'*17000+b'"}'
    assert knowledge['client'].post(PREFIX+'/search',content=oversized,headers={'content-type':'application/json'}).status_code==413
    assert knowledge['client'].post(PREFIX+'/search',content=iter([oversized[:8000],oversized[8000:]]),headers={'content-type':'application/json'}).status_code==413
    assert knowledge['client'].post(PREFIX+'/search',content=b'{"query":"\\ud800"}',headers={'content-type':'application/json'}).status_code==422
    csrf=knowledge['client'].headers.pop('x-csrf-token')
    try:assert search(knowledge).status_code==403
    finally:knowledge['client'].headers['x-csrf-token']=csrf
    with login(knowledge,'admin') as admin:assert search(knowledge,client=admin).status_code==403
    revision=live(knowledge);built(knowledge,revision)
    model.error=MeasurementError('EMBEDDING_SERVICE_UNAVAILABLE','safe fixture',status=503)
    response=search(knowledge,include_developer=True);assert response.status_code==503
    with transaction() as c:
        assert c.execute(text("SELECT count(*) FROM knowledge_events WHERE organization_id=:org AND action='search_failed'"),knowledge).scalar_one()>=1
        assert c.execute(text('SELECT count(*) FROM knowledge_retrieval_runs WHERE organization_id=:org'),knowledge).scalar_one()==0
