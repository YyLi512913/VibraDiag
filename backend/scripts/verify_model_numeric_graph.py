"""Real numerical model graph, deterministic tools, process recovery and provenance invalidation."""
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
import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT,BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine,transaction
from vibradiag.services.identity_service import password_hasher,authenticate
from vibradiag.services.tool_profile_service import create_profile
from vibradiag.services.diagnostic_context_service import create_component,create_bearing_revision,create_context


def main():
    test=setting('VIBRADIAG_TEST_DATABASE_URL');migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test or not migration or make_url(test).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:raise RuntimeError('Dedicated test database required')
    os.environ.update(VIBRADIAG_DATABASE_URL=test,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_COOKIE_SECURE='false',
        VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_AGENT_WORKER_ENABLED='false',VIBRADIAG_NUMERIC_EXECUTOR='model',VIBRADIAG_FORMAL_LLM_PROVIDER='deepseek')
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    run_id=uuid4().hex;private=PROJECT_ROOT/'.runtime/model-numeric'/run_id;private.mkdir(parents=True)
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
        child=subprocess.Popen([sys.executable,str(BACKEND_ROOT/'scripts/m3_numeric_graph_probe.py'),str(job)],cwd=PROJECT_ROOT,
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
            if worker['child'].poll() is not None:raise RuntimeError('Task exited before final decision commit')
            sleep(.05)
        raise RuntimeError('Real numerical decision did not reach pause')
    tasks='/api/v1/agent-tasks';asset=uuid4();point=uuid4()
    original_external=setting('VIBRADIAG_ALLOW_EXTERNAL_LLM','false')
    try:
        with transaction() as c:
            c.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:id,:org,:code,'Synthetic numerical motor','motor')"),dict(id=asset,org=org,code=run_id))
            c.execute(text("INSERT INTO measurement_points(id,organization_id,asset_id,point_code,name) VALUES (:id,:org,:asset,'DE','Synthetic DE')"),dict(id=point,org=org,asset=asset))
            c.execute(text('INSERT INTO asset_grants VALUES (:org,:user,:asset,true,true)'),dict(org=org,user=users['author'],asset=asset))
        start_api()
        t=np.arange(48322)/12000;signal=2*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
        raw=('value\n'+'\n'.join(format(v,'.17g') for v in signal)+'\n').encode()
        metadata=dict(sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(asset),measurement_point_id=str(point),rpm=1800.,source_type='synthetic')
        response=clients['author'].post('/api/v1/measurements',files={'file':('synthetic-am.csv',raw,'text/csv')},data={'metadata':json.dumps(metadata)})
        response.raise_for_status();measurement=response.json();revision=measurement['current_revision_id']
        confirmation=post('author','/api/v1/measurements/'+measurement['id']+'/confirmations',dict(revision_id=revision))
        with transaction() as c:principal=authenticate(c,clients['author'].cookies.get('vibradiag_session'))
        provenance=dict(kind='synthetic_exact',reference='Analytic AM signal and bearing geometry, no field data',version='1')
        component=create_component(dict(asset_id=asset,component_code=run_id,name='Synthetic target'),principal)
        bearing=create_bearing_revision(dict(component_id=component,expected_current_revision_id=None,parameterization='geometry',geometry=dict(rolling_elements=8,
            rolling_element_diameter=10.,pitch_diameter=40.,length_unit='mm',contact_angle_deg=0.,bearing_design='single_row_ball',source=provenance),reason='Self-authored numerical fixture'),principal)
        create_context(dict(measurement_revision_id=revision,component_id=component,bearing_revision_id=bearing,conditions=dict(rpm_basis='synthetic_exact',rpm_source=provenance,
            rpm_uncertainty_rpm=0.,rpm_uncertainty_source=provenance,speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),
            acquisition=dict(low_hz=100.,high_hz=5000.,source=provenance),source_reference=provenance,reason='Synthetic conditions, geometry uncertainty deliberately absent'),principal)
        create_profile(dict(asset_id=asset,sampling_rate_hz=12000,band_low_hz=2000,band_high_hz=4000,modulation_low_hz=10,modulation_high_hz=500,
            selection_reason='Fixed analytic AM test, not industrial qualification'),principal)
        def create(goal='bearing_observations'):
            return post('author',tasks,dict(goal=goal,measurement_revision_id=revision,description='For this synthetic bearing signal, run basic analysis, exploratory envelope analysis and ideal bearing frequencies when offered, then finish. Preserve missing uncertainty and do not diagnose a fault.'))
        task=create();assert task['executor']=='model_numeric_v1'
        worker=launch_worker(task,'pause_after_final_decision');wait_ready(worker)
        with transaction() as c:
            assert c.execute(text("SELECT count(*) FROM agent_model_rounds WHERE task_id=:id AND state='completed'"),dict(id=UUID(task['id']))).scalar_one()==4
            assert c.execute(text('SELECT count(*) FROM agent_calls WHERE task_id=:id'),dict(id=UUID(task['id']))).scalar_one()==3
        finish_worker(worker,kill=True);start_api()
        finish_worker(launch_worker(task,'recover'));interrupted=get('author',tasks+'/'+task['id'])
        assert interrupted['status']=='interrupted' and len(interrupted['budget']['reservations'])==7
        post('author',tasks+'/'+task['id']+'/resume',dict(expected_version=interrupted['version'],question_id=interrupted['question']['id'],answer='Recover committed numerical decisions and actual tools'))
        os.environ['VIBRADIAG_ALLOW_EXTERNAL_LLM']='false'
        try:finish_worker(launch_worker(task,'execute'))
        finally:stop_api();os.environ['VIBRADIAG_ALLOW_EXTERNAL_LLM']=original_external;start_api()
        restored=get('author',tasks+'/'+task['id'])
        assert restored['status']=='completed_degraded' and restored['model_called'] and restored['formal_graph_executed']
        assert len(restored['model_rounds'])==4 and len(restored['calls'])==3 and len(restored['budget']['reservations'])==7
        assert {h['action'] for h in restored['calls']}=={'analyze_basic','analyze_envelope','calculate_bearing_frequencies'}
        assert 'diagnostic_profile_not_qualified' in restored['draft']['missing_requirements'] and 'matching_not_completed' in restored['draft']['missing_requirements']
        assert restored['draft']['publication_state']=='unpublished_draft'
        for obs in restored['draft']['observations']:assert obs['citations'][0]['id'] in {h['resource_id'] for h in restored['calls']}
        frequency=next(h for h in restored['calls'] if h['action']=='calculate_bearing_frequencies')
        actual_frequency=get('author','/api/v1/tool-runs/'+frequency['resource_id'])
        assert actual_frequency['result']['summary']['frequencies']['BPFO']['frequency_hz']==90.
        cancelled=create('inspect_signal');worker=launch_worker(cancelled,'pause_after_decision_release_lease');wait_ready(worker);start_api()
        current=get('author',tasks+'/'+cancelled['id']);post('author',tasks+'/'+cancelled['id']+'/cancel',dict(expected_version=current['version']))
        (worker['folder']/'release').write_text('release',encoding='utf8');finish_worker(worker)
        cancelled_result=get('author',tasks+'/'+cancelled['id']);assert cancelled_result['status']=='cancelled' and cancelled_result['calls']==[] and cancelled_result['draft'] is None
        with transaction() as c:c.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(confirmation['id'])))
        invalid=get('author',tasks+'/'+task['id']);assert invalid['draft'] is None and not invalid['draft_validity']['confirmations_active']
        paths=[PROJECT_ROOT/'experiments/m3-t04b2-tests.xml',PROJECT_ROOT/'experiments/m3-t04b2-numeric-tests.xml']
        suites=[ElementTree.parse(p).getroot().find('testsuite') for p in paths]
        assert all(int(s.attrib[k])==0 for s in suites for k in ('failures','errors','skipped'))
        paths += [p for directory in ('backend/src','backend/tests','backend/scripts','backend/migrations','backend/config') for p in (PROJECT_ROOT/directory).rglob('*') if p.suffix in {'.py','.json'} and '__pycache__' not in p.parts]
        paths += [PROJECT_ROOT/p for p in ('backend/.env.example','backend/requirements.lock.txt','README.md','backend/README.md','docs/README.md','docs/03-milestones.md','docs/07-m3-agent-design.md','docs/09-model-runtime-design.md','docs/contracts/m3-agent-contract.json','docs/acceptance/M3-T04b2-verification.md')]
        report=dict(passed=True,recorded_at=datetime.now(timezone.utc).isoformat(),run_id=run_id,scope='Opt-in numerical model orchestration over real deterministic tools; no diagnostic qualification',
            actual_process=dict(real_cloud_decisions_drove_tools=True,worker_killed_after_final_model_receipt=True,recovered_with_external_disabled=True,
                model_rounds=4,logical_tool_calls=3,reservations=7,missing_uncertainty_blocked_matching=True,cancellation_fenced_late_decision=True,revocation_hides_draft=True),
            cloud=dict(fixture_passed=True,model_rounds=restored['model_rounds'],calls=restored['calls'],graph=restored['graph']),
            observed_bpfo_hz=90.,source_sha256=sha256(raw).hexdigest(),source_type='synthetic',full_backend_tests=suites[0].attrib,numeric_graph_tests=suites[1].attrib,
            diagnostic_release=False,factory_trial_approved=False,local_model_tested_here=False,
            limitations=['Developer-authored synthetic data, no independent engineering review or field trial','Server assembles cited numerical draft; no free-form model diagnostic generation',
                'At most five tools and six decisions in this graph, bounded by existing global budget','No local/cloud quality ranking; local prior refusal remains recorded in T04b1',
                'API consumer disabled for task-ID isolation; probes use same claim/execute/recover and alternate the single-process lease'],
            source_fingerprints={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/'experiments/m3-t04b2-model-graph.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
        print(json.dumps(dict(passed=True,model_rounds=4,logical_tool_calls=3,full_tests=report['full_backend_tests'],numeric_tests=report['numeric_graph_tests'])))
    finally:
        for worker in workers:
            if worker['child'].poll() is None:finish_worker(worker,kill=True)
            elif not worker['output'].closed:worker['output'].close()
        stop_api()


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(f'Numerical model graph verification failed ({type(error).__name__}); inspect private logs; credentials hidden') from None
