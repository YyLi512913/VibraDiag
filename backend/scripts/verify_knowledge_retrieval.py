"""Owned real HTTP + local embedding + PostgreSQL exact retrieval/restart acceptance."""
from hashlib import sha256
from datetime import datetime,timezone
import json
import os
import re
import runpy
import socket
import subprocess
import sys
from time import sleep
from uuid import uuid4,UUID
from xml.etree import ElementTree

import httpx
import numpy as np
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT,BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine,transaction
from vibradiag.infrastructure.retrieval.embedding import configured_identity,LocalEmbedding
from vibradiag.infrastructure.retrieval.vector_readiness import inspect_vector
from vibradiag.services.identity_service import password_hasher


def main():
    test=setting('VIBRADIAG_TEST_DATABASE_URL');migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test or not migration or make_url(test).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:raise RuntimeError('Separate test database required')
    os.environ.update(VIBRADIAG_DATABASE_URL=test,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_COOKIE_SECURE='false',
        VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_AGENT_WORKER_ENABLED='false')
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    run_id=uuid4().hex;private=PROJECT_ROOT/'.runtime/knowledge-retrieval'/run_id;private.mkdir(parents=True)
    org=uuid4();password=str(uuid4())+str(uuid4());users={n:uuid4() for n in ('author','reader','admin')}
    with transaction() as c:
        c.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic dense HTTP '+run_id))
        for name,identifier in users.items():
            c.execute(text('INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,:name,:hash)'),dict(id=identifier,org=org,name=name,hash=password_hasher.hash(password)))
            c.execute(text('INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,:role,:id)'),dict(id=identifier,org=org,role='engineer' if name=='author' else 'inspector' if name=='reader' else 'admin'))
        tables=c.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'")).scalar_one()
        checkpoints=c.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public_agent_checkpoints'")).scalar_one()
        vector=inspect_vector(c)
        assert tables==42 and checkpoints==4
        assert vector['installed_version']=='0.8.6' and vector['extension_schema']=='vibradiag_extensions' and not vector['public_ddl']
        assert not c.execute(text("SELECT has_schema_privilege(current_user,'vibradiag_extensions','CREATE')")).scalar_one()
    owner=create_engine(make_url(migration),hide_parameters=True,connect_args={'connect_timeout':5})
    try:
        with owner.connect() as c:
            c.exec_driver_sql('SET TRANSACTION READ ONLY')
            schema=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert schema=='0016_knowledge_vectors'
    finally:owner.dispose()
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}';process=None;api=None;log=None;launch=0;clients={}
    def stop():
        nonlocal process,api,log
        for client in clients.values():client.close()
        clients.clear()
        if api is not None:api.kill();api.wait();api.close();api=None
        if process is not None:
            if os.name!='nt' and process.poll() is None:process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            process=None
        if log is not None:log.close();log=None
    def start(ollama='http://127.0.0.1:11434'):
        nonlocal process,api,log,launch
        launch+=1;path=private/f'api-{launch}.log';log=path.open('w',encoding='utf8')
        env=dict(os.environ,VIBRADIAG_OLLAMA_URL=ollama)
        process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],
            cwd=PROJECT_ROOT,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(150):
            if process.poll() is not None:raise RuntimeError('Owned API startup failed')
            try:
                if httpx.get(url+'/',timeout=1,trust_env=False).status_code==200:break
            except httpx.HTTPError:pass
            sleep(.1)
        else:raise RuntimeError('Owned API startup timeout')
        if os.name=='nt':
            from verify_m3_isolation_flow import ProcessHandle
            pids=re.findall(r'Started server process \[(\d+)\]',path.read_text(encoding='utf8'))
            if not pids:raise RuntimeError('Owned server handle unavailable')
            api=ProcessHandle(int(pids[-1]))
        for name in users:
            client=httpx.Client(base_url=url,timeout=140,trust_env=False);clients[name]=client
            response=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username=name,password=password));response.raise_for_status()
            client.headers['x-csrf-token']=response.json()['csrf_token']
    docs='/api/v1/knowledge-documents';indexes='/api/v1/knowledge-indexes'
    def post(name,path,body,key=None):
        response=clients[name].post(path,json=body,headers={'Idempotency-Key':key or str(uuid4())});response.raise_for_status();return response.json(),response.status_code
    def get(name,path):
        response=clients[name].get(path);response.raise_for_status();return response.json()
    def action(revision,op):return post('author',docs+'/revisions/'+revision['id']+'/'+op,dict(expected_version=revision['row_version'],expected_document_version=revision['document_version'],reason='Synthetic HTTP '+op))[0]
    def upload(raw,metadata,document=None):
        response=clients['author'].post(docs+(f'/{document}/revisions' if document else ''),files={'file':('dense-fixture.md',raw,'text/markdown')},
            data={'metadata':json.dumps(metadata)},headers={'Idempotency-Key':str(uuid4())});response.raise_for_status();return response.json()
    def build(revision,version=0,key=None):return post('author',indexes+'/revisions/'+revision['id']+'/build',dict(expected_version=revision['row_version'],expected_document_version=revision['document_version'],expected_index_version=version,reason='Synthetic real embedding build'),key)
    try:
        start()
        post('admin',docs+'/review-grants',dict(user_id=str(users['author']),enabled=True,expected_version=0,reason='Explicit synthetic self-review',password=password))
        metadata=dict(title='Synthetic dense integration fixture',source_note='Developer-authored integration text; no field/expert evidence',
            equipment_scope=['motor'],component_scope='bearing',content_version='fixture-v1',usage_rights='Developer authored for automated testing',
            content_origin='internal_note',visibility='organization',reason='Synthetic HTTP acceptance')
        raw=b'\xef\xbb\xbf'+('# 诊断条件\r\n包络谱解释需要转速、轴承几何和有效采集条件。\r\n'
            '## 边界\r\n缺少条件时记录缺失信息，不根据振动单独断言润滑失效。\r\n'+run_id).encode()
        revision=action(action(upload(raw,metadata),'submit'),'publish')
        key=str(uuid4());first,status=build(revision,key=key);assert status==201 and first['status']=='ready' and first['vector_count']==2
        replay,status=build(revision,key=key);assert status==200 and replay['id']==first['id']
        query='轴承包络诊断缺少转速时应怎样处理？'
        result,_=post('reader',indexes+'/search',dict(query=query));assert len(result['hits'])==2 and result['model_called']
        assert result['retrieval_mode']=='published_dense_exact' and not result['relevance_qualified']
        source=get('reader',docs+'/chunks/'+result['hits'][0]['id']);assert source['source_sha256']==sha256(raw).hexdigest()
        # Independently compare PostgreSQL cosine scores to NumPy on the actual persisted float32 data.
        identity=configured_identity();query_vector,_=LocalEmbedding(identity).encode([query]);max_error=0
        with transaction() as c:
            rows=c.execute(text('SELECT chunk_id,embedding::text FROM knowledge_chunk_vectors WHERE run_id=:run'),dict(run=UUID(first['id']))).all()
        saved={str(row[0]):np.asarray(json.loads(row[1]),dtype=np.float64) for row in rows}
        q=query_vector[0].astype(np.float64)
        for hit in result['hits']:
            v=saved[hit['id']];expected=float(np.dot(q,v)/(np.linalg.norm(q)*np.linalg.norm(v)))
            max_error=max(max_error,abs(expected-hit['similarity']))
        assert max_error<2e-5
        before=get('reader',indexes+'/retrievals/'+result['id'])
        assert clients['author'].get(indexes+'/retrievals/'+result['id']).status_code==404
        # A real unreachable provider makes rebuild fail, without damaging the active generation.
        stop()
        with socket.socket() as unavailable:
            unavailable.bind(('127.0.0.1',0));bad_port=unavailable.getsockname()[1]
            start(f'http://127.0.0.1:{bad_port}')
            failed,status=build(revision,version=1);assert status==201 and failed['status']=='failed' and failed['active_index_id']==first['id']
            assert clients['reader'].post(indexes+'/search',json=dict(query=query)).status_code==503
            stop()
        start()
        restored=get('reader',indexes+'/retrievals/'+result['id']);assert restored['matches']==before['matches']
        again,_=post('reader',indexes+'/search',dict(query=query));assert {h['index_id'] for h in again['hits']}=={first['id']}
        rebuilt,status=build(revision,version=1);assert rebuilt['status']=='ready' and rebuilt['index_version']==2
        assert not get('reader',indexes+'/runs/'+first['id'])['is_active']
        # New publication is not searchable until its own complete index is ready.
        second=upload(raw+b'\nSecond source version.\n',dict(metadata,content_version='fixture-v2',expected_document_version=revision['document_version']),revision['document_id'])
        second=action(action(second,'submit'),'publish')
        assert post('reader',indexes+'/search',dict(query=query))[0]['hits']==[]
        second_index,_=build(second);assert second_index['status']=='ready'
        assert {h['revision_id'] for h in post('reader',indexes+'/search',dict(query=query))[0]['hits']}=={second['id']}
        action(second,'withdraw')
        empty,_=post('reader',indexes+'/search',dict(query=query));assert empty['hits']==[] and not empty['model_called']
        historical=get('reader',docs+'/chunks/'+result['hits'][0]['id']);assert historical['status']=='withdrawn' and historical['text']==source['text']
        tests=PROJECT_ROOT/'experiments/m3-t03b2-tests.xml';focused=PROJECT_ROOT/'experiments/m3-t03b2-index-tests.xml'
        boundary=PROJECT_ROOT/'experiments/m3-t03b2-boundary-tests.xml'
        suites=[ElementTree.parse(path).getroot().find('testsuite') for path in (tests,focused,boundary)]
        assert all(int(s.attrib[k])==0 for s in suites for k in ('failures','errors','skipped'))
        assert int(suites[1].attrib['tests'])==19
        assert len([case for case in suites[0].findall('testcase') if case.attrib['classname'].endswith('test_knowledge_indexes')])==19
        maintenance=PROJECT_ROOT/'experiments/m3-t03b2-extension-setup.json'
        setup=json.loads(maintenance.read_text(encoding='utf8'));assert setup['passed'] and not setup['credentials_recorded']
        for path,expected in setup['source_sha256'].items():assert sha256((PROJECT_ROOT/path).read_bytes()).hexdigest()==expected
        paths=[p for directory in ('backend/src','backend/tests','backend/scripts','backend/migrations','backend/config') for p in (PROJECT_ROOT/directory).rglob('*') if p.suffix in {'.py','.json'} and '__pycache__' not in p.parts]
        paths += [tests,focused,boundary,maintenance]+[PROJECT_ROOT/p for p in ('backend/requirements.lock.txt','README.md','backend/README.md','docs/README.md',
            'docs/03-milestones.md','docs/07-m3-agent-design.md','docs/08-knowledge-design.md','docs/acceptance/M3-T03b2-verification.md')]
        report=dict(passed=True,run_id=run_id,recorded_at=datetime.now(timezone.utc).isoformat(),scope='M3-T03b2 scoped exact dense retrieval, not Agent/RAG quality qualification',
            business_tables=tables,checkpoint_tables=checkpoints,migration_head=schema,vector=vector,embedding_identity=identity.__dict__,
            extension_setup_receipt_sha256=sha256(maintenance.read_bytes()).hexdigest(),
            real_embedding_called=True,cosine_numpy_max_absolute_error=max_error,
            actual_http=dict(ready_index_with_two_vectors=True,idempotent_build=True,normal_reader_search=True,own_receipt_only=True,
                failed_real_rebuild_preserves_old_index=True,unavailable_model_search_fails=True,api_restart_preserves_receipt_and_index=True,
                atomic_generation_switch=True,new_publication_waits_for_own_index=True,withdrawn_excluded=True,withdrawn_history_stable=True),
            full_backend_tests={k:suites[0].attrib[k] for k in ('tests','failures','errors','skipped','time')},index_tests={k:suites[1].attrib[k] for k in ('tests','failures','errors','skipped','time')},
            boundary_tests={k:suites[2].attrib[k] for k in ('tests','failures','errors','skipped','time')},
            agent_knowledge_integrated=False,retrieval_quality_qualified=False,ann_index=False,
            limitations=['Synthetic developer-authored source declared as internal_note for API compatibility testing; no independent source or engineering approval',
                'Exact cosine scores are relevance signals, not diagnostic probabilities; no calibrated relevance threshold or holdout qualification',
                'Synchronous build cap 128 chunks/120s and search cap 10000 eligible vectors; single API, one build and one local embedding request per process',
                'Expired interrupted generations require an explicit retry with a new key; no autonomous background index recovery',
                'No Agent citation integration, knowledge frontend, production capacity or factory trial approval'],
            source_sha256={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/'experiments/m3-t03b2-retrieval.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
        print(json.dumps({k:report[k] for k in ('passed','business_tables','migration_head','real_embedding_called','cosine_numpy_max_absolute_error','full_backend_tests','index_tests')}))
    finally:stop()


if __name__=='__main__':
    try:main()
    except Exception as error:
        raise SystemExit(f'Dense retrieval HTTP verification failed ({type(error).__name__}); inspect private logs; credentials hidden') from None
