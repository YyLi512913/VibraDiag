"""Real loopback HTTP + hosted task worker + owned API termination; synthetic inputs only."""
from hashlib import sha256
from importlib.metadata import version
import json
import os
import platform
import re
import runpy
import socket
import subprocess
import sys
from time import monotonic,sleep
from uuid import uuid4
from xml.etree import ElementTree
import httpx
import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT,BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine,transaction
from vibradiag.services.identity_service import password_hasher


def main(*,receipt_path='experiments/m3-t02b2-http.json',tests_report='experiments/m3-t02b2-tests.xml'):
    test=setting('VIBRADIAG_TEST_DATABASE_URL');migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test or not migration or make_url(test).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:
        raise RuntimeError('Separate test database required')
    run_id=uuid4().hex;private=PROJECT_ROOT/'.runtime/m3-task-flow'/run_id;private.mkdir(parents=True)
    os.environ.update(VIBRADIAG_DATABASE_URL=test,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_COOKIE_SECURE='false',
        VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_AGENT_WORKER_ENABLED='true',VIBRADIAG_DATA_ROOT=str(private/'data'))
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    org,actor,asset=[uuid4() for _ in range(3)];password=str(uuid4())+str(uuid4())
    with transaction() as conn:
        conn.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic task acceptance '+run_id))
        conn.execute(text("INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,'verification',:hash)"),dict(id=actor,org=org,hash=password_hasher.hash(password)))
        conn.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,'engineer',:id)"),dict(id=actor,org=org))
        conn.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:id,:org,:code,'Synthetic task asset','motor')"),dict(id=asset,org=org,code=run_id))
        conn.execute(text('INSERT INTO asset_grants VALUES (:org,:actor,:asset,true,true)'),dict(org=org,actor=actor,asset=asset))
        tables=conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'")).scalar_one()
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}';process=None;log=None;launch=0;owned_pids=[]
    def start():
        nonlocal process,log,launch
        launch+=1;path=private/f'api-{launch}.log';log=path.open('w',encoding='utf8')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],
            cwd=PROJECT_ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(150):
            if process.poll() is not None:raise RuntimeError('API startup failed')
            try:
                if httpx.get(url+'/',timeout=1).status_code==200:break
            except httpx.HTTPError:pass
            sleep(.1)
        else:raise RuntimeError('API startup timeout')
        pids=re.findall(r'Started server process \[(\d+)\]',path.read_text(encoding='utf8'))
        if not pids:raise RuntimeError('Owned API identity unavailable')
        pid=int(pids[-1]);owned_pids.append(pid);return pid
    def stop():
        nonlocal process,log
        if process is not None:
            if process.poll() is None:process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            process=None
        if log is not None:log.close();log=None
    try:
        pid=start()
        with httpx.Client(base_url=url,timeout=180) as client:
            auth=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username='verification',password=password));auth.raise_for_status()
            client.headers['x-csrf-token']=auth.json()['csrf_token']
            def post(path,body,key=None):
                response=client.post(path,json=body,headers={'Idempotency-Key':key or str(uuid4())});response.raise_for_status()
                return response.json(),response.status_code
            def get(identifier):
                response=client.get('/api/v1/agent-tasks/'+identifier);response.raise_for_status();return response.json()
            def wait(identifier,statuses):
                deadline=monotonic()+90
                while monotonic()<deadline:
                    task=get(identifier)
                    if task['status'] in statuses:return task
                    if task['status'] in {'failed','cancelled','superseded','budget_exhausted','timed_out'}:raise RuntimeError('Unexpected terminal task')
                    sleep(.05)
                raise RuntimeError('Hosted task deadline exceeded')
            def upload(count):
                t=np.arange(count)/12000;x=2*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
                raw=('value\n'+'\n'.join(format(v,'.17g') for v in x)+'\n').encode()
                response=client.post('/api/v1/measurements',files={'file':('synthetic.csv',raw,'text/csv')},
                    data={'metadata':json.dumps(dict(sampling_rate_hz=12000,acceleration_unit='m/s2',rpm=1800,asset_id=str(asset),source_type='synthetic'))})
                response.raise_for_status();return response.json(),sha256(raw).hexdigest()
            record,digest=upload(120000)
            body=dict(goal='inspect_signal',description='Synthetic hosted task acceptance',measurement_revision_id=record['current_revision_id'])
            key=str(uuid4());task,status=post('/api/v1/agent-tasks',body,key);assert status==202
            paused=wait(task['id'],{'waiting_information'});assert paused['question']['code']=='input_confirmation_required'
            post('/api/v1/measurements/'+record['id']+'/confirmations',dict(revision_id=record['current_revision_id']))
            answer=dict(expected_version=paused['version'],question_id=paused['question']['id'],answer='Confirmed via measurement API')
            resume_key=str(uuid4());post('/api/v1/agent-tasks/'+task['id']+'/resume',answer,resume_key)
            replay,status=post('/api/v1/agent-tasks/'+task['id']+'/resume',answer,resume_key);assert status==200
            complete=wait(task['id'],{'completed'})
            replay,status=post('/api/v1/agent-tasks',body,key);assert status==200 and replay['id']==task['id']
            assert len(complete['calls'])==1 and complete['calls'][0]['state']=='completed' and complete['draft']
            assert not complete['model_called'] and complete['formal_graph_executed']
            assert complete['graph']['version']=='rules-graph-v1' and complete['graph']['checkpoint_count']>0
            print('Hosted HTTP acceptance, confirmation pause/resume and idempotency passed',flush=True)
            large,large_digest=upload(1000000)
            post('/api/v1/measurements/'+large['id']+'/confirmations',dict(revision_id=large['current_revision_id']))
            crash_body=dict(goal='inspect_signal',description='Synthetic owned-process interruption',measurement_revision_id=large['current_revision_id'])
            crash_key=str(uuid4());crash,status=post('/api/v1/agent-tasks',crash_body,crash_key);assert status==202
            observed=False
            for _ in range(2000):
                with transaction() as conn:
                    observed=conn.execute(text("SELECT status='running' AND EXISTS(SELECT 1 FROM agent_calls WHERE task_id=:id AND state='running') AND EXISTS(SELECT 1 FROM analysis_runs a WHERE a.measurement_revision_id=agent_tasks.measurement_revision_id AND a.requested_by=agent_tasks.requested_by AND a.status='running') FROM agent_tasks WHERE id=:id"),dict(id=crash['id'])).scalar_one()
                if observed:break
                if get(crash['id'])['status'] in {'completed','failed'}:break
                sleep(.002)
            if not observed:raise RuntimeError('Could not observe in-flight owned task')
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(pid),'/F'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            else:process.kill()
            stop();start()
            recovered=wait(crash['id'],{'interrupted'});spent=recovered['budget']['active_ms']
            replay,status=post('/api/v1/agent-tasks',crash_body,crash_key);assert status==200 and replay['id']==crash['id'] and replay['status']=='interrupted'
            rounds=0
            while recovered['status']=='interrupted' and rounds<3:
                post('/api/v1/agent-tasks/'+crash['id']+'/resume',dict(expected_version=recovered['version'],question_id=recovered['question']['id'],answer='Explicit interruption recovery'))
                recovered=wait(crash['id'],{'interrupted','completed'});rounds+=1
            assert recovered['status']=='completed' and recovered['budget']['active_ms']>=spent
            assert len(recovered['calls'])==2 and len(recovered['budget']['reservations'])==2
            assert recovered['calls'][0]['state']=='interrupted' and recovered['calls'][1]['state']=='completed'
            client.post('/api/v1/auth/logout').raise_for_status()
            print('Owned API termination, durable ledger recovery and linked retry passed',flush=True)
        suite=ElementTree.parse(PROJECT_ROOT/tests_report).getroot().find('testsuite')
        assert all(int(suite.attrib[k])==0 for k in ('failures','errors','skipped'))
        graph_cases=[case for case in suite.findall('testcase') if case.attrib['classname'].endswith('test_durable_rules_graph')]
        assert len(graph_cases)==14
        focused_path='experiments/m3-t02b2-task-tests.xml'
        focused=ElementTree.parse(PROJECT_ROOT/focused_path).getroot().find('testsuite')
        assert int(focused.attrib['tests'])==19 and all(int(focused.attrib[k])==0 for k in ('failures','errors','skipped'))
        paths=[p for folder in ('backend/src','backend/migrations','backend/tests','backend/scripts','frontend/src') for p in (PROJECT_ROOT/folder).rglob('*')
            if p.is_file() and p.suffix in {'.py','.ts','.vue','.css'} and '__pycache__' not in p.parts]
        paths += [PROJECT_ROOT/p for p in (tests_report,focused_path,'backend/pyproject.toml','backend/requirements.lock.txt','backend/.env.example',
            'README.md','backend/README.md','docs/README.md','docs/03-milestones.md','docs/07-m3-agent-design.md',
            'docs/acceptance/M3-T02b2-verification.md')]
        report=dict(passed=True,run_id=run_id,scope='M3-T02b2 task-bound durable rules graph',business_tables=tables,migration_head='0014_agent_graph',
            versions={name:version(name) for name in ('langgraph','langgraph-checkpoint','langgraph-checkpoint-postgres','psycopg')},
            production_task_graph_integrated=True,synthetic_only=True,factory_data_used=False,executor='rules_graph_v1',model_called=False,formal_graph_executed=True,production_hard_cancel_implemented=False,
            published_knowledge_integrated=False,formal_diagnostic_release_approved=False,
            small_case=dict(sample_count=120000,sha256=digest,status=complete['status'],calls=len(complete['calls']),budget=complete['budget'],graph=complete['graph'],pause_resume=True,idempotency=True),
            process_recovery=dict(sample_count=1000000,sha256=large_digest,observed_running=bool(observed),owned_process_restarted=True,
                same_task_replay=True,resume_rounds=rounds,call_states=[c['state'] for c in recovered['calls']],budget=recovered['budget'],graph=recovered['graph'],status=recovered['status']),
            environment=dict(python=platform.python_version(),platform=platform.platform()),
            full_backend_tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},
            final_task_tests={k:focused.attrib[k] for k in ('tests','failures','errors','skipped','time')},new_graph_tests=len(graph_cases),checkpoint_tables=4,
            limitations=['Durable rules graph only; no model decision or published knowledge','Cooperative cancellation and soft deadline; no hard compute termination',
                'Single API lease, one hosted task consumer, shared M1/M2 numerical capacity two','Recovery conservatively charges time since persisted tick including downtime; not a latency benchmark'],
            source_sha256={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/receipt_path).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(json.dumps({key:report[key] for key in ('passed','business_tables','full_backend_tests','model_called','formal_graph_executed')}))
    finally:stop()


if __name__=='__main__':
    try:
        from verification_cli import verification_options
        main(**verification_options('experiments/m3-t02b2-http.json','experiments/m3-t02b2-tests.xml'))
    except Exception:raise SystemExit('Task HTTP verification failed; inspect private runtime logs (credentials hidden)') from None
