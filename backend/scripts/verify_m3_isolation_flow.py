"""Real Windows API/child lifetime, cancellation and deadline verification; dedicated test DB only."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
import platform
import re
import runpy
import shutil
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


class ProcessHandle:
    """Keep a handle to the observed owned process so PID reuse cannot change the target."""
    def __init__(self,pid):
        import _winapi
        self.handle=_winapi.OpenProcess(0x100001,False,pid);self.pid=pid
    def alive(self):
        import _winapi
        return _winapi.WaitForSingleObject(self.handle,0)==258
    def wait(self,seconds=5):
        import _winapi
        return _winapi.WaitForSingleObject(self.handle,int(seconds*1000))==0
    def kill(self):
        import _winapi
        if self.alive():_winapi.TerminateProcess(self.handle,86)
    def close(self):
        import _winapi
        _winapi.CloseHandle(self.handle)


def main():
    if os.name!='nt':raise RuntimeError('This lifetime receipt is validated on Windows only')
    test=setting('VIBRADIAG_TEST_DATABASE_URL');migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test or not migration or make_url(test).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:raise RuntimeError('Dedicated test database required')
    run_id=uuid4().hex;private=PROJECT_ROOT/'.runtime/m3-isolation-flow'/run_id;private.mkdir(parents=True)
    data_root=private/'data'
    os.environ.update(VIBRADIAG_DATABASE_URL=test,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_COOKIE_SECURE='false',
        VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_AGENT_WORKER_ENABLED='true',VIBRADIAG_DATA_ROOT=str(data_root),VIBRADIAG_NUMERIC_BACKEND='process')
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    org,actor,asset=[uuid4() for _ in range(3)];password=str(uuid4())+str(uuid4())
    with transaction() as connection:
        connection.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic process isolation '+run_id))
        connection.execute(text("INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,'verification',:hash)"),dict(id=actor,org=org,hash=password_hasher.hash(password)))
        connection.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,'engineer',:id)"),dict(id=actor,org=org))
        connection.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:id,:org,:code,'Synthetic isolation asset','motor')"),dict(id=asset,org=org,code=run_id))
        connection.execute(text('INSERT INTO asset_grants VALUES (:org,:actor,:asset,true,true)'),dict(org=org,actor=actor,asset=asset))
        tables=connection.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'")).scalar_one()
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}';process=None;api=None;log=None;launch=0;handles=[]
    def stop():
        nonlocal process,api,log
        if api is not None:api.kill();api.wait();api.close();api=None
        if process is not None:
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
                process.wait(timeout=5)
            process=None
        if log is not None:log.close();log=None
    def start(probe=None,timeout=15000):
        nonlocal process,api,log,launch
        if probe:os.environ['VIBRADIAG_NUMERIC_TEST_SCENARIO']=probe
        else:os.environ.pop('VIBRADIAG_NUMERIC_TEST_SCENARIO',None)
        os.environ['VIBRADIAG_NUMERIC_TIMEOUT_MS']=str(timeout)
        launch+=1;path=private/f'api-{launch}.log';log=path.open('w',encoding='utf8')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],cwd=PROJECT_ROOT,stdout=log,stderr=log,creationflags=0x08000000)
        for _ in range(150):
            if process.poll() is not None:raise RuntimeError('Owned API startup failed')
            try:
                if httpx.get(url+'/',timeout=1).status_code==200:break
            except httpx.HTTPError:pass
            sleep(.1)
        else:raise RuntimeError('Owned API startup timeout')
        pids=re.findall(r'Started server process \[(\d+)\]',path.read_text(encoding='utf8'))
        if not pids:raise RuntimeError('Owned API PID not observed')
        api=ProcessHandle(int(pids[-1]))
    def child():
        deadline=monotonic()+20
        while monotonic()<deadline:
            for ready in (data_root/'compute').glob('*/ready.json'):
                info=json.loads(ready.read_text(encoding='utf8'))
                try:handle=ProcessHandle(info['pid'])
                except OSError:continue
                if handle.alive():handles.append(handle);return handle,ready.parent
                handle.close()
            sleep(.025)
        raise RuntimeError('Numerical child not observed')
    try:
        start()
        with httpx.Client(base_url=url,timeout=120) as client:
            auth=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username='verification',password=password));auth.raise_for_status()
            client.headers['x-csrf-token']=auth.json()['csrf_token']
            def post(path,body,key=None):
                response=client.post(path,json=body,headers={'Idempotency-Key':key or str(uuid4())});response.raise_for_status();return response.json()
            def get_task(identifier):
                response=client.get('/api/v1/agent-tasks/'+identifier);response.raise_for_status();return response.json()
            def wait_task(identifier,states):
                deadline=monotonic()+60
                while monotonic()<deadline:
                    task=get_task(identifier)
                    if task['status'] in states:return task
                    if task['status'] in {'failed','timed_out','budget_exhausted','superseded'}:raise RuntimeError('Unexpected task state')
                    sleep(.05)
                raise RuntimeError('Task wait timeout')
            def upload(count):
                t=np.arange(count)/12000;x=2*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
                raw=('value\n'+'\n'.join(format(v,'.17g') for v in x)+'\n').encode()
                response=client.post('/api/v1/measurements',files={'file':('synthetic.csv',raw,'text/csv')},data={'metadata':json.dumps(dict(
                    sampling_rate_hz=12000,acceleration_unit='m/s2',rpm=1800,asset_id=str(asset),source_type='synthetic'))})
                response.raise_for_status();record=response.json()
                confirmation=post('/api/v1/measurements/'+record['id']+'/confirmations',dict(revision_id=record['current_revision_id']))
                return record,confirmation['id'],sha256(raw).hexdigest()
            record,confirmation,digest=upload(120000)
            body=dict(goal='inspect_signal',description='Synthetic numerical subprocess',measurement_revision_id=record['current_revision_id'])
            task=post('/api/v1/agent-tasks',body);complete=wait_task(task['id'],{'completed'})
            source=client.get('/api/v1/analyses/'+complete['calls'][0]['resource_id']);source.raise_for_status()
            execution=source.json()['result_summary']['compute_execution']
            assert execution['backend']=='process' and not any(k.startswith(('VIBRADIAG','DEEPSEEK')) for k in execution['environment_keys'])
            large,large_confirmation,large_digest=upload(1000000)
            stop();start('hang',15000)
            crash=post('/api/v1/agent-tasks',dict(body,measurement_revision_id=large['current_revision_id']))
            owned,orphan_directory=child();assert owned.alive()
            observed=get_task(crash['id']);assert observed['status']=='running' and observed['calls']
            api.kill();assert api.wait() and owned.wait(5)
            assert not owned.alive()
            stop();start()
            recovered=wait_task(crash['id'],{'interrupted'});spent=recovered['budget']['active_ms'];rounds=0
            while recovered['status']=='interrupted' and rounds<3:
                post('/api/v1/agent-tasks/'+crash['id']+'/resume',dict(expected_version=recovered['version'],question_id=recovered['question']['id'],answer='Explicit interrupted-child reconciliation'))
                recovered=wait_task(crash['id'],{'interrupted','completed'});rounds+=1
            assert recovered['status']=='completed' and recovered['budget']['active_ms']>=spent
            assert len(recovered['calls'])==2 and len(recovered['budget']['reservations'])==2
            # Only the known UUID-owned, dead-job directory in this verification's private root is removed.
            marker=json.loads((orphan_directory/'request.json').read_text(encoding='utf8'))
            assert marker['nonce']==orphan_directory.name and orphan_directory.resolve().parent==(data_root/'compute').resolve()
            assert orphan_directory.resolve().is_relative_to((PROJECT_ROOT/'.runtime').resolve()) and not orphan_directory.is_symlink()
            shutil.rmtree(orphan_directory)
            print('Parent API death terminated its numerical child; durable task recovery passed',flush=True)
            stop();start('hang',4000)
            cancel=post('/api/v1/agent-tasks',body);cancel_child,_=child();current=get_task(cancel['id'])
            started=monotonic();cancelled=post('/api/v1/agent-tasks/'+cancel['id']+'/cancel',dict(expected_version=current['version']))
            cancel_seconds=monotonic()-started
            assert cancelled['status']=='cancelled' and cancel_child.wait(5) and cancelled['draft'] is None
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(post,'/api/v1/analyses',dict(measurement_revision_id=record['current_revision_id'],confirmation_id=confirmation))
                timeout_child,_=child();failed=future.result()
            assert timeout_child.wait(5) and failed['status']=='failed' and failed['error_code']=='NUMERIC_DEADLINE_EXCEEDED' and not failed['artifacts']
            assert client.get('/').status_code==200
            stop();start()
            final=post('/api/v1/analyses',dict(measurement_revision_id=record['current_revision_id'],confirmation_id=confirmation))
            assert final['status']=='completed' and final['result_summary']['compute_execution']['backend']=='process'
            client.post('/api/v1/auth/logout').raise_for_status()
            print('HTTP cancellation, deadline termination and slot reuse passed',flush=True)
        tests='experiments/m3-t02b3-tests.xml';focused='experiments/m3-t02b3-process-tests.xml'
        suite=ElementTree.parse(PROJECT_ROOT/tests).getroot().find('testsuite');special=ElementTree.parse(PROJECT_ROOT/focused).getroot().find('testsuite')
        assert all(int(s.attrib[k])==0 for s in (suite,special) for k in ('failures','errors','skipped'))
        cases=[c for c in suite.findall('testcase') if c.attrib['classname'].endswith('test_numeric_process')]
        assert len(cases)==13 and int(special.attrib['tests'])==13
        paths=[p for folder in ('backend/src','backend/tests','backend/scripts','backend/migrations') for p in (PROJECT_ROOT/folder).rglob('*.py') if '__pycache__' not in p.parts]
        paths += [PROJECT_ROOT/p for p in (tests,focused,'backend/.env.example','backend/requirements.lock.txt','README.md','backend/README.md',
            'docs/README.md','docs/03-milestones.md','docs/07-m3-agent-design.md','docs/acceptance/M3-T02b3-verification.md')]
        report=dict(passed=True,scope='M3-T02b3 supervised pure numerical subprocesses',run_id=run_id,business_tables=tables,checkpoint_tables=4,
            migration_head='0014_agent_graph',factory_data_used=False,model_called=False,numerical_backend='process',
            small_input=dict(sample_count=120000,sha256=digest,execution=execution),
            parent_death=dict(sample_count=1000000,sha256=large_digest,child_handle_observed_alive=True,child_exited_after_api_death=True,
                task_status=recovered['status'],resume_rounds=rounds,call_states=[c['state'] for c in recovered['calls']],budget=recovered['budget'],private_dead_job_copy_removed_by_verifier=True),
            cancellation=dict(status=cancelled['status'],child_terminated=True,http_seconds=cancel_seconds,no_draft=True),
            deadline=dict(configured_ms=4000,source_status=failed['status'],error_code=failed['error_code'],child_terminated=True,api_remained_available=True,subsequent_source_status=final['status']),
            environment=dict(python=platform.python_version(),platform=platform.platform()),new_real_process_tests=len(cases),
            full_backend_tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},
            real_process_tests={k:special.attrib[k] for k in ('tests','failures','errors','skipped','time')},
            limitations=['Single API admission owner and shared numerical capacity two; no independent credentialed workers',
                'Windows Job Object lifetime verified; Linux parent-death implementation not validated here',
                'Subprocesses run trusted code under the same OS user, not a filesystem/network security sandbox',
                'Deadline covers numerical child lifetime with independent watchdog; parent source preparation/persistence can outlast it',
                'Forced API death may leave private IPC files; verifier removed its known dead job, not a production retention policy',
                'Legacy fault-injection suite uses a dedicated-test-database-only inline backend; 13 cases and this HTTP verification use real children'],
            source_sha256={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/'experiments/m3-t02b3-isolation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
        print(json.dumps({k:report[k] for k in ('passed','full_backend_tests','real_process_tests','business_tables')}))
    finally:
        stop()
        for handle in handles:handle.close()


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(f'Isolation HTTP verification failed ({type(error).__name__}); inspect private logs; credentials hidden') from None
