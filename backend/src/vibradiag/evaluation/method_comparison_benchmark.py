"""Frozen authored controls through real task APIs, retrieval and two-input services."""
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import json
import os
from time import monotonic
from uuid import UUID, uuid4
import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import text
from vibradiag.core.paths import BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.llm.formal_gateway import configuration
from vibradiag.infrastructure.retrieval.embedding import configured_identity
from vibradiag.services.identity_service import password_hasher, authenticate
from vibradiag.services import agent_task_service as tasks, agent_model_service as models
from vibradiag.services.model_method_service import QUOTE_PREFIX
from vibradiag.services.diagnostic_context_service import create_component, create_bearing_revision, create_context
from vibradiag.services.comparison_service import create_declaration
from vibradiag.evaluation.method_comparison import score_method, score_comparison

PROTOCOL = BACKEND_ROOT/'config/agent-method-comparison-v2.json'
PREFIX = '/api/v1/knowledge-documents'
SOURCE = dict(kind='synthetic_exact', reference='Developer-authored analytic AM; no physical acquisition or field calibration', version='t05c2b-v1')


def post(client, path, **kwargs):
    response = client.post(path, headers={'Idempotency-Key': str(uuid4())}, **kwargs)
    response.raise_for_status()
    return response.json()


def get(client, path):
    response = client.get(path); response.raise_for_status(); return response.json()


def asset(principal):
    a, p = uuid4(), uuid4()
    with transaction() as c:
        values = dict(a=a, p=p, org=principal.organization_id, actor=principal.user_id)
        c.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:a,:org,CAST(:a AS text),'Authored evaluation motor','motor')"), values)
        c.execute(text("INSERT INTO measurement_points(id,organization_id,asset_id,point_code,name) VALUES (:p,:org,:a,'DE','Synthetic DE')"), values)
        c.execute(text('INSERT INTO asset_grants VALUES (:org,:actor,:a,true,true)'), values)
    return a, p


def document_action(client, revision, operation):
    return post(client, PREFIX+'/revisions/'+revision['id']+'/'+operation, json=dict(
        expected_version=revision['row_version'], expected_document_version=revision['document_version'], reason='Explicit authored development '+operation))


def publish(client, a, name, source):
    body = dict(title='Authored development '+name, source_note='Synthetic development control, not engineer-approved field knowledge',
                equipment_scope=['motor'], component_scope='bearing', content_version='t05c2b-v1',
                usage_rights='Developer authored; permitted for this evaluation', content_origin='internal_note',
                visibility='asset', asset_id=str(a), reason='Isolated test database development corpus')
    # internal_note exercises the normal retrieval route only inside this dedicated test DB.
    revision = post(client, PREFIX, files={'file': (name+'.md', source['text'].encode(), 'text/markdown')}, data={'metadata': json.dumps(body)})
    revision = document_action(client, document_action(client, revision, 'submit'), 'publish')
    index = post(client, '/api/v1/knowledge-indexes/revisions/'+revision['id']+'/build', json=dict(
        expected_version=revision['row_version'], expected_document_version=revision['document_version'], expected_index_version=0, reason='Authored development index'))
    if index['status'] != 'ready': raise RuntimeError('Development index failed')
    chunks = get(client, PREFIX+'/revisions/'+revision['id']+'/chunks')['items']
    if revision['source_sha256'] != sha256(source['text'].encode()).hexdigest(): raise RuntimeError('Corpus bytes changed')
    return revision, index, chunks


@contextmanager
def withdraw_after_generation(client, revision):
    """Only the owned sequential task; real provider response is unchanged, then publication is withdrawn."""
    original = models.Gateway
    state = dict(injected=False)
    class WithWithdrawal:
        def __init__(self, cfg): self.gateway = original(cfg)
        def invoke(self, phase, *args, **kwargs):
            response = self.gateway.invoke(phase, *args, **kwargs)
            if phase == 'extractive_draft' and not state['injected']:
                document_action(client, get(client, PREFIX+'/revisions/'+revision['id']), 'withdraw')
                state['injected'] = True
            return response
    models.Gateway = WithWithdrawal
    try: yield state
    finally: models.Gateway = original


def execute(client, payload):
    started = monotonic()
    accepted = post(client, '/api/v1/agent-tasks', json=payload)
    claimed = tasks.claim_next(UUID(accepted['id']))
    if claimed is None: raise RuntimeError('Owned task not claimable')
    tasks.execute_claimed(claimed)
    return get(client, '/api/v1/agent-tasks/'+accepted['id']), monotonic()-started


def common_row(case, task, seconds):
    traces = [r['trace'] for r in task['model_rounds'] if r['trace']]
    reserved = sum(r['kind']=='model' for r in task['budget']['reservations'])
    return dict(case_id=case['id'], task_id=task['id'], status=task['status'], error_code=task['error_code'],
                executor=task['executor'], graph=task['graph'], calls=task['calls'], model_rounds=task['model_rounds'],
                model_traces=traces, model_reserved=reserved, model_confirmed=len(traces), model_unknown=max(0,reserved-len(traces)),
                budget=task['budget'], draft=task['draft'], execution_seconds=seconds,
                draft_validity=task['draft_validity'], embedding_called=task['embedding_called'])


def method_case(client, principal, case, spec):
    a, _ = asset(principal); inventory=[]; spans={}; source_texts={}; revisions=[]
    for name in case['documents']:
        source=spec['corpus'][name];revision,index,chunks=publish(client,a,name,source);revisions.append(revision)
        inventory.append(dict(name=name,revision_id=revision['id'],source_sha256=revision['source_sha256'],index_id=index['id'],chunks=chunks))
        for chunk in chunks:
            source_texts[chunk['id']]=chunk['text']
            marked=[s for s in source['relevant_spans'] if s in chunk['text']]
            if marked: spans[chunk['id']]=marked
    payload=dict(goal='explain_method',description=case['question'],knowledge_scope=dict(equipment='motor',component='bearing',asset_id=str(a)))
    injected=False
    if case['withdraw_after_generation']:
        with withdraw_after_generation(client,revisions[0]) as state: task,seconds=execute(client,payload)
        injected=state['injected']
    else: task,seconds=execute(client,payload)
    observations=(task['draft'] or {}).get('observations',[]); selections=[]; format_ok=True
    for observation in observations:
        refs=observation['citations'];valid_format=observation['text'].startswith(QUOTE_PREFIX) and len(refs)==1 and refs[0]['kind']=='knowledge_chunk' and observation['claim_kind']=='method_explanation'
        format_ok=format_ok and valid_format
        selections.append(dict(source_id=refs[0]['id'] if refs else '',quote=observation['text'][len(QUOTE_PREFIX):] if valid_format else ''))
    visible={h['id']:h['text'] for h in task['knowledge_references']}
    # Resolve citations against current authorized API references, not the corpus inventory alone.
    actual=dict(status=task['status'],sources=visible,selections=selections,
                output_state='no_visible_draft' if task['draft'] is None else task['draft']['publication_state'],
                unpublished=task['draft'] is None or task['draft'].get('publication_state')=='unpublished_draft',
                diagnostic_claim=any(s in json.dumps(observations) for s in spec['forbidden_output_spans']) or any(o['claim_kind']!='method_explanation' for o in observations))
    metric=score_method(dict(relevant_source_ids=list(spans),relevant_spans=spans,must_refuse=case['must_refuse']),actual)
    metric['current_sources_match_inventory']=all(source_texts.get(k)==v for k,v in visible.items())
    metric['lifecycle_injected']=injected if case['withdraw_after_generation'] else None
    metric['no_unexpected_tools']=all(c['action']=='search_published_knowledge' for c in task['calls'])
    metric['extract_format_valid']=format_ok
    metric['met_expectations'] &= metric['current_sources_match_inventory'] and metric['no_unexpected_tools'] and format_ok and (injected if case['withdraw_after_generation'] else True)
    if not case['must_refuse']: metric['met_expectations'] &= task['draft_validity']['usable']
    row=common_row(case,task,seconds)
    row.update(kind='method',inventory=inventory,expected=dict(must_refuse=case['must_refuse'],relevant_spans=spans),normalized=actual,metrics=metric)
    return row


def comparison_fixture(client,principal,case,spec):
    a,p=asset(principal);signal=spec['signal'];t=np.arange(signal['sample_count'])/signal['sampling_rate_hz']
    base=(1+signal['modulation_depth']*np.cos(2*np.pi*signal['modulation_hz']*t))*np.cos(2*np.pi*signal['carrier_hz']*t)
    component=create_component(dict(asset_id=a,component_code=str(uuid4()),name='Synthetic bearing'),principal)
    bearing=create_bearing_revision(dict(component_id=component,expected_current_revision_id=None,parameterization='geometry',geometry=dict(
        rolling_elements=8,rolling_element_diameter=10.,pitch_diameter=40.,length_unit='mm',contact_angle_deg=0.,bearing_design='single_row_ball',source=SOURCE),reason='Authored comparison control'),principal)
    records={};contexts={};basics={};confirmations={};hashes={}
    for name,stamp in [('before','2026-01-01T00:00:00Z'),('after','2026-01-01T00:01:00Z')]:
        amplitude=signal['before_amplitude'] if name=='before' or case['scenario']=='duplicate_samples' else signal['after_amplitude']
        raw=('value\n'+'\n'.join(format(v,'.17g') for v in amplitude*base)+'\n').encode();hashes[name]=sha256(raw).hexdigest()
        metadata=dict(sampling_rate_hz=signal['sampling_rate_hz'],acceleration_unit='m/s2',asset_id=str(a),measurement_point_id=str(p),direction='horizontal_radial',
                      rpm=1800.,load_value=100.,load_unit='Nm',source_type='synthetic',acquired_at=stamp)
        record=post(client,'/api/v1/measurements',files={'file':(name+'.csv',raw,'text/csv')},data={'metadata':json.dumps(metadata)});records[name]=record
        revision=record['current_revision_id']
        confirmations[name]=post(client,'/api/v1/measurements/'+record['id']+'/confirmations',json=dict(revision_id=revision))['id']
        contexts[name]=str(create_context(dict(measurement_revision_id=revision,component_id=component,bearing_revision_id=bearing,
            conditions=dict(rpm_basis='synthetic_exact',rpm_source=SOURCE,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=SOURCE,speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),
            acquisition=dict(low_hz=100.,high_hz=5000.,source=SOURCE),source_reference=SOURCE,reason='Authored comparison control'),principal))
        result=post(client,'/api/v1/analyses',json=dict(measurement_revision_id=revision,confirmation_id=confirmations[name]))
        if result['status']!='completed':raise RuntimeError('Comparison parent failed')
        basics[name]=result['analysis_id']
    chain=dict(configuration=dict(sensor_id='synthetic-generator',channel_id='synthetic-channel',daq_id='numpy',mounting_reference='no-physical-mount',
               calibration_reference='exact-mathematics-not-field-calibration',acquisition_configuration='synthetic-fs12000-v1'),source=SOURCE)
    sides={name:dict(provenance=dict(acquisition_id='t05c2b-'+name,root_acquisition_refs=['t05c2b-'+('before' if case['scenario']=='shared_root' else name)],source=SOURCE),
        timing=dict(time_reference='start',source=SOURCE),load=dict(quantity='torque',reference='synthetic-shaft-torque',condition='constant_steady_checked',source=SOURCE),measurement_chain=chain) for name in ('before','after')}
    declaration=dict(before_context_id=contexts['before'],after_context_id=contexts['after'],before_basic_id=basics['before'],after_basic_id=basics['after'],**sides,
                     order_source=SOURCE,load_tolerance=None if case['scenario']=='missing_load_tolerance' else dict(absolute=0.,unit='Nm',quantity='torque',reference='synthetic-shaft-torque',source=SOURCE),reason='Authored comparison control')
    identifier=create_declaration(declaration,principal)
    return dict(goal='compare_measurements',description='Compare only eligible measurements; a change is not repair effectiveness.',
                measurement_revision_id=records['after']['current_revision_id'],comparison_declaration_id=str(identifier)),dict(records=records,hashes=hashes,basics=basics,declaration=declaration)


def comparison_case(client,principal,case,spec):
    payload,inventory=comparison_fixture(client,principal,case,spec);task,seconds=execute(client,payload)
    row=common_row(case,task,seconds);completed=[c for c in task['calls'] if c['action']=='compare_measurements' and c['resource_id']]
    if len(completed)!=1:raise RuntimeError('Comparison task did not produce one bound tool receipt')
    tool=get(client,'/api/v1/tool-runs/'+completed[0]['resource_id']);summary=tool['result']['summary']
    draft=task['draft'] or {};observations=draft.get('observations',[])
    citations=[r for o in observations for r in o['citations']]
    actual=dict(comparison_status=summary['comparison_status'],metrics=summary['metrics'],repair_effectiveness=summary['repair_effectiveness'],
                diagnostic_claim=any(o['claim_kind']!='exploratory_observation' for o in observations),
                both_inputs_current_and_authorized=tool['is_current_before_revision'] and tool['is_current_revision'] and tool['input_confirmation_active'],
                parents_valid=task['draft_validity']['usable'] and bool(citations) and all(r['id']==tool['tool_execution_id'] and r['kind']=='tool_run' for r in citations))
    metric=score_comparison(case,actual)
    metric['expected_input_bindings']=tool['before_measurement_revision_id']==inventory['records']['before']['current_revision_id'] and tool['measurement_revision_id']==inventory['records']['after']['current_revision_id'] and tool['declaration_id']==payload['comparison_declaration_id']
    metric['task_boundary_preserved']=task['status'] in {'completed','completed_degraded'} and draft.get('publication_state')=='unpublished_draft' and not task['model_called'] and len(task['calls'])==1
    metric['met_expectations'] &= metric['task_boundary_preserved'] and metric['expected_input_bindings']
    row.update(kind='comparison',inventory=inventory,tool=tool,normalized=actual,metrics=metric)
    return row


def run(private,progress=lambda value:None):
    from vibradiag.main import app
    if os.environ.get('VIBRADIAG_AGENT_WORKER_ENABLED')!='false':raise RuntimeError('Sequential owned-task runner requires disabled worker')
    spec=json.loads(PROTOCOL.read_text(encoding='utf8'));org,user,admin=uuid4(),uuid4(),uuid4();password=str(uuid4())+str(uuid4())
    with transaction() as c:
        c.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Authored method/comparison evaluation '+str(org)))
        for identifier,name,role in [(user,'evaluation','engineer'),(admin,'administrator','admin')]:
            c.execute(text('INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,:name,:hash)'),dict(id=identifier,org=org,name=name,hash=password_hasher.hash(password)))
            c.execute(text('INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,:role,:id)'),dict(id=identifier,org=org,role=role))
    rows=[];old_method=os.environ.get('VIBRADIAG_METHOD_EXECUTOR');old_numeric=os.environ.get('VIBRADIAG_NUMERIC_EXECUTOR')
    os.environ.update(VIBRADIAG_METHOD_EXECUTOR='model',VIBRADIAG_NUMERIC_EXECUTOR='rules')
    try:
        with TestClient(app) as client:
            response=post(client,'/api/v1/auth/login',json=dict(organization_id=str(org),username='evaluation',password=password));client.headers['x-csrf-token']=response['csrf_token']
            with transaction() as c:principal=authenticate(c,client.cookies.get('vibradiag_session'))
            administrator=TestClient(app)  # Shares the already active ASGI lifespan; no second worker lease.
            try:
                response=post(administrator,'/api/v1/auth/login',json=dict(organization_id=str(org),username='administrator',password=password));administrator.headers['x-csrf-token']=response['csrf_token']
                post(administrator,PREFIX+'/review-grants',json=dict(user_id=str(user),enabled=True,expected_version=0,reason='Explicit isolated development reviewer, not independent engineering review',password=password))
            finally:administrator.close()
            for kind,cases,runner in [('method',spec['method_cases'],method_case),('comparison',spec['comparison_cases'],comparison_case)]:
                for case in cases:
                    row=runner(client,principal,case,spec);rows.append(row)
                    (private/'partial-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf8')
                    progress(dict(case_id=case['id'],kind=kind,status=row['status'],met_expectations=row['metrics']['met_expectations']))
    finally:
        for key,value in [('VIBRADIAG_METHOD_EXECUTOR',old_method),('VIBRADIAG_NUMERIC_EXECUTOR',old_numeric)]:
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
    usages=[t.get('usage',{}) for r in rows for t in r['model_traces']]
    token_usage={key:sum(u[key] for u in usages) if usages and all(type(u.get(key)) is int for u in usages) else None for key in ('prompt_tokens','completion_tokens','total_tokens')}
    return dict(protocol_version=spec['version'],protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),configuration=asdict(configuration('deepseek')),
                embedding_identity=asdict(configured_identity()),rows=rows,
                summary={kind:dict(runs=sum(r['kind']==kind for r in rows),passed=sum(r['kind']==kind and r['metrics']['met_expectations'] for r in rows)) for kind in ('method','comparison')},
                model_reserved=sum(r['model_reserved'] for r in rows),model_confirmed=sum(r['model_confirmed'] for r in rows),model_unknown=sum(r['model_unknown'] for r in rows),
                token_usage=token_usage,all_cases_met_expectations=all(r['metrics']['met_expectations'] for r in rows),independent_holdout=False,semantic_quality_qualified=False,diagnostic_release=False,factory_trial_approved=False,
                methodology=dict(transport='Real PostgreSQL and production ASGI/services',repetitions=1,method_mode='model_method_v1',comparison_mode='rules_graph_v1',
                                 corpus_origin='Developer-authored controls imported as internal_note only in isolated test database',withdrawal='After unchanged provider response, before generation receipt commit',monetary_cost=None),limitations=spec['limitations'])
