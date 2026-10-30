"""Actual authenticated HTTP publication/revocation/restart; synthetic source text, no model or vector search."""
from hashlib import sha256
import json
import os
import re
import runpy
import socket
import subprocess
import sys
from time import sleep
from uuid import uuid4
from xml.etree import ElementTree
import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT,BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import transaction,engine
from vibradiag.services.identity_service import password_hasher,authenticate
from vibradiag.services.knowledge_document_service import published_candidates


def main():
    test=setting('VIBRADIAG_TEST_DATABASE_URL');migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test or not migration or make_url(test).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:raise RuntimeError('Dedicated test database required')
    run_id=uuid4().hex;private=PROJECT_ROOT/'.runtime/knowledge-publication'/run_id;private.mkdir(parents=True)
    os.environ.update(VIBRADIAG_DATABASE_URL=test,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_COOKIE_SECURE='false',
        VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_AGENT_WORKER_ENABLED='false')
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    org=uuid4();password=str(uuid4())+str(uuid4());users={name:uuid4() for name in ('author','reviewer','reader','admin')}
    with transaction() as connection:
        connection.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic knowledge '+run_id))
        for name,identifier in users.items():
            connection.execute(text('INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,:name,:hash)'),dict(id=identifier,org=org,name=name,hash=password_hasher.hash(password)))
            connection.execute(text('INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,:role,:id)'),
                dict(id=identifier,org=org,role='engineer' if name in {'author','reviewer'} else 'inspector' if name=='reader' else 'admin'))
        tables=connection.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'")).scalar_one()
        available=connection.execute(text("SELECT count(*) FROM pg_available_extensions WHERE name='vector'")).scalar_one()
        installed=connection.execute(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")).scalar_one()
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
    def start():
        nonlocal process,api,log,launch
        launch+=1;path=private/f'api-{launch}.log';log=path.open('w',encoding='utf8')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],
            cwd=PROJECT_ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(150):
            if process.poll() is not None:raise RuntimeError('Owned API startup failed')
            try:
                if httpx.get(url+'/',timeout=1).status_code==200:break
            except httpx.HTTPError:pass
            sleep(.1)
        else:raise RuntimeError('Owned API startup timeout')
        if os.name=='nt':
            from verify_m3_isolation_flow import ProcessHandle
            pids=re.findall(r'Started server process \[(\d+)\]',path.read_text(encoding='utf8'))
            if not pids:raise RuntimeError('Owned API PID unavailable')
            api=ProcessHandle(int(pids[-1]))
        for name in users:
            client=httpx.Client(base_url=url,timeout=60);clients[name]=client
            auth=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username=name,password=password));auth.raise_for_status()
            client.headers['x-csrf-token']=auth.json()['csrf_token']
    prefix='/api/v1/knowledge-documents'
    def post(name,path,body,key=None):
        response=clients[name].post(path,json=body,headers={'Idempotency-Key':key or str(uuid4())});response.raise_for_status();return response.json()
    def get(name,path):
        response=clients[name].get(path);response.raise_for_status();return response.json()
    def action(name,revision,operation):
        return post(name,prefix+'/revisions/'+revision['id']+'/'+operation,dict(expected_version=revision['row_version'],expected_document_version=revision['document_version'],reason='Synthetic HTTP '+operation))
    def upload(raw,metadata,document=None,key=None):
        response=clients['author'].post(prefix+(f'/{document}/revisions' if document else ''),files={'file':('synthetic.md',raw,'text/markdown')},
            data={'metadata':json.dumps(metadata)},headers={'Idempotency-Key':key or str(uuid4())});response.raise_for_status();return response.json(),response.status_code
    try:
        start()
        metadata=dict(title='Synthetic knowledge source',source_note='Authored for automated HTTP acceptance; no standard/manual/expert assertion',
            equipment_scope=['motor'],component_scope='bearing',content_version='fixture-v1',usage_rights='Developer-authored fixture, permitted for this test',
            content_origin='developer_test',visibility='organization',reason='Synthetic import')
        raw=b'\xef\xbb\xbf'+('# 方法\r\n开发验证，不用于现场定类。\r\n## 限制\r\n<script>never_execute()</script>\r\n[外链](http://example.invalid/never-fetch)\r\n'+run_id).encode()
        key=str(uuid4());first,status=upload(raw,metadata,key=key);assert status==201 and first['status']=='draft'
        replay,status=upload(raw,metadata,key=key);assert status==200 and replay['id']==first['id']
        assert clients['reader'].get(prefix+'/revisions/'+first['id']).status_code==404
        pending=action('author',first,'submit')
        denied=clients['author'].post(prefix+'/revisions/'+first['id']+'/publish',json=dict(expected_version=pending['row_version'],expected_document_version=pending['document_version'],reason='No implicit review'),headers={'Idempotency-Key':str(uuid4())})
        assert denied.status_code==403
        post('admin',prefix+'/review-grants',dict(user_id=str(users['reviewer']),enabled=True,expected_version=0,reason='Explicit synthetic review capability',password=password))
        assert clients['admin'].get(prefix+'/revisions/'+first['id']).status_code==403
        published=action('reviewer',pending,'publish');assert published['status']=='published' and not published['formal_retrieval_available']
        chunks=get('reader',prefix+'/revisions/'+first['id']+'/chunks')['items'];assert len(chunks)==2
        original=clients['reader'].get(prefix+'/revisions/'+first['id']+'/original');original.raise_for_status()
        assert original.content==raw and original.headers['content-type'].startswith('text/plain') and original.headers['x-content-type-options']=='nosniff'
        with transaction() as connection:principal=authenticate(connection,clients['author'].cookies.get('vibradiag_session'))
        assert len(published_candidates(principal,include_developer=True))==2 and published_candidates(principal)==[]
        second,status=upload(raw+b'\nNew revision.\n',dict(metadata,title='Synthetic revision two',content_version='fixture-v2',expected_document_version=published['document_version']),first['document_id'])
        assert get('reader',prefix+'/'+first['document_id'])['id']==first['id']
        assert clients['reader'].get(prefix+'/revisions/'+second['id']).status_code==404
        second=action('reviewer',action('author',second,'submit'),'publish')
        assert get('reader',prefix+'/chunks/'+chunks[0]['id'])['status']=='withdrawn'
        stop();start()
        restored=get('reviewer',prefix+'/revisions/'+second['id']);assert restored['status']=='published'
        old=get('reader',prefix+'/chunks/'+chunks[0]['id']);assert old['text']==chunks[0]['text'] and old['source_sha256']==sha256(raw).hexdigest()
        withdrawn=action('reviewer',restored,'withdraw');assert withdrawn['status']=='withdrawn'
        assert get('reader',prefix)['items']==[]
        with transaction() as connection:principal=authenticate(connection,clients['author'].cookies.get('vibradiag_session'))
        assert published_candidates(principal,include_developer=True)==[]
        post('admin',prefix+'/review-grants',dict(user_id=str(users['reviewer']),enabled=False,expected_version=1,reason='Explicit synthetic revocation',password=password))
        third,status=upload(raw+b'\nThird private draft.\n',dict(metadata,content_version='fixture-v3',expected_document_version=withdrawn['document_version']),first['document_id'])
        third=action('author',third,'submit')
        assert clients['reviewer'].get(prefix+'/revisions/'+third['id']).status_code==404
        denied=clients['reviewer'].post(prefix+'/revisions/'+third['id']+'/publish',json=dict(expected_version=third['row_version'],expected_document_version=third['document_version'],reason='Revoked capability'),headers={'Idempotency-Key':str(uuid4())})
        assert denied.status_code==403
        print('HTTP import/review/publication/revision/withdrawal and API restart passed',flush=True)
        tests='experiments/m3-t03a-tests.xml';focused='experiments/m3-t03a-knowledge-tests.xml'
        suite=ElementTree.parse(PROJECT_ROOT/tests).getroot().find('testsuite');special=ElementTree.parse(PROJECT_ROOT/focused).getroot().find('testsuite')
        assert all(int(s.attrib[k])==0 for s in (suite,special) for k in ('failures','errors','skipped'))
        assert int(special.attrib['tests'])==25
        assert len([c for c in suite.findall('testcase') if c.attrib['classname'].endswith('test_knowledge_documents')])==15
        paths=[p for directory in ('backend/src','backend/tests','backend/scripts','backend/migrations') for p in (PROJECT_ROOT/directory).rglob('*.py') if '__pycache__' not in p.parts]
        paths += [PROJECT_ROOT/p for p in (tests,focused,'backend/requirements.lock.txt','README.md','backend/README.md','docs/README.md','docs/03-milestones.md',
            'docs/07-m3-agent-design.md','docs/08-knowledge-design.md','docs/acceptance/M3-T03a-verification.md')]
        report=dict(passed=True,run_id=run_id,scope='M3-T03a source text publication only',business_tables=tables,checkpoint_tables=4,migration_head='0015_knowledge_documents',
            source=dict(source_sha256=sha256(raw).hexdigest(),format='md',bom_crlf_preserved=True,chunk_count=len(chunks)),
            actual_http=dict(draft_private=True,idempotent_same_revision=True,explicit_reviewer_grant=True,no_implicit_admin_content_access=True,
                new_draft_preserves_old_publication=True,old_chunk_stable_after_republication=True,restart_persistent=True,
                withdrawn_excluded_from_publication_prefilter=True,withdrawn_history_resolves=True,grant_revocation_effective=True),
            publication_prefilter_is_vector_search=False,vector_index_integrated=False,agent_knowledge_integrated=False,model_called=False,
            postgresql_vector=dict(available=bool(available),installed=bool(installed),extension_modified=False),
            full_backend_tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},knowledge_tests={k:special.attrib[k] for k in ('tests','failures','errors','skipped','time')},
            limitations=['Text publication is an application approval, not independent engineering/source/license authentication',
                'No vector index, Embedding call, semantic ranking or Agent knowledge citations in this stage',
                'Developer test content excluded from the production publication prefilter by default',
                'No knowledge frontend or full lifecycle/backup capacity approval; factory trial still pending'],
            source_sha256={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/'experiments/m3-t03a-publication.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
        print(json.dumps({k:report[k] for k in ('passed','business_tables','full_backend_tests','knowledge_tests','vector_index_integrated')}))
    finally:stop()


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(f'Knowledge HTTP verification failed ({type(error).__name__}); inspect private logs; credentials hidden') from None
