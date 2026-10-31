"""Real model-driven method graph, completed-round recovery, cancellation and source invalidation."""
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
        VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_AGENT_WORKER_ENABLED='false',VIBRADIAG_METHOD_EXECUTOR='model',VIBRADIAG_FORMAL_LLM_PROVIDER='deepseek')
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    run_id=uuid4().hex;private=PROJECT_ROOT/'.runtime/model-method'/run_id;private.mkdir(parents=True)
    org=uuid4();password=str(uuid4())+str(uuid4());users={n:uuid4() for n in ('author','reader','admin')}
    with transaction() as c:
        c.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic model graph '+run_id))
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
        child=subprocess.Popen([sys.executable,str(BACKEND_ROOT/'scripts/m3_model_graph_probe.py'),str(job)],cwd=PROJECT_ROOT,
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
        payload=dict(goal='explain_method',description='What condition does bearing envelope interpretation require?')
        if scoped:payload['knowledge_scope']=dict(equipment='motor',component='bearing')
        return post('author',tasks,payload)
    original_external=setting('VIBRADIAG_ALLOW_EXTERNAL_LLM','false')
    try:
        start_api()
        post('admin',docs+'/review-grants',dict(user_id=str(users['author']),enabled=True,expected_version=0,reason='Synthetic explicit self-review',password=password))
        raw=('# Method\nBearing envelope interpretation requires a valid speed source.\n'
             '# Limit\nMissing sampling conditions prevent a definite fault conclusion.\n'+run_id).encode()
        metadata=dict(title='Synthetic model method fixture',source_note='Self-authored English development fixture; no independent engineering approval',
            equipment_scope=['motor'],component_scope='bearing',content_version='fixture-v1',usage_rights='Developer authored for automated verification',
            content_origin='internal_note',visibility='organization',reason='Synthetic model graph verification')
        response=clients['author'].post(docs,files={'file':('model-method.md',raw,'text/markdown')},data={'metadata':json.dumps(metadata)},headers={'Idempotency-Key':str(uuid4())})
        response.raise_for_status();revision=response.json()
        for op in ('submit','publish'):
            revision=post('author',docs+'/revisions/'+revision['id']+'/'+op,dict(expected_version=revision['row_version'],expected_document_version=revision['document_version'],reason='Synthetic '+op))
        built=post('author','/api/v1/knowledge-indexes/revisions/'+revision['id']+'/build',dict(expected_version=revision['row_version'],expected_document_version=revision['document_version'],expected_index_version=0,reason='Synthetic real source index'))
        assert built['status']=='ready'
        task=create(False);assert task['executor']=='model_method_v1'
        finish_worker(launch_worker(task,'execute'));paused=get('author',tasks+'/'+task['id'])
        assert paused['status']=='waiting_information' and paused['model_rounds']==[]
        choice=next(item['id'] for item in paused['question']['choices'] if item['equipment']=='motor')
        post('author',tasks+'/'+task['id']+'/resume',dict(expected_version=paused['version'],question_id=paused['question']['id'],answer=choice))
        worker=launch_worker(task,'pause_after_generation');ready=wait_ready(worker)
        with transaction() as c:
            round_states=c.execute(text('SELECT phase,state FROM agent_model_rounds WHERE task_id=:id ORDER BY round_no'),dict(id=UUID(task['id']))).all()
            assert round_states==[('decision','completed'),('extractive_draft','completed')]
            assert c.execute(text('SELECT count(*) FROM agent_calls WHERE task_id=:id'),dict(id=UUID(task['id']))).scalar_one()==1
        finish_worker(worker,kill=True);start_api()
        finish_worker(launch_worker(task,'recover'));interrupted=get('author',tasks+'/'+task['id'])
        assert interrupted['status']=='interrupted' and len(interrupted['budget']['reservations'])==3
        post('author',tasks+'/'+task['id']+'/resume',dict(expected_version=interrupted['version'],question_id=interrupted['question']['id'],answer='Recover the completed model receipts'))
        # Persisted completed rounds and the existing retrieval must suffice with both new network paths disabled.
        os.environ['VIBRADIAG_ALLOW_EXTERNAL_LLM']='false'
        try:
            with socket.socket() as unavailable:
                unavailable.bind(('127.0.0.1',0))
                finish_worker(launch_worker(task,'execute',f'http://127.0.0.1:{unavailable.getsockname()[1]}'))
        finally:
            stop_api();os.environ['VIBRADIAG_ALLOW_EXTERNAL_LLM']=original_external;start_api()
        restored=get('author',tasks+'/'+task['id'])
        from vibradiag.services.model_method_service import QUOTE_PREFIX
        assert restored['status']=='completed_degraded' and restored['model_called'] and restored['formal_graph_executed']
        assert len(restored['model_rounds'])==2 and len(restored['calls'])==1 and len(restored['budget']['reservations'])==3
        assert restored['draft']['observations'] and all(o['text'].startswith(QUOTE_PREFIX) for o in restored['draft']['observations'])
        assert restored['draft']['publication_state']=='unpublished_draft'
        for observation in restored['draft']['observations']:
            source=next(h for h in restored['knowledge_references'] if h['id']==observation['citations'][0]['id'])
            assert observation['text'][len(QUOTE_PREFIX):] in source['text']
        assert all(m['provider']=='deepseek' for m in restored['model_rounds'])
        # Stop a delayed old executor from using a successful decision after HTTP cancellation.
        cancelled=create();worker=launch_worker(cancelled,'pause_after_decision_release_lease');wait_ready(worker);start_api()
        current=get('author',tasks+'/'+cancelled['id'])
        post('author',tasks+'/'+cancelled['id']+'/cancel',dict(expected_version=current['version']))
        (worker['folder']/'release').write_text('release',encoding='utf8');finish_worker(worker)
        cancelled_result=get('author',tasks+'/'+cancelled['id'])
        assert cancelled_result['status']=='cancelled' and cancelled_result['draft'] is None and cancelled_result['calls']==[]
        # A separate explicit local task runs through the same graph; no silent cloud fallback.
        stop_api();os.environ['VIBRADIAG_FORMAL_LLM_PROVIDER']='ollama';start_api()
        local_task=create();finish_worker(launch_worker(local_task,'execute'));local=get('author',tasks+'/'+local_task['id'])
        local_passed=bool(local['status']=='completed_degraded' and local['draft'] and local['draft']['observations'] and all(o['text'].startswith(QUOTE_PREFIX) for o in local['draft']['observations']))
        assert all(m['provider']=='ollama' for m in local['model_rounds'])
        stop_api();os.environ['VIBRADIAG_FORMAL_LLM_PROVIDER']='deepseek';start_api()
        post('author',docs+'/revisions/'+revision['id']+'/withdraw',dict(expected_version=revision['row_version'],expected_document_version=revision['document_version'],reason='Synthetic withdrawal'))
        invalid=get('author',tasks+'/'+task['id']);assert invalid['draft'] is None and not invalid['draft_validity']['knowledge_current']
        full=PROJECT_ROOT/'experiments/m3-t04b1-tests.xml';focused=PROJECT_ROOT/'experiments/m3-t04b1-model-graph-tests.xml';boundary=PROJECT_ROOT/'experiments/m3-t04b1-boundary-tests.xml'
        suites=[ElementTree.parse(path).getroot().find('testsuite') for path in (full,focused,boundary)]
        assert all(int(s.attrib[k])==0 for s in suites for k in ('failures','errors','skipped'))
        paths=[p for directory in ('backend/src','backend/tests','backend/scripts','backend/migrations','backend/config') for p in (PROJECT_ROOT/directory).rglob('*') if p.suffix in {'.py','.json'} and '__pycache__' not in p.parts]
        paths += [full,focused,boundary]+[PROJECT_ROOT/p for p in ('backend/.env.example','backend/requirements.lock.txt','README.md','backend/README.md','docs/README.md','docs/03-milestones.md',
            'docs/07-m3-agent-design.md','docs/09-model-runtime-design.md','docs/contracts/m3-agent-contract.json','docs/acceptance/M3-T04b1-verification.md')]
        report=dict(passed=True,recorded_at=datetime.now(timezone.utc).isoformat(),run_id=run_id,scope='T04b1 opt-in model-driven method graph, not numerical autonomous diagnosis',
            actual_process=dict(scope_clarified_before_model=True,real_cloud_decision_drove_retrieval=True,real_cloud_quotes_grounded=True,
                worker_killed_after_model_receipts=True,recovered_with_external_disabled_and_embedding_unreachable=True,
                model_rounds=2,logical_tool_calls=1,reservations=3,cancellation_fenced_late_decision=True,withdrawal_hides_draft=True),
            cloud=dict(fixture_passed=True,model_rounds=restored['model_rounds'],graph=restored['graph']),
            local=dict(fixture_passed=local_passed,status=local['status'],error_code=local['error_code'],model_rounds=local['model_rounds'],
                missing_requirements=local['draft']['missing_requirements'] if local['draft'] else [],automatic_cloud_fallback=False),
            full_backend_tests={k:suites[0].attrib[k] for k in ('tests','failures','errors','skipped','time')},model_graph_tests={k:suites[1].attrib[k] for k in ('tests','failures','errors','skipped','time')},
            boundary_tests={k:suites[2].attrib[k] for k in ('tests','failures','errors','skipped','time')},source_sha256=sha256(raw).hexdigest(),
            generated_style='validated_exact_source_quotes_only',diagnostic_release=False,relevance_qualified=False,factory_trial_approved=False,
            limitations=['Synthetic self-authored English fixture; explicit application review is not independent engineering approval',
                'Method graph only, with one allowed retrieval candidate and at most two model phases; numerical tasks remain rules-based',
                'Phase-specific prompt v2 differs from T04a; a local success would not establish general improvement or qualification',
                'Hosted consumer disabled for task-ID isolation; API and owned probes alternate the same lease; cancellation injects old-executor lease loss',
                'No automatic model retry or provider fallback; copied text is still untrusted context, not a maintenance recommendation'],
            source_fingerprints={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/'experiments/m3-t04b1-model-graph.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
        print(json.dumps(dict(passed=True,cloud_fixture_passed=True,local_fixture_passed=local_passed,full_backend_tests=report['full_backend_tests'],model_graph_tests=report['model_graph_tests'])))
    finally:
        for worker in workers:
            if worker['child'].poll() is None:finish_worker(worker,kill=True)
            elif not worker['output'].closed:worker['output'].close()
        stop_api()


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(f'Model method graph verification failed ({type(error).__name__}); inspect private logs; credentials hidden') from None
