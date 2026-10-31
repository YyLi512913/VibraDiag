"""Production ASGI/services, isolated synthetic fixtures, frozen benchmark-only strategy adapters."""
from dataclasses import asdict
from hashlib import sha256
import json
from time import monotonic
from uuid import UUID,uuid4
import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import text
from vibradiag.core.paths import BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.llm.formal_gateway import configuration
from vibradiag.services.identity_service import password_hasher,authenticate
from vibradiag.services.tool_profile_service import create_profile
from vibradiag.services.diagnostic_context_service import create_component,create_bearing_revision,create_context
from vibradiag.services import agent_task_service as tasks
from vibradiag.evaluation import single_router
from vibradiag.evaluation.agent_metrics import assess,summarize,order

PROTOCOL=BACKEND_ROOT/'config/agent-evaluation-v1.json'


def protocol():return json.loads(PROTOCOL.read_text(encoding='utf8'))


def fixture(client,principal,case,spec):
    asset=uuid4();point=uuid4()
    with transaction() as c:
        values=dict(asset=asset,point=point,org=principal.organization_id,user=principal.user_id,code=str(asset))
        c.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:asset,:org,:code,'Synthetic evaluation motor','motor')"),values)
        c.execute(text("INSERT INTO measurement_points(id,organization_id,asset_id,point_code,name) VALUES (:point,:org,:asset,'DE','Synthetic DE')"),values)
        c.execute(text('INSERT INTO asset_grants VALUES (:org,:user,:asset,true,true)'),values)
    t=np.arange(spec['sample_count'])/spec['sampling_rate_hz'];x=spec['amplitude']*(1+spec['modulation_depth']*np.cos(2*np.pi*spec['modulation_hz']*t))*np.cos(2*np.pi*spec['carrier_hz']*t)
    raw=('value\n'+'\n'.join(format(v,'.17g') for v in x)+'\n').encode()
    metadata=dict(sampling_rate_hz=spec['sampling_rate_hz'],acceleration_unit='m/s2',asset_id=str(asset),measurement_point_id=str(point),rpm=1800.,source_type='synthetic')
    response=client.post('/api/v1/measurements',files={'file':('synthetic.csv',raw,'text/csv')},data={'metadata':json.dumps(metadata)})
    response.raise_for_status();measurement=response.json();revision=measurement['current_revision_id']
    if case['confirmed']:
        response=client.post('/api/v1/measurements/'+measurement['id']+'/confirmations',json=dict(revision_id=revision));response.raise_for_status()
    if case['goal']=='bearing_observations':
        source=dict(kind='synthetic_exact',reference='Analytic developer-authored AM and bearing geometry; no field data',version='1')
        component=create_component(dict(asset_id=asset,component_code=str(uuid4()),name='Synthetic bearing'),principal)
        geometry=dict(rolling_elements=8,rolling_element_diameter=10.,pitch_diameter=40.,length_unit='mm',contact_angle_deg=0.,bearing_design='single_row_ball',source=source)
        if case['uncertainty_complete']:geometry['uncertainty']=dict(rolling_element_diameter_absolute=0.,pitch_diameter_absolute=0.,contact_angle_deg_absolute=0.,source=source)
        bearing=create_bearing_revision(dict(component_id=component,expected_current_revision_id=None,parameterization='geometry',geometry=geometry,reason='Synthetic evaluation'),principal)
        create_context(dict(measurement_revision_id=revision,component_id=component,bearing_revision_id=bearing,
            conditions=dict(rpm_basis='synthetic_exact',rpm_source=source,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=source,speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),
            acquisition=dict(low_hz=100.,high_hz=5000.,source=source),source_reference=source,reason='Synthetic evaluation'),principal)
        create_profile(dict(asset_id=asset,sampling_rate_hz=12000,band_low_hz=2000,band_high_hz=4000,modulation_low_hz=10,modulation_high_hz=500,selection_reason='Fixed synthetic evaluation band'),principal)
    return revision,sha256(raw).hexdigest()


def run(private,progress=lambda value:None):
    from vibradiag.main import app
    spec=protocol();cfg=configuration('deepseek',purpose='numeric');org=uuid4();user=uuid4();password=str(uuid4())+str(uuid4())
    with transaction() as c:
        c.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic Agent evaluation '+str(org)))
        c.execute(text('INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,:name,:hash)'),dict(id=user,org=org,name='evaluation',hash=password_hasher.hash(password)))
        c.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,'engineer',:id)"),dict(id=user,org=org))
    rows=[]
    with TestClient(app) as client:
        response=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username='evaluation',password=password));response.raise_for_status()
        client.headers['x-csrf-token']=response.json()['csrf_token']
        with transaction() as c:principal=authenticate(c,client.cookies.get('vibradiag_session'))
        for index,case in enumerate(spec['cases']):
            revision,signal_hash=fixture(client,principal,case,spec['signal'])
            for strategy in order(index):
                import os
                os.environ['VIBRADIAG_NUMERIC_EXECUTOR']='model' if strategy=='agent' else 'rules'
                started=monotonic()
                response=client.post('/api/v1/agent-tasks',json=dict(goal=case['goal'],description=case['description'],measurement_revision_id=revision),headers={'Idempotency-Key':str(uuid4())});response.raise_for_status()
                identifier=response.json()['id'];claimed=tasks.claim_next(UUID(identifier));receipt=None
                if strategy=='single_router':receipt=single_router.execute(claimed,principal,cfg,private/(identifier+'.json'))
                else:tasks.execute_claimed(claimed)
                response=client.get('/api/v1/agent-tasks/'+identifier);response.raise_for_status();task=response.json();seconds=monotonic()-started
                traces=[r['trace'] for r in task['model_rounds'] if r['trace']]
                if receipt and receipt.get('trace'):traces.append(receipt['trace'])
                reserved=sum(r['kind']=='model' for r in task['budget']['reservations'])
                row=dict(case_id=case['id'],strategy=strategy,status=task['status'],error_code=task['error_code'],execution_seconds=seconds,
                    task_id=identifier,graph=task['graph'],production_model_called_field=task['model_called'],
                    selected_workflow=receipt.get('selected_workflow') if receipt else None,router_state=receipt.get('state') if receipt else None,
                    signal_sha256=signal_hash,calls=task['calls'],model_traces=traces,budget=task['budget'],
                    missing_requirements=(task['draft'] or {}).get('missing_requirements',[]),metrics=assess(case,task,model_reserved=reserved,model_confirmed=len(traces)))
                rows.append(row);(private/'partial-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                progress(dict(case_id=case['id'],strategy=strategy,status=task['status'],expectation_met=row['metrics']['expectation_met']))
    return dict(protocol_version=spec['protocol_version'],protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),configuration=asdict(cfg),
        rows=rows,summary=summarize(rows),all_cases_met_expectations=all(r['metrics']['expectation_met'] for r in rows),
        independent_holdout=False,semantic_quality_qualified=False,factory_trial_approved=False,
        methodology=dict(transport='in-process ASGI TestClient, real PostgreSQL/files and production compute services',
            fixture_setup_excluded_from_execution_time=True,execution_time_includes_task_creation_and_final_read=True,
            ordering='rotated sequential strategies, one repetition, no warmup; not a controlled latency benchmark',
            source_families=1,independent_acquisitions=0,single_router='benchmark-only router receipt/event ledger; production task model_called excludes that separate receipt',
            no_automatic_provider_retry=True,no_provider_fallback=True,invoice_cost_available=False),
        limitations=spec['exclusions']+['Five authored controls share one synthetic signal family; no population accuracy or confidence interval',
            'Tasks have explicit structured goals; does not evaluate natural-language goal classification',
            'Single-call router sees registered workflows, Agent sees step candidates; comparison is architecture-specific, not universal single-prompt capability',
            'Exact action coverage is a developer rubric, not independent evidence relevance or semantic correctness'])
