"""Persistent task API with real PostgreSQL, original files and numerical services."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID,uuid4
import json
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.analysis_store import AnalysisStore
from vibradiag.services import agent_task_service as service
from vibradiag.services import persistent_analysis_service as basic
from vibradiag.services.tool_profile_service import create_profile
from test_tool_runs import case,login
from test_matching_runs import matching
from test_comparison_runs import comparison


@pytest.fixture(autouse=True)
def manual_executor(monkeypatch):
    monkeypatch.setenv('VIBRADIAG_AGENT_WORKER_ENABLED','false')


def create(case,goal='inspect_signal',key=None,**extra):
    payload=dict(goal=goal,description='Synthetic acceptance; no factory trial',**extra)
    if goal!='explain_method':payload['measurement_revision_id']=case['revision']
    response=case['client'].post('/api/v1/agent-tasks',json=payload,headers={'Idempotency-Key':key or str(uuid4())})
    assert response.status_code in {200,202},response.text
    return response


def read(case,task):
    response=case['client'].get('/api/v1/agent-tasks/'+task['id'])
    assert response.status_code==200,response.text
    return response.json()


def execute(task):
    # Claim a specific freshly queued task without depending on older fixture records.
    claimed=service.claim_next(UUID(task['id']))
    assert str(claimed['id'])==task['id']
    service.execute_claimed(claimed)


def resume(case,task,answer='Conditions reviewed; retry',key=None,**extra):
    return case['client'].post('/api/v1/agent-tasks/'+task['id']+'/resume',
        json=dict(expected_version=task['version'],question_id=task['question']['id'],answer=answer,**extra),
        headers={'Idempotency-Key':key or str(uuid4())})


def test_accept_replay_conflict_and_real_basic_draft(case):
    key=str(uuid4());response=create(case,key=key);task=response.json()
    assert response.status_code==202 and task['status']=='queued'
    assert response.headers['location'].endswith(task['id'])
    assert create(case,key=key).json()['id']==task['id']
    bad=case['client'].post('/api/v1/agent-tasks',json=dict(goal='bearing_observations',description='different',
        measurement_revision_id=case['revision']),headers={'Idempotency-Key':key})
    assert bad.status_code==409
    execute(task);result=read(case,task)
    assert result['status']=='completed',result
    assert len(result['calls'])==1 and len(result['budget']['reservations'])==1
    assert result['calls'][0]['state']=='completed'
    source=case['client'].get('/api/v1/analyses/'+result['calls'][0]['resource_id'])
    assert source.status_code==200 and source.json()['status']=='completed'
    assert result['draft']['observations'][0]['citations'][0]['id']==result['calls'][0]['resource_id']
    assert result['draft']['publication_state']=='unpublished_draft'
    assert result['executor']=='rules_graph_v1' and not result['model_called'] and result['formal_graph_executed']
    assert any(event['kind']=='call_reserved' for event in result['events'])
    assert create(case,key=key).json()['status']=='completed'


def test_pause_confirmation_resume_cas_and_idempotency(case):
    response=case['client'].post('/api/v1/measurements',files={'file':('unconfirmed.csv',case['raw'],'text/csv')},
        data={'metadata':json.dumps(dict(sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(case['asset']),source_type='synthetic'))})
    assert response.status_code==201,response.text
    case['measurement']=response.json();case['revision']=case['measurement']['current_revision_id']
    task=create(case).json();execute(task);paused=read(case,task)
    assert paused['status']=='waiting_information' and paused['question']['code']=='input_confirmation_required'
    assert not paused['calls']
    # Answer text alone never confirms the measurement.
    assert resume(case,paused).status_code==202
    execute(task);again=read(case,task)
    assert again['status']=='waiting_information'
    assert resume(case,paused).status_code==409
    confirmation=case['client'].post(f"/api/v1/measurements/{case['measurement']['id']}/confirmations",json={'revision_id':case['revision']})
    assert confirmation.status_code in {200,201},confirmation.text
    key=str(uuid4());assert resume(case,again,key=key).status_code==202
    assert resume(case,again,key=key).status_code==200
    execute(task);result=read(case,task)
    assert result['status']=='completed' and result['budget']['active_ms']>=again['budget']['active_ms']


def test_bearing_actual_tools_and_uncertainty_degradation(case):
    task=create(case,'bearing_observations').json();execute(task);result=read(case,task)
    # Module shares an asset with earlier cases: multiple profiles require an explicit choice.
    while result['status']=='waiting_information' and result['question']['choices']:
        choices=result['question']['choices'];field=result['question']['selection_field']
        answer=str(case['profile'] if field=='profile_id' else case['context'])
        assert answer in {item['id'] for item in choices}
        assert resume(case,result,answer).status_code==202
        execute(task);result=read(case,task)
    assert result['status']=='completed_degraded',result
    assert {c['action'] for c in result['calls']}=={'analyze_basic','analyze_envelope','calculate_bearing_frequencies'}
    assert 'matching_parents_or_uncertainty_incomplete' in result['draft']['missing_requirements']
    assert 'diagnostic_profile_not_qualified' in result['draft']['missing_requirements']
    assert len(result['budget']['reservations'])==3


def test_method_refusal_has_no_fabricated_rag(case):
    task=create(case,'explain_method').json();execute(task);result=read(case,task)
    assert result['status']=='refused' and result['calls']==[]
    assert result['draft']['missing_requirements']==['published_knowledge_service_not_integrated']
    assert result['draft']['observations']==[]


def test_auth_csrf_owner_scope_and_override_rejection(case):
    task=create(case).json();prefix='/api/v1/agent-tasks/'+task['id']
    for name,org in [('owner','org'),('admin','org'),('outsider','other_org')]:
        with login(case,name,org) as client:
            assert client.get(prefix).status_code==404
            assert task['id'] not in {item['id'] for item in client.get('/api/v1/agent-tasks').json()['items']}
    token=case['client'].headers.pop('x-csrf-token')
    try:
        assert case['client'].post(prefix+'/cancel',json={'expected_version':task['version']},headers={'Idempotency-Key':str(uuid4())}).status_code==403
    finally:case['client'].headers['x-csrf-token']=token
    bad=case['client'].post('/api/v1/agent-tasks',json=dict(goal='inspect_signal',description='x',measurement_revision_id=case['revision'],rpm=900),headers={'Idempotency-Key':str(uuid4())})
    assert bad.status_code==422
    response=case['client'].post(prefix+'/cancel',json={'expected_version':task['version']},headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==200 and response.json()['status']=='cancelled'


def test_cancel_during_computation_fences_late_result(case,monkeypatch):
    entered=Event();release=Event();original=basic.create_analysis
    def blocked(*args,**kwargs):
        entered.set();assert release.wait(15)
        return original(*args,**kwargs)
    monkeypatch.setattr(basic,'create_analysis',blocked)
    task=create(case).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(execute,task)
        try:
            assert entered.wait(10)
            running=read(case,task)
            response=case['client'].post('/api/v1/agent-tasks/'+task['id']+'/cancel',
                json={'expected_version':running['version']},headers={'Idempotency-Key':str(uuid4())})
            assert response.status_code==200,response.text
        finally:release.set()
        future.result()
    result=read(case,task)
    assert result['status']=='cancelled' and result['draft'] is None
    assert result['calls'][0]['state']=='interrupted'


def test_recovery_unknown_outcome_reattaches_without_double_calculation(case,monkeypatch):
    original=basic.create_analysis;count=0
    def response_lost(*args,**kwargs):
        nonlocal count
        count+=1
        outcome=original(*args,**kwargs)
        assert service.recover_interrupted()>=1
        return outcome
    monkeypatch.setattr(basic,'create_analysis',response_lost)
    task=create(case).json();execute(task);paused=read(case,task)
    assert paused['status']=='interrupted' and paused['calls'][0]['resource_id'] is None
    monkeypatch.setattr(basic,'create_analysis',original)
    assert resume(case,paused).status_code==202
    execute(task);result=read(case,task)
    assert result['status']=='completed' and len(result['calls'])==1
    assert len(result['budget']['reservations'])==1 and count==1
    with transaction() as conn:
        assert conn.execute(text('SELECT count(*) FROM analysis_runs WHERE measurement_revision_id=:id'),dict(id=UUID(case['revision']))).scalar_one()==1


def test_storage_failure_explicit_resume_creates_linked_retry(case,monkeypatch):
    original=AnalysisStore.publish
    def fail(*args,**kwargs):raise OSError('Private injected path')
    monkeypatch.setattr(AnalysisStore,'publish',fail)
    task=create(case).json();execute(task);paused=read(case,task)
    assert paused['status']=='interrupted',paused
    assert paused['calls'][0]['state']=='failed' and paused['calls'][0]['resource_id']
    monkeypatch.setattr(AnalysisStore,'publish',original)
    assert resume(case,paused).status_code==202
    execute(task);result=read(case,task)
    assert result['status']=='completed',result
    assert len(result['calls'])==2 and len(result['budget']['reservations'])==2
    with transaction() as conn:
        retry_of=conn.execute(text('SELECT retry_of FROM analysis_runs WHERE id=:id'),dict(id=UUID(result['calls'][1]['resource_id']))).scalar_one()
    assert str(retry_of)==paused['calls'][0]['resource_id']


def test_old_revision_fails_execution_and_permission_revocation_hides_task(case):
    task=create(case).json()
    response=case['client'].post(f"/api/v1/measurements/{case['measurement']['id']}/revisions",json=dict(
        metadata=dict(sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(case['asset']),rpm=900,source_type='synthetic'),
        reason='Correct next revision',expected_current_revision_id=case['revision'],row_version=1))
    assert response.status_code==201,response.text
    execute(task);result=read(case,task)
    assert result['status']=='superseded' and not result['draft_validity']['inputs_current'] and result['draft'] is None
    with transaction() as conn:
        conn.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer AND asset_id=:asset'),case)
    assert case['client'].get('/api/v1/agent-tasks/'+task['id']).status_code==404


def test_budget_recovery_excludes_manual_wait_and_terminal_history_immutable(case):
    task=create(case).json()
    claimed=service.claim_next(UUID(task['id']));assert str(claimed['id'])==task['id']
    with transaction() as conn:
        conn.execute(text("UPDATE agent_tasks SET last_tick_at=now()-interval '121 seconds',version=version+1 WHERE id=:id"),dict(id=UUID(task['id'])))
    service.recover_interrupted();result=read(case,task)
    assert result['status']=='timed_out' or result['status']=='budget_exhausted'
    assert result['budget']['active_ms']==120000
    service.execute_claimed(claimed)
    for query in ["UPDATE agent_tasks SET description='x' WHERE id=:id",'DELETE FROM agent_tasks WHERE id=:id',
                  "UPDATE agent_tasks SET version=version+1 WHERE id=:id"]:
        with pytest.raises(DBAPIError),transaction() as conn:conn.execute(text(query),dict(id=UUID(task['id'])))
    with pytest.raises(DBAPIError),transaction() as conn:
        conn.execute(text('DELETE FROM agent_events WHERE task_id=:id'),dict(id=UUID(task['id'])))


def choose_remaining(case,task):
    result=read(case,task)
    for _ in range(3):
        if result['status']!='waiting_information' or not result['question']['choices']:break
        field=result['question']['selection_field']
        value=str(case['profile'] if field=='profile_id' else case['context'])
        assert resume(case,result,value).status_code==202
        execute(task);result=read(case,task)
    return result


def test_complete_matching_uses_real_parents_and_retains_qualification_block(matching):
    task=create(matching,'bearing_observations').json();execute(task);result=choose_remaining(matching,task)
    assert result['status']=='completed_degraded',result
    calls=[c for c in result['calls'] if c['action']=='match_frequency_evidence']
    assert len(calls)==1 and calls[0]['state']=='completed'
    source=matching['client'].get('/api/v1/tool-runs/'+calls[0]['resource_id']).json()
    assert source['evidence_refs'] and not source['result']['summary']['evidence_eligible']
    assert 'diagnostic_profile_not_qualified' in result['draft']['missing_requirements']
    assert len(result['budget']['reservations'])==5


def test_comparison_task_binds_both_current_roots(comparison):
    comparison['revision']=comparison['after_record']['current_revision_id']
    task=create(comparison,'compare_measurements',comparison_declaration_id=str(comparison['declaration_id'])).json()
    assert len(task['input_snapshot']['roots'])==2
    execute(task);result=read(comparison,task)
    assert result['status']=='completed',result
    assert result['calls'][0]['action']=='compare_measurements'
    source=comparison['client'].get('/api/v1/tool-runs/'+result['calls'][0]['resource_id']).json()
    assert source['result']['summary']['comparison_status']=='comparable'
    assert source['result']['summary']['repair_effectiveness'] is None


def test_concurrent_same_key_and_cancel_cas(case):
    key=str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:create(case,key=key),range(2)))
    assert sorted(r.status_code for r in responses)==[200,202]
    task=responses[0].json()
    def cancel(_):
        return case['client'].post('/api/v1/agent-tasks/'+task['id']+'/cancel',
            json={'expected_version':task['version']},headers={'Idempotency-Key':str(uuid4())})
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(cancel,range(2)))
    assert sorted(r.status_code for r in responses)==[200,409]


def test_confirmation_revocation_invalidates_saved_draft(case):
    task=create(case).json();execute(task)
    assert read(case,task)['draft']
    with transaction() as conn:
        conn.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(case['confirmation'])))
    result=read(case,task)
    assert result['status']=='completed' and result['draft'] is None
    assert not result['draft_validity']['confirmations_active']


def test_session_expiry_requires_reauthentication_and_resume(case):
    task=create(case).json()
    with transaction() as conn:
        conn.execute(text('UPDATE auth_sessions SET revoked_at=now() WHERE id=:id'),dict(id=case['principal'].session_id))
    execute(task)
    with login(case) as client:
        case['client']=client
        paused=read(case,task)
        assert paused['status']=='waiting_information' and paused['question']['code']=='session_reauthentication_required'
        assert resume(case,paused).status_code==202
        execute(task);assert read(case,task)['status']=='completed'
        exhausted=create(case).json();claimed=service.claim_next(UUID(exhausted['id']))
        with transaction() as conn:
            conn.execute(text('UPDATE agent_tasks SET resume_count=20,version=version+1 WHERE id=:id'),dict(id=UUID(exhausted['id'])))
        with pytest.raises(service.YieldTask):service._wait(UUID(exhausted['id']),claimed['lease_token'],'input_confirmation_required')
        with login(case) as newer:
            case['client']=newer;paused=read(case,exhausted)
            response=resume(case,paused)
            assert response.status_code==202 and response.json()['status']=='budget_exhausted',response.text


def test_capacity_retry_is_bounded_and_preserves_reservation(case,monkeypatch):
    from vibradiag.services.compute_capacity import capacity
    original=service._change
    def debug(*args,**kwargs):
        if kwargs.get('error')=='TASK_EXECUTION_FAILED':
            import sys
            raise RuntimeError('Unexpected executor failure') from sys.exception()
        return original(*args,**kwargs)
    monkeypatch.setattr(service,'_change',debug)
    task=create(case).json();capacity.acquire();capacity.acquire()
    try:execute(task)
    finally:capacity.release();capacity.release()
    result=read(case,task)
    assert result['status']=='queued' and result['error_code']=='CAPACITY_WAIT'
    assert len(result['budget']['reservations'])==1 and result['budget']['reservations'][0]['retries_used']==1
    # Deferred scheduling is tested through the real worker; avoid sleeping in this unit of integration.
    from time import sleep
    sleep(2.1)
    execute(task);result=read(case,task)
    assert result['status']=='completed' and len(result['calls'])==1


def test_tool_budget_exhaustion_blocks_dispatch_without_refund(case):
    task=create(case).json();claimed=service.claim_next(UUID(task['id']))
    reservations=[dict(key=format(i,'064x'),kind='tool',retries_used=0) for i in range(20)]
    with transaction() as conn:
        row=service._running(conn,UUID(task['id']),claimed['lease_token'])
        service._change(conn,row,'running',budget=dict(policy_version='agent-budget-v1',active_ms=5,reservations=reservations))
    service.execute_claimed(claimed);result=read(case,task)
    assert result['status']=='budget_exhausted' and len(result['budget']['reservations'])==20
    assert result['calls']==[]


def test_profile_question_rejects_unoffered_choice_and_keeps_basic(case):
    create_profile(dict(asset_id=case['asset'],sampling_rate_hz=12000,band_low_hz=1900,band_high_hz=4100,
        modulation_low_hz=10,modulation_high_hz=500,selection_reason='Second candidate for explicit selection'),case['principal'])
    task=create(case,'bearing_observations').json();execute(task);result=read(case,task)
    assert result['status']=='waiting_information' and result['question']['code']=='profile_required'
    assert len(result['calls'])==1 and result['calls'][0]['action']=='analyze_basic'
    assert resume(case,result,str(uuid4())).status_code==409
    assert read(case,task)['budget']==result['budget']
    cancelled=case['client'].post('/api/v1/agent-tasks/'+task['id']+'/cancel',
        json={'expected_version':result['version']},headers={'Idempotency-Key':str(uuid4())})
    assert cancelled.status_code==200


def test_bearing_revision_change_invalidates_saved_task_draft(case):
    from test_tool_runs import SOURCE
    from vibradiag.services.diagnostic_context_service import create_bearing_revision
    task=create(case,'bearing_observations').json();execute(task);result=choose_remaining(case,task)
    assert result['status']=='completed_degraded' and result['draft']
    create_bearing_revision(dict(component_id=case['component'],expected_current_revision_id=case['bearing'],parameterization='geometry',
        geometry=dict(rolling_elements=9,rolling_element_diameter=10.,pitch_diameter=40.,length_unit='mm',contact_angle_deg=0.,
            bearing_design='single_row_ball',source=SOURCE),reason='Correct next bearing revision'),case['principal'])
    result=read(case,task)
    assert result['status']=='completed_degraded' and result['draft'] is None
    assert not result['draft_validity']['contexts_current'] and not result['draft_validity']['usable']
