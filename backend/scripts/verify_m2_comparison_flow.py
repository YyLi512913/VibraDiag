"""Isolated synthetic HTTP comparison, source gates and actual API restart receipt."""
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
from time import perf_counter, sleep
from uuid import uuid4
from xml.etree import ElementTree
import httpx
import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import make_url
from benchmark_m1 import memory_info
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT, BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine, transaction
from vibradiag.services.identity_service import password_hasher, authenticate
from vibradiag.services.diagnostic_context_service import create_component, create_bearing_revision, create_context
from vibradiag.services.tool_profile_service import create_profile
from vibradiag.services.comparison_service import create_declaration

SOURCE=dict(kind='synthetic_exact',reference='Exact synthetic AM comparison fixture; no field device or repair data',version='t04b-v1')


def main(*, receipt_path='experiments/m2-t04b-http-resources.json', tests_report='experiments/m2-t04b-tests.xml'):
    test=setting('VIBRADIAG_TEST_DATABASE_URL'); migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test or not migration or make_url(test).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:
        raise RuntimeError('Separate test database required')
    run_id=uuid4().hex; private=PROJECT_ROOT/'.runtime/m2-comparison-flow'/run_id;private.mkdir(parents=True)
    os.environ.update(VIBRADIAG_DATABASE_URL=test,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_COOKIE_SECURE='false',
        VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_DATA_ROOT=str(private/'data'))
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    org,actor,asset,point=[uuid4() for _ in range(4)];password=str(uuid4())+str(uuid4())
    with transaction() as conn:
        conn.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic comparison '+run_id))
        conn.execute(text("INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,'verification',:hash)"),dict(id=actor,org=org,hash=password_hasher.hash(password)))
        conn.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,'engineer',:id)"),dict(id=actor,org=org))
        conn.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:id,:org,:code,'Synthetic comparison asset','motor')"),dict(id=asset,org=org,code=run_id))
        conn.execute(text("INSERT INTO measurement_points(id,organization_id,asset_id,point_code,name) VALUES (:id,:org,:asset,'DE','Synthetic point')"),dict(id=point,org=org,asset=asset))
        conn.execute(text('INSERT INTO asset_grants VALUES (:org,:actor,:asset,true,true)'),dict(org=org,actor=actor,asset=asset))
        tables=conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'")).scalar_one()
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}';process=None;log=None;launch=0
    def start():
        nonlocal process,log,launch
        launch+=1;path=private/f'api-{launch}.log';log=path.open('w',encoding='utf8')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],
            cwd=PROJECT_ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(100):
            if process.poll() is not None:raise RuntimeError('API startup failed')
            try:
                if httpx.get(url+'/',timeout=1).status_code==200:break
            except httpx.HTTPError:pass
            sleep(.1)
        else:raise RuntimeError('Startup timeout')
        pids=re.findall(r'Started server process \[(\d+)\]',path.read_text(encoding='utf8'))
        if not pids:raise RuntimeError('Cannot identify owned API process')
        return int(pids[-1])
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
            with transaction() as conn:principal=authenticate(conn,client.cookies.get('vibradiag_session'))
            component=create_component(dict(asset_id=asset,component_code='DE',name='Synthetic target'),principal)
            bearing=create_bearing_revision(dict(component_id=component,expected_current_revision_id=None,parameterization='geometry',geometry=dict(
                rolling_elements=8,rolling_element_diameter=10.,pitch_diameter=40.,length_unit='mm',contact_angle_deg=0.,bearing_design='single_row_ball',source=SOURCE),reason='Synthetic target'),principal)
            profile=create_profile(dict(asset_id=asset,sampling_rate_hz=12000,band_low_hz=2000,band_high_hz=4000,
                modulation_low_hz=10,modulation_high_hz=500,selection_reason='Fixed synthetic comparison'),principal)
            chain=dict(configuration=dict(sensor_id='synthetic-generator',channel_id='synthetic-channel',daq_id='numpy',
                mounting_reference='no-physical-mount',calibration_reference='exact-mathematics-not-field-calibration',acquisition_configuration='fixed-fs12000-v1'),source=SOURCE)
            def post(path,body,key=None):
                response=client.post(path,json=body,headers={'Idempotency-Key':key or str(uuid4())});response.raise_for_status()
                return response.json(),response.status_code
            cases=[];denials=[]
            for count in (120000,1000000):
                records={};contexts={};basics={};envelopes={};confirmations={};hashes={};sides={}
                for name,amplitude,time in [('before',2.,'2026-01-01T00:00:00Z'),('after',1.,'2026-01-01T00:10:00Z')]:
                    t=np.arange(count)/12000;x=amplitude*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
                    raw=('value\n'+'\n'.join(format(v,'.17g') for v in x)+'\n').encode();hashes[name]=sha256(raw).hexdigest()
                    response=client.post('/api/v1/measurements',files={'file':(name+'-synthetic.csv',raw,'text/csv')},data={'metadata':json.dumps(dict(
                        sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(asset),measurement_point_id=str(point),direction='horizontal_radial',
                        rpm=1800.,load_value=100.,load_unit='Nm',acquired_at=time,source_type='synthetic',source_note='Isolated T04b HTTP comparison'))})
                    response.raise_for_status();record=response.json();records[name]=record
                    confirmation=client.post('/api/v1/measurements/'+record['id']+'/confirmations',json={'revision_id':record['current_revision_id']});confirmation.raise_for_status();confirmations[name]=confirmation.json()['id']
                    contexts[name]=create_context(dict(measurement_revision_id=record['current_revision_id'],component_id=component,bearing_revision_id=bearing,conditions=dict(
                        rpm_basis='synthetic_exact',rpm_source=SOURCE,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=SOURCE,
                        speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),acquisition=dict(low_hz=100.,high_hz=5000.,source=SOURCE),source_reference=SOURCE,reason='Exact synthetic context'),principal)
                    base=dict(measurement_revision_id=record['current_revision_id'],confirmation_id=confirmations[name])
                    basic,_=post('/api/v1/analyses',base);assert basic['status']=='completed';basics[name]=basic['analysis_id']
                    envelope,_=post('/api/v1/tool-runs',dict(base,tool_name='analyze_envelope',context_id=str(contexts[name]),profile_id=str(profile)));assert envelope['result_status']=='ok';envelopes[name]=envelope['tool_execution_id']
                    sides[name]=dict(provenance=dict(acquisition_id=f'{count}-{name}',root_acquisition_refs=[f'{count}-{name}'],source=SOURCE),
                        timing=dict(time_reference='start',source=SOURCE),measurement_chain=chain,
                        load=dict(quantity='torque',reference='synthetic-shaft-torque',condition='constant_steady_checked',source=SOURCE))
                declaration=dict(before_context_id=str(contexts['before']),after_context_id=str(contexts['after']),before_basic_id=basics['before'],after_basic_id=basics['after'],
                    before_envelope_id=envelopes['before'],after_envelope_id=envelopes['after'],**sides,order_source=SOURCE,
                    load_tolerance=dict(absolute=0.,unit='Nm',quantity='torque',reference='synthetic-shaft-torque',source=SOURCE),reason='Exact synthetic pair')
                identifier=create_declaration(declaration,principal)
                payload=dict(tool_name='compare_measurements',measurement_revision_id=records['after']['current_revision_id'],confirmation_id=confirmations['after'],
                    before_confirmation_id=confirmations['before'],declaration_id=str(identifier))
                key=str(uuid4());started=perf_counter();result,http_status=post('/api/v1/tool-runs',payload,key);elapsed=perf_counter()-started
                assert http_status==201 and result['status']=='completed' and result['result']['summary']['comparison_status']=='comparable'
                summary=result['result']['summary'];assert abs(summary['metrics']['rms']['relative_change_percent']+50)<1e-8
                assert abs(summary['metrics']['envelope_rms']['relative_change_percent']+50)<1e-8 and summary['repair_effectiveness'] is None
                replay,replay_status=post('/api/v1/tool-runs',payload,key);assert replay_status==200 and replay['tool_execution_id']==result['tool_execution_id']
                cases.append(dict(sample_count_per_input=count,input_sha256=hashes,comparison_http_seconds=elapsed,basic_rms_percent=summary['metrics']['rms']['relative_change_percent'],
                    envelope_rms_percent=summary['metrics']['envelope_rms']['relative_change_percent'],replay_same_resource=True,repair_effectiveness=None,diagnostic_evidence_eligible=False))
                if count==120000:
                    for label in ('not_comparable','related_or_same_acquisition'):
                        changed=json.loads(json.dumps(declaration))
                        if label=='not_comparable':changed['load_tolerance']=None
                        else:changed['after']['provenance']['root_acquisition_refs']=changed['before']['provenance']['root_acquisition_refs']
                        changed['reason']='Synthetic denial '+label
                        changed_payload=dict(payload,declaration_id=str(create_declaration(changed,principal)))
                        denied,_=post('/api/v1/tool-runs',changed_payload)
                        assert denied['status']=='completed' and denied['result_status']=='unavailable' and denied['result']['summary']['comparison_status']==label and not denied['result']['summary']['metrics']
                        denials.append(dict(comparison_status=label,metrics_empty=True))
                print(f'Two-input comparison HTTP passed: {count} points per input',flush=True)
            memory=memory_info(pid)
            interrupted_key=str(uuid4())
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(post,'/api/v1/tool-runs',payload,interrupted_key);interrupted_id=None
                for _ in range(1000):
                    with transaction() as conn:
                        interrupted_id=conn.execute(text("SELECT id FROM tool_runs WHERE requested_by=:actor AND tool_name='compare_measurements' AND status='running' ORDER BY started_at DESC LIMIT 1"),dict(actor=actor)).scalar_one_or_none()
                    if interrupted_id:break
                    if future.done():break
                    sleep(.002)
                if not interrupted_id:raise RuntimeError('Could not observe accepted comparison')
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(pid),'/F'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                else:process.kill()
                try:future.result()
                except httpx.HTTPError:pass
                else:raise RuntimeError('Interrupted request unexpectedly completed')
            stop();start()
            recovered,status=post('/api/v1/tool-runs',payload,interrupted_key)
            assert status==200 and recovered['status']=='interrupted' and recovered['tool_execution_id']==str(interrupted_id)
            retry,status=post('/api/v1/tool-runs',dict(payload,retry_of=str(interrupted_id)))
            assert status==201 and retry['status']=='completed' and retry['tool_execution_id']!=str(interrupted_id)
            client.post('/api/v1/auth/logout').raise_for_status()
            print('Owned API termination, comparison recovery/replay and explicit retry passed',flush=True)
        suite=ElementTree.parse(PROJECT_ROOT/tests_report).getroot().find('testsuite')
        assert int(suite.attrib['failures'])==int(suite.attrib['errors'])==int(suite.attrib['skipped'])==0
        paths=[p for folder in ('backend/src','backend/migrations','backend/tests','backend/scripts','frontend/src') for p in (PROJECT_ROOT/folder).rglob('*')
            if p.is_file() and p.suffix in {'.py','.sql','.ts','.vue','.css'} and '__pycache__' not in p.parts]
        paths += [PROJECT_ROOT/p for p in ('backend/requirements.lock.txt','frontend/package-lock.json',tests_report)]
        report=dict(run_id=run_id,passed=True,input_source='synthetic',factory_data_used=False,business_tables=tables,cases=cases,denial_cases=denials,
            actual_process_recovery=dict(same_key_status='interrupted',same_resource_id=True,explicit_retry_status=retry['status']),
            environment=dict(python=platform.python_version(),platform=platform.platform(),**memory),
            full_backend_tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},
            limitations=['Single local request per input size; not P95 or factory effectiveness','Comparison POST includes both sources/hash checks, persistence and final verified read; parent creation excluded',
                'API memory peak covers first API process and parent runs; excludes database/browser/model memory','Declarations attest to synthetic conditions, not field calibration'],
            source_sha256={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/receipt_path).write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
        print(json.dumps({k:v for k,v in report.items() if k!='source_sha256'}))
    finally:stop()


if __name__=='__main__':
    try:
        from verification_cli import verification_options
        main(**verification_options('experiments/m2-t04b-http-resources.json','experiments/m2-t04b-tests.xml'))
    except Exception:raise SystemExit('Comparison HTTP verification failed; inspect private runtime logs (credentials hidden)') from None
