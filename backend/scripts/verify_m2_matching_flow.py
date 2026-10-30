"""Actual matching/graph HTTP and resource receipt, isolated synthetic test database."""
import json
import os
import platform
import re
import runpy
import socket
import subprocess
import sys
from hashlib import sha256
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

SOURCE=dict(kind='synthetic_exact',reference='Exact synthetic AM and Z8 d10 D40 fixture; no real bearing model',version='t03b-v1')


def main(*, receipt_path='experiments/m2-t03b-http-resources.json', tests_report='experiments/m2-t03b-tests.xml'):
    test=setting('VIBRADIAG_TEST_DATABASE_URL'); migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test or not migration or make_url(test).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:
        raise RuntimeError('Separate test database required')
    run_id=uuid4().hex; private=PROJECT_ROOT/'.runtime/m2-matching-flow'/run_id; private.mkdir(parents=True)
    os.environ.update(VIBRADIAG_DATABASE_URL=test,VIBRADIAG_MIGRATION_DATABASE_URL=migration,
        VIBRADIAG_COOKIE_SECURE='false',VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_DATA_ROOT=str(private/'data'))
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    org,actor,asset=uuid4(),uuid4(),uuid4();password=str(uuid4())+str(uuid4())
    with transaction() as conn:
        conn.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic matching '+run_id))
        conn.execute(text("INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,'verification',:hash)"),dict(id=actor,org=org,hash=password_hasher.hash(password)))
        conn.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,'engineer',:id)"),dict(id=actor,org=org))
        conn.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:id,:org,:code,'Synthetic matching asset','motor')"),dict(id=asset,org=org,code=run_id))
        conn.execute(text('INSERT INTO asset_grants VALUES (:org,:actor,:asset,true,true)'),dict(org=org,actor=actor,asset=asset))
        table_count=conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'")).scalar_one()
    with socket.socket() as listener: listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}'; log_path=private/'api.log'; log=log_path.open('w',encoding='utf8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],
        cwd=PROJECT_ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        for _ in range(100):
            if process.poll() is not None: raise RuntimeError('API startup failed')
            try:
                if httpx.get(url+'/',timeout=1).status_code==200: break
            except httpx.HTTPError: pass
            sleep(.1)
        else: raise RuntimeError('Startup timeout')
        with httpx.Client(base_url=url,timeout=180) as client:
            auth=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username='verification',password=password));auth.raise_for_status()
            client.headers['x-csrf-token']=auth.json()['csrf_token']
            with transaction() as conn: principal=authenticate(conn,client.cookies.get('vibradiag_session'))
            component=create_component(dict(asset_id=asset,component_code='DE',name='Synthetic target'),principal)
            bearing=create_bearing_revision(dict(component_id=component,expected_current_revision_id=None,parameterization='geometry',geometry=dict(
                rolling_elements=8,rolling_element_diameter=10.,pitch_diameter=40.,length_unit='mm',contact_angle_deg=0.,bearing_design='single_row_ball',source=SOURCE,
                uncertainty=dict(rolling_element_diameter_absolute=0.,pitch_diameter_absolute=0.,contact_angle_deg_absolute=0.,source=SOURCE)),reason='Analytic fixture'),principal)
            profile=create_profile(dict(asset_id=asset,sampling_rate_hz=12000,band_low_hz=2000,band_high_hz=4000,modulation_low_hz=10,
                modulation_high_hz=500,selection_reason='Fixed AM matching HTTP verification'),principal)
            def post(path,body):
                response=client.post(path,json=body,headers={'Idempotency-Key':str(uuid4())});response.raise_for_status()
                result=response.json();assert result['status']=='completed'
                return result
            cases=[]
            for count in (120000,1000000):
                t=np.arange(count)/12000;x=2*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
                raw=('value\n'+'\n'.join(format(v,'.17g') for v in x)+'\n').encode()
                response=client.post('/api/v1/measurements',files={'file':('synthetic-am.csv',raw,'text/csv')},data={'metadata':json.dumps(dict(
                    sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(asset),rpm=1800,source_type='synthetic',source_note='M2-T03b isolated HTTP verification'))})
                response.raise_for_status();record=response.json();revision=record['current_revision_id']
                confirmation=client.post(f"/api/v1/measurements/{record['id']}/confirmations",json={'revision_id':revision});confirmation.raise_for_status()
                context=create_context(dict(measurement_revision_id=revision,component_id=component,bearing_revision_id=bearing,conditions=dict(
                    rpm_basis='synthetic_exact',rpm_source=SOURCE,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=SOURCE,
                    speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),source_reference=SOURCE,reason='Exact synthetic context'),principal)
                base=dict(measurement_revision_id=revision,confirmation_id=confirmation.json()['id'])
                basic=post('/api/v1/analyses',base)
                envelope=post('/api/v1/tool-runs',dict(base,tool_name='analyze_envelope',profile_id=str(profile),context_id=str(context)))
                frequency=post('/api/v1/tool-runs',dict(base,tool_name='calculate_bearing_frequencies',context_id=str(context)))
                body=dict(base,tool_name='match_frequency_evidence',context_id=str(context),envelope_run_id=envelope['tool_execution_id'],
                    frequency_run_id=frequency['tool_execution_id'],basic_analysis_id=basic['analysis_id'])
                started=perf_counter(); result=post('/api/v1/tool-runs',body);elapsed=perf_counter()-started
                assert result['result_status']=='ok' and len(result['evidence_refs'])==63 and not result['result']['summary']['evidence_eligible']
                evidence=client.get('/api/v1/evidence/'+result['evidence_refs'][0]);evidence.raise_for_status()
                fact=client.get('/api/v1/facts/'+evidence.json()['fact_id']);fact.raise_for_status()
                assert not evidence.json()['validity']['evidence_eligible']
                cases.append(dict(sample_count=count,input_sha256=sha256(raw).hexdigest(),matching_http_seconds=elapsed,
                    fact_count=len(result['fact_refs']),evidence_count=len(result['evidence_refs']),evidence_eligible=False,
                    independent_acquisition_count=result['result']['summary']['independent_acquisition_count'],evidence_and_fact_resolved=True))
                print(f'Matching HTTP and graph passed: {count} points',flush=True)
            client.post('/api/v1/auth/logout').raise_for_status()
        pids=re.findall(r'Started server process \[(\d+)\]',log_path.read_text(encoding='utf8'))
        memory=memory_info(int(pids[-1]))
        suite=ElementTree.parse(PROJECT_ROOT/tests_report).getroot().find('testsuite')
        assert int(suite.attrib['failures'])==int(suite.attrib['errors'])==int(suite.attrib['skipped'])==0
        paths=[p for folder in ('backend/src','backend/migrations','backend/tests','backend/scripts','frontend/src')
            for p in (PROJECT_ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.sql','.ts','.vue','.css'} and '__pycache__' not in p.parts]
        paths += [PROJECT_ROOT/p for p in ('backend/requirements.lock.txt','frontend/package-lock.json',tests_report)]
        report=dict(run_id=run_id,passed=True,source='synthetic',factory_data_used=False,business_tables=table_count,cases=cases,
            environment=dict(python=platform.python_version(),platform=platform.platform(),**memory),
            full_backend_tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},
            limitations=['One local run per size; not P95 or field performance','Matching POST timing includes lineage/hash checks, persistence and final verified read; parent creation excluded',
                'API peak includes both sizes and their parent calculations; excludes PostgreSQL/browser/model memory','Exploratory evidence only; no qualification publication'],
            source_sha256={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/receipt_path).write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
        print(json.dumps({k:v for k,v in report.items() if k!='source_sha256'}))
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill();process.wait()
        log.close()


if __name__=='__main__':
    try:
        from verification_cli import verification_options
        main(**verification_options('experiments/m2-t03b-http-resources.json','experiments/m2-t03b-tests.xml'))
    except Exception: raise SystemExit('Matching HTTP verification failed; inspect private runtime logs (credentials hidden)') from None
