"""Real method-task retrieval, process loss, reattachment, cancellation and withdrawal."""
from hashlib import sha256
from datetime import datetime,timezone
import json
import os
import re
import runpy
import socket
import subprocess
import sys
from time import sleep,monotonic
from uuid import UUID,uuid4
from xml.etree import ElementTree
import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT,BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine,transaction
from vibradiag.services.identity_service import password_hasher


def main():
    test=setting('VIBRADIAG_TEST_DATABASE_URL');migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test or not migration or make_url(test).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:raise RuntimeError('Dedicated test database required')
    os.environ.update(VIBRADIAG_DATABASE_URL=test,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_COOKIE_SECURE='false',
        VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_AGENT_WORKER_ENABLED='false')
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    run_id=uuid4().hex;private=PROJECT_ROOT/'.runtime/agent-knowledge'/run_id;private.mkdir(parents=True)
    org=uuid4();password=str(uuid4())+str(uuid4());users={n:uuid4() for n in ('author','reader','admin')}
    with transaction() as c:
        c.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic task knowledge '+run_id))
        for name,identifier in users.items():
            c.execute(text('INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,:name,:hash)'),dict(id=identifier,org=org,name=name,hash=password_hasher.hash(password)))
            c.execute(text('INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,:role,:id)'),
                dict(id=identifier,org=org,role='engineer' if name=='author' else 'inspector' if name=='reader' else 'admin'))
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}';process=None;api=None;log=None;launch=0;clients={};workers=[]
    def stop_api():
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
    def start_api():
        nonlocal process,api,log,launch
        launch+=1;path=private/f'api-{launch}.log';log=path.open('w',encoding='utf8')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],cwd=PROJECT_ROOT,
            stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(150):
            if process.poll() is not None:raise RuntimeError('Owned API startup failed')
            try:
                if httpx.get(url+'/',timeout=1,trust_env=False).status_code==200:break
            except httpx.HTTPError:pass
            sleep(.1)
        else:raise RuntimeError('API startup timeout')
        if os.name=='nt':
            from verify_m3_isolation_flow import ProcessHandle
            found=re.findall(r'Started server process \[(\d+)\]',path.read_text(encoding='utf8'))
            if not found:raise RuntimeError('Owned API handle unavailable')
            api=ProcessHandle(int(found[-1]))
        for name in users:
            client=httpx.Client(base_url=url,timeout=140,trust_env=False);clients[name]=client
            response=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username=name,password=password));response.raise_for_status()
            client.headers['x-csrf-token']=response.json()['csrf_token']
    def post(name,path,body,key=None):
        response=clients[name].post(path,json=body,headers={'Idempotency-Key':key or str(uuid4())});response.raise_for_status();return response.json()
    def get(name,path):
        response=clients[name].get(path);response.raise_for_status();return response.json()
    def launch_worker(task,mode,endpoint='http://127.0.0.1:11434'):
        stop_api()  # API and owned execution probe must never hold the single-process lease concurrently.
        folder=private/('worker-'+uuid4().hex);folder.mkdir();job=folder/'job.json'
        job.write_text(json.dumps(dict(task_id=task['id'],mode=mode)),encoding='utf8');output=(folder/'worker.log').open('w',encoding='utf8')
        child=subprocess.Popen([sys.executable,str(BACKEND_ROOT/'scripts/m3_knowledge_probe.py'),str(job)],cwd=PROJECT_ROOT,
            env=dict(os.environ,VIBRADIAG_OLLAMA_URL=endpoint),stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        worker=dict(child=child,folder=folder,output=output,handle=None);workers.append(worker)
        for _ in range(150):
            if (folder/'pid.json').exists():
                if os.name=='nt' and not (folder/'done.json').exists():
                    from verify_m3_isolation_flow import ProcessHandle
                    worker['handle']=ProcessHandle(json.loads((folder/'pid.json').read_text())['pid'])
                return worker
            if child.poll() is not None:raise RuntimeError('Owned task worker startup failed')
            sleep(.05)
        raise RuntimeError('Task worker startup timeout')
    def finish_worker(worker,kill=False):
        child=worker['child'];handle=worker['handle']
        if kill:
            if handle is not None:handle.kill();handle.wait()
            elif child.poll() is None:child.terminate()
        try:child.wait(timeout=140 if not kill else 5)
        except subprocess.TimeoutExpired:
            if handle is not None:handle.kill();handle.wait()
            child.kill();child.wait(timeout=5);raise RuntimeError('Owned task worker timeout')
        if handle is not None:handle.close();worker['handle']=None
        worker['output'].close()
        if not kill and child.returncode!=0:raise RuntimeError('Owned task execution failed')
        if not kill and process is None:start_api()
        return json.loads((worker['folder']/'done.json').read_text()) if not kill else None
    def wait_ready(worker):
        for _ in range(1200):
            if (worker['folder']/'ready.json').exists():return json.loads((worker['folder']/'ready.json').read_text())
            if worker['child'].poll() is not None:raise RuntimeError('Task exited before retrieval commit')
            sleep(.05)
        raise RuntimeError('Real retrieval did not reach pause')
    tasks='/api/v1/agent-tasks';docs='/api/v1/knowledge-documents'
    def create(scoped=True):
        payload=dict(goal='explain_method',description='轴承包络方法缺少转速时需要哪些条件？')
        if scoped:payload['knowledge_scope']=dict(equipment='motor',component='bearing')
        return post('author',tasks,payload)
    try:
        start_api()
        post('admin',docs+'/review-grants',dict(user_id=str(users['author']),enabled=True,expected_version=0,reason='Synthetic explicit self-review',password=password))
        raw=('# 诊断方法\n包络谱需要转速、轴承几何和有效采集条件。\n## 边界\n缺少条件时保留缺失说明，不断言故障。\n'+run_id).encode()
        metadata=dict(title='Synthetic method task fixture',source_note='Developer-authored integration text, no independent engineering approval',equipment_scope=['motor'],
            component_scope='bearing',content_version='fixture-v1',usage_rights='Developer authored for automated testing',content_origin='internal_note',visibility='organization',reason='Synthetic process acceptance')
        response=clients['author'].post(docs,files={'file':('method.md',raw,'text/markdown')},data={'metadata':json.dumps(metadata)},headers={'Idempotency-Key':str(uuid4())});response.raise_for_status();revision=response.json()
        for op in ('submit','publish'):
            revision=post('author',docs+'/revisions/'+revision['id']+'/'+op,dict(expected_version=revision['row_version'],expected_document_version=revision['document_version'],reason='Synthetic '+op))
        built=post('author','/api/v1/knowledge-indexes/revisions/'+revision['id']+'/build',dict(expected_version=revision['row_version'],expected_document_version=revision['document_version'],expected_index_version=0,reason='Synthetic real model index'))
        assert built['status']=='ready'
        task=create(False);assert task['executor']=='rules_knowledge_v1'
        finish_worker(launch_worker(task,'execute'));paused=get('author',tasks+'/'+task['id'])
        assert paused['status']=='waiting_information' and not paused['calls']
        choice=next(item['id'] for item in paused['question']['choices'] if item['equipment']=='motor')
        post('author',tasks+'/'+task['id']+'/resume',dict(expected_version=paused['version'],question_id=paused['question']['id'],answer=choice))
        worker=launch_worker(task,'pause_after_retrieval');ready=wait_ready(worker)
        with transaction() as c:
            running=c.execute(text('SELECT status,budget FROM agent_tasks WHERE id=:id'),dict(id=UUID(task['id']))).mappings().one()
            call=c.execute(text('SELECT resource_id FROM agent_calls WHERE task_id=:id'),dict(id=UUID(task['id']))).scalar_one()
            assert running['status']=='running' and call is None and len(running['budget']['reservations'])==1
        finish_worker(worker,kill=True)  # Actual process loss after query receipt commit, before tool acknowledgement/checkpoint.
        stop_api();start_api()
        finish_worker(launch_worker(task,'recover'));interrupted=get('author',tasks+'/'+task['id'])
        assert interrupted['status']=='interrupted'
        post('author',tasks+'/'+task['id']+'/resume',dict(expected_version=interrupted['version'],question_id=interrupted['question']['id'],answer='Explicit retry after process loss'))
        with socket.socket() as unavailable:
            unavailable.bind(('127.0.0.1',0));bad=f'http://127.0.0.1:{unavailable.getsockname()[1]}'
            finish_worker(launch_worker(task,'execute',bad))  # Must reuse committed receipt; provider is genuinely unreachable.
        restored=get('author',tasks+'/'+task['id'])
        assert restored['status']=='completed_degraded' and restored['formal_graph_executed'] and restored['embedding_called'] and not restored['model_called']
        assert len(restored['calls'])==1 and restored['calls'][0]['resource_id']==ready['retrieval_id']
        assert len(restored['budget']['reservations'])==1 and len(restored['knowledge_references'])==2
        assert restored['draft']['missing_requirements']==['knowledge_relevance_not_qualified']
        assert all(h['untrusted_content'] and h['source_sha256']==sha256(raw).hexdigest() for h in restored['knowledge_references'])
        assert clients['reader'].get(tasks+'/'+task['id']).status_code==404
        with transaction() as c:assert c.execute(text('SELECT count(*) FROM knowledge_retrieval_runs WHERE agent_task_id=:id'),dict(id=UUID(task['id']))).scalar_one()==1
        # A separate task is cancelled after its retrieval commit but before late tool completion.
        cancelled=create();worker=launch_worker(cancelled,'pause_after_retrieval_release_lease');wait_ready(worker)
        start_api()  # Old probe released its lease but still holds the delayed result.
        current=get('author',tasks+'/'+cancelled['id'])
        post('author',tasks+'/'+cancelled['id']+'/cancel',dict(expected_version=current['version']))
        (worker['folder']/'release').write_text('release',encoding='utf8');finish_worker(worker)
        assert get('author',tasks+'/'+cancelled['id'])['draft'] is None
        assert get('author',tasks+'/'+cancelled['id'])['status']=='cancelled'
        post('author',docs+'/revisions/'+revision['id']+'/withdraw',dict(expected_version=revision['row_version'],expected_document_version=revision['document_version'],reason='Synthetic withdrawal'))
        invalid=get('author',tasks+'/'+task['id'])
        assert invalid['draft'] is None and invalid['knowledge_references']==[] and not invalid['draft_validity']['knowledge_current']
        historical=get('author',docs+'/chunks/'+restored['knowledge_references'][0]['id']);assert historical['status']=='withdrawn'
        empty=create();finish_worker(launch_worker(empty,'execute'));refused=get('author',tasks+'/'+empty['id'])
        assert refused['status']=='refused' and not refused['embedding_called'] and refused['draft']['observations']==[]
        full=PROJECT_ROOT/'experiments/m3-t03c-tests.xml';focused=PROJECT_ROOT/'experiments/m3-t03c-agent-tests.xml';boundary=PROJECT_ROOT/'experiments/m3-t03c-boundary-tests.xml'
        suites=[ElementTree.parse(path).getroot().find('testsuite') for path in (full,focused,boundary)]
        assert all(int(s.attrib[k])==0 for s in suites for k in ('failures','errors','skipped'))
        assert int(suites[1].attrib['tests'])==17
        paths=[p for directory in ('backend/src','backend/tests','backend/scripts','backend/migrations','backend/config') for p in (PROJECT_ROOT/directory).rglob('*') if p.suffix in {'.py','.json'} and '__pycache__' not in p.parts]
        paths += [full,focused,boundary]+[PROJECT_ROOT/p for p in ('backend/requirements.lock.txt','README.md','backend/README.md','docs/README.md','docs/03-milestones.md',
            'docs/07-m3-agent-design.md','docs/08-knowledge-design.md','docs/contracts/m3-agent-contract.json','docs/acceptance/M3-T03c-verification.md')]
        report=dict(passed=True,recorded_at=datetime.now(timezone.utc).isoformat(),run_id=run_id,scope='M3-T03c method-only task-bound retrieved context and citations',
            executor=restored['executor'],graph=restored['graph'],logical_tool_calls=len(restored['calls']),reservations=len(restored['budget']['reservations']),
            knowledge_reference_count=len(restored['knowledge_references']),source_sha256=sha256(raw).hexdigest(),
            actual_process=dict(scope_clarified_over_http=True,real_embedding_called=True,receipt_committed_before_worker_kill=True,
                api_restarted=True,explicit_recovery_and_resume=True,reattached_with_unreachable_provider=True,one_retrieval_row=True,
                cancellation_fenced_late_completion=True,withdrawal_hides_draft=True,history_still_resolves=True,empty_corpus_refuses_without_embedding=True),
            full_backend_tests={k:suites[0].attrib[k] for k in ('tests','failures','errors','skipped','time')},agent_tests={k:suites[1].attrib[k] for k in ('tests','failures','errors','skipped','time')},
            boundary_tests={k:suites[2].attrib[k] for k in ('tests','failures','errors','skipped','time')},
            llm_decision_called=False,generated_answer=False,retrieval_relevance_qualified=False,factory_trial_approved=False,
            limitations=['Synthetic self-authored internal_note fixture; no independent engineering or relevance approval',
                'Method-only rule graph returns cited context for review, not a generated method answer or device conclusion',
                'API hosted consumer disabled to isolate task IDs; owned probes invoke the same claim/execute/recover service paths',
                'Cancellation fences persistence but does not hard-kill Ollama server inference; provider waits are limited by remaining task budget',
                'Measurement-task knowledge enrichment, DeepSeek decisions/generation and held-out retrieval qualification remain later work'],
            source_fingerprints={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/'experiments/m3-t03c-knowledge.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
        print(json.dumps({k:report[k] for k in ('passed','executor','logical_tool_calls','knowledge_reference_count','full_backend_tests','agent_tests','boundary_tests')}))
    finally:
        for worker in workers:
            if worker['child'].poll() is None:finish_worker(worker,kill=True)
            elif not worker['output'].closed:worker['output'].close()
        stop_api()


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(f'Agent knowledge verification failed ({type(error).__name__}); inspect private logs; credentials hidden') from None
