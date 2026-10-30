"""Isolated loopback HTTP, resource and real process-restart verification (synthetic only)."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
import platform
import re
import runpy
import socket
import subprocess
import sys
import time
from uuid import UUID, uuid4
from xml.etree import ElementTree
import httpx
import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.core.paths import BACKEND_ROOT, PROJECT_ROOT
from vibradiag.infrastructure.persistence.database import engine, transaction
from vibradiag.services.identity_service import password_hasher, authenticate
from vibradiag.services.tool_profile_service import create_profile
from benchmark_m1 import memory_info


def main(*, receipt_path='experiments/m2-t02b2-http-resources.json', tests_report='experiments/m2-t02b2-tests.xml'):
    test_url=setting('VIBRADIAG_TEST_DATABASE_URL')
    migration_url=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test_url or not migration_url or make_url(test_url).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:
        raise RuntimeError('Separate test database is required')
    os.environ.update(VIBRADIAG_DATABASE_URL=test_url,VIBRADIAG_MIGRATION_DATABASE_URL=migration_url,
        VIBRADIAG_COOKIE_SECURE='false',VIBRADIAG_ENABLE_PROTOTYPE='false')
    run_id=uuid4().hex
    private=PROJECT_ROOT/'.runtime/m2-tool-flow'/run_id
    private.mkdir(parents=True)
    os.environ['VIBRADIAG_DATA_ROOT']=str(private/'data')
    engine.cache_clear()
    runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    org, actor, asset=uuid4(),uuid4(),uuid4()
    password=str(uuid4())+str(uuid4())
    with transaction() as conn:
        conn.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='M2 synthetic verification '+run_id))
        conn.execute(text("INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,'verification',:hash)"),
            dict(id=actor,org=org,hash=password_hasher.hash(password)))
        conn.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,'engineer',:id)"),dict(id=actor,org=org))
        conn.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:id,:org,:code,'Synthetic verification','motor')"),
            dict(id=asset,org=org,code=run_id))
        conn.execute(text('INSERT INTO asset_grants VALUES (:org,:actor,:asset,true,true)'),dict(org=org,actor=actor,asset=asset))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    process=None; log=None; launch_no=0

    def stop():
        nonlocal process, log
        if process is not None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            process=None
        if log is not None: log.close(); log=None

    def start():
        nonlocal process, log, launch_no
        launch_no+=1
        log_path=private/f'api-{launch_no}.log'
        log=log_path.open('w',encoding='utf8')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],
            cwd=PROJECT_ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(100):
            if process.poll() is not None: raise RuntimeError('Verification API failed to start')
            try:
                if httpx.get(url+'/',timeout=1).status_code==200:
                    break
            except httpx.HTTPError: pass
            time.sleep(.1)
        else: raise RuntimeError('API startup timeout')
        matches=re.findall(r'Started server process \[(\d+)\]',log_path.read_text(encoding='utf8'))
        if not matches: raise RuntimeError('Cannot identify owned API process')
        return int(matches[-1])

    try:
        api_pid=start()
        with httpx.Client(base_url=url,timeout=120) as client:
            auth=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username='verification',password=password))
            auth.raise_for_status(); client.headers['x-csrf-token']=auth.json()['csrf_token']
            with transaction() as conn:
                principal=authenticate(conn,client.cookies.get('vibradiag_session'))
            profile=create_profile(dict(asset_id=asset,sampling_rate_hz=12000,band_low_hz=2000,band_high_hz=4000,
                modulation_low_hz=10,modulation_high_hz=500,selection_reason='Fixed synthetic AM verification'),principal)
            samples=[]; performances=[]
            def upload(count):
                t=np.arange(count)/12000
                x=2*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
                raw=('value\n'+'\n'.join(format(v,'.17g') for v in x)+'\n').encode()
                response=client.post('/api/v1/measurements',files={'file':('synthetic-am.csv',raw,'text/csv')},
                    data={'metadata':json.dumps(dict(asset_id=str(asset),sampling_rate_hz=12000,acceleration_unit='m/s2',
                        source_type='synthetic',source_note='M2 local HTTP verification, no field acquisition'))})
                response.raise_for_status(); measurement=response.json()
                confirmation=client.post(f"/api/v1/measurements/{measurement['id']}/confirmations",json={'revision_id':measurement['revision']['id']})
                confirmation.raise_for_status()
                samples.append(dict(sample_count=count,sha256=sha256(raw).hexdigest(),byte_size=len(raw)))
                return dict(tool_name='analyze_envelope',measurement_revision_id=measurement['revision']['id'],
                    confirmation_id=confirmation.json()['id'],profile_id=str(profile),mode='exploratory')
            def execute(body, key=None):
                started=time.perf_counter()
                response=client.post('/api/v1/tool-runs',json=body,headers={'Idempotency-Key':key or str(uuid4())})
                response.raise_for_status(); result=response.json()
                return result,dict(seconds=time.perf_counter()-started,http_status=response.status_code,status=result['status'],result_status=result['result_status'])
            typical=upload(120000); large=upload(1000000)
            for body in (typical,large):
                result, timing=execute(body)
                assert result['status']=='completed' and result['result_status']=='ok'
                assert result['result']['summary']['effective_segment']['sample_count'] in {119678,999678}
                performances.append(timing)
            print('HTTP 120000 and 1000000 point tool runs passed',flush=True)
            # Mixed M1/M2 actual executions share two slots; third request must be rejected.
            basic={k:large[k] for k in ('measurement_revision_id','confirmation_id')}
            with ThreadPoolExecutor(max_workers=2) as pool:
                tool_future=pool.submit(execute,large)
                basic_future=pool.submit(client.post,'/api/v1/analyses',json=basic,headers={'Idempotency-Key':str(uuid4())})
                observed=False
                for _ in range(500):
                    with transaction() as conn:
                        count=conn.execute(text("""SELECT (SELECT count(*) FROM analysis_runs WHERE requested_by=:actor AND status='running')
                            +(SELECT count(*) FROM tool_runs WHERE requested_by=:actor AND status='running')"""),dict(actor=actor)).scalar_one()
                    if count==2: observed=True; break
                    if tool_future.done() or basic_future.done(): break
                    time.sleep(.002)
                rejection=client.post('/api/v1/tool-runs',json=large,headers={'Idempotency-Key':str(uuid4())}) if observed else None
                mixed_tool=tool_future.result(); mixed_basic=basic_future.result()
            assert observed and rejection.status_code==429
            assert mixed_tool[0]['status']=='completed' and mixed_basic.status_code==201 and mixed_basic.json()['status']=='completed'
            memory=memory_info(api_pid)
            print('Actual mixed M1/M2 concurrency and shared-capacity 429 passed',flush=True)
            # Terminate only the owned process after durable acceptance, then restart.
            interrupted_key=str(uuid4())
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(execute,large,interrupted_key)
                interrupted_id=None
                for _ in range(1000):
                    with transaction() as conn:
                        interrupted_id=conn.execute(text("SELECT id FROM tool_runs WHERE requested_by=:actor AND status='running' ORDER BY started_at DESC LIMIT 1"),dict(actor=actor)).scalar_one_or_none()
                    if interrupted_id: break
                    if future.done(): break
                    time.sleep(.002)
                if not interrupted_id: raise RuntimeError('Failed to observe accepted running tool')
                # On Windows venv redirector may differ from Uvicorn PID. Kill verified owned child.
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(api_pid),'/F'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                else: process.kill()
                try: future.result()
                except httpx.HTTPError: pass
                else: raise RuntimeError('Interrupted request unexpectedly completed')
            stop(); start()
            replay, replay_timing=execute(large,interrupted_key)
            assert replay['tool_execution_id']==str(interrupted_id) and replay['status']=='interrupted' and replay_timing['http_status']==200
            retry=dict(large,retry_of=str(interrupted_id)); retried,retry_timing=execute(retry)
            assert retried['status']=='completed' and retried['tool_execution_id']!=str(interrupted_id)
            print('Owned API termination, restart recovery, same-key replay and explicit retry passed',flush=True)
            client.post('/api/v1/auth/logout').raise_for_status()
        paths=[str(p.relative_to(PROJECT_ROOT)).replace('\\','/') for base in ('backend/src/vibradiag','backend/migrations','backend/scripts','backend/tests')
            for p in (PROJECT_ROOT/base).rglob('*.py') if '__pycache__' not in p.parts]
        paths += [str(p.relative_to(PROJECT_ROOT)).replace('\\','/') for p in (BACKEND_ROOT/'migrations').rglob('*.sql')]
        paths += ['backend/requirements.lock.txt',tests_report]
        suite=ElementTree.parse(PROJECT_ROOT/tests_report).getroot().find('testsuite')
        assert int(suite.attrib['failures'])==int(suite.attrib['errors'])==int(suite.attrib['skipped'])==0
        receipt=dict(run_id=run_id,input_source='synthetic',factory_data_used=False,passed=True,
            environment=dict(python=platform.python_version(),platform=platform.platform(),**memory),
            transport='Loopback HTTP; POST includes acceptance, full input/outputs verification and persistence; upload/confirmation excluded',
            samples=samples,standalone_runs=performances,mixed_concurrency=dict(observed_two_running=observed,
                over_capacity_http_status=rejection.status_code,tool=mixed_tool[1],basic_http_status=mixed_basic.status_code),
            actual_process_recovery=dict(same_key_status=replay['status'],same_resource_id=True,explicit_retry=retry_timing),
            full_backend_tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},
            limitations=['Single local run per size, not a P95 benchmark','API peak working set excludes database/browser/model memory',
                'Memory peak covers standalone and mixed runs in the first API process before termination',
                'No field data, qualification publication or fault-effectiveness evaluation'],
            source_sha256={p:sha256((PROJECT_ROOT/p).read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/receipt_path).write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
        print(json.dumps({k:v for k,v in receipt.items() if k!='source_sha256'}))
    finally:
        stop()


if __name__=='__main__':
    try:
        from verification_cli import verification_options
        main(**verification_options('experiments/m2-t02b2-http-resources.json','experiments/m2-t02b2-tests.xml'))
    except Exception: raise SystemExit('M2 HTTP verification failed; inspect private runtime logs (credentials hidden)') from None
