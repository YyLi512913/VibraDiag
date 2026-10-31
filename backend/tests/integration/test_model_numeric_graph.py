"""Actual M1/M2 tools and durable numerical graph with a deterministic model double."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID,uuid4
import json
import pytest
from sqlalchemy import text
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.graph_store import TaskSaver
from vibradiag.services import agent_task_service as tasks,agent_model_service as models,persistent_analysis_service as basic
from vibradiag.agents import model_numeric_graph
from test_tool_runs import case,login
from test_agent_tasks import create,read,resume,execute,manual_executor
from test_matching_runs import matching
from test_comparison_runs import comparison


@pytest.fixture
def planner(monkeypatch):
    monkeypatch.setenv('VIBRADIAG_NUMERIC_EXECUTOR','model');monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    class Fake:
        calls=[];mode='choose';reverse=False;hook=None;stop_after=None
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            assert phase=='numeric_decision';self.calls.append(context)
            hook=type(self).hook
            if hook:hook(context)
            candidates=context['candidates']
            if self.mode=='bad':output=dict(action='choose_action',candidate_id=str(uuid4()),rationale='forged')
            elif self.mode=='override':output=dict(action='choose_action',candidate_id=candidates[0]['candidate_id'],rationale='override',rpm=999)
            elif self.mode=='ask':output=dict(action='ask_information',question_id=str(uuid4()),rationale='invent question')
            elif self.mode=='finish' or not candidates or self.stop_after is not None and context['step']>=self.stop_after:output=dict(action='finish',outcome='analysis_only' if context['completed'] else 'insufficient_evidence',rationale='Only exploratory results')
            else:output=dict(action='choose_action',candidate_id=candidates[-1 if self.reverse else 0]['candidate_id'],rationale='Use offered deterministic tool')
            return json.dumps(output),dict(provider=self.cfg.provider,requested_model=self.cfg.model,served_model=self.cfg.model)
    monkeypatch.setattr(models,'Gateway',Fake);return Fake


def run_to_end(data,task):
    execute(task);result=read(data,task)
    for _ in range(4):
        if result['status']!='waiting_information' or not result['question']['choices']:break
        field=result['question']['selection_field'];answer=str(data['profile'] if field=='profile_id' else data['context'])
        assert resume(data,result,answer).status_code==202
        execute(task);result=read(data,task)
    return result


def test_basic_two_decisions_one_real_tool_and_unpublished_draft(case,planner):
    task=create(case).json();result=run_to_end(case,task)
    assert result['executor']=='model_numeric_v1' and result['graph']['version']=='model-numeric-v1'
    assert result['status']=='completed' and result['model_called']
    assert len(result['model_rounds'])==2 and len(result['calls'])==1 and len(result['budget']['reservations'])==3
    assert result['draft']['observations'][0]['citations'][0]['id']==result['calls'][0]['resource_id']
    assert result['draft']['publication_state']=='unpublished_draft'
    assert not planner.calls[-1]['candidates']


@pytest.mark.parametrize('reverse',[False,True])
def test_bearing_model_can_choose_tool_order_but_missing_uncertainty_blocks_matching(case,planner,reverse):
    planner.reverse=reverse;task=create(case,'bearing_observations').json();result=run_to_end(case,task)
    assert result['status']=='completed_degraded',result
    expected=['analyze_basic']+(['calculate_bearing_frequencies','analyze_envelope'] if reverse else ['analyze_envelope','calculate_bearing_frequencies'])
    assert [h['action'] for h in result['calls']]==expected
    assert len(result['model_rounds'])==4 and len(result['budget']['reservations'])==7
    assert 'diagnostic_profile_not_qualified' in result['draft']['missing_requirements']
    assert 'matching_not_completed' in result['draft']['missing_requirements']
    assert not any(c['action']=='match_frequency_evidence' for ctx in planner.calls for c in ctx['candidates'])


def test_exact_uncertainty_exposes_matching_only_after_actual_bound_parents(matching,planner):
    task=create(matching,'bearing_observations').json();result=run_to_end(matching,task)
    assert result['status']=='completed_degraded',result
    assert [h['action'] for h in result['calls']]==['analyze_basic','analyze_envelope','calculate_bearing_frequencies','analyze_envelope','match_frequency_evidence']
    assert len(result['model_rounds'])==6 and len(result['budget']['reservations'])==11
    assert 'diagnostic_profile_not_qualified' in result['draft']['missing_requirements']
    for observation in result['draft']['observations']:assert observation['citations'][0]['id'] in {h['resource_id'] for h in result['calls']}


def test_comparison_preserves_declaration_and_two_roots(comparison,planner):
    comparison['revision']=comparison['after_record']['current_revision_id']
    task=create(comparison,'compare_measurements',comparison_declaration_id=str(comparison['declaration_id'])).json();result=run_to_end(comparison,task)
    assert result['status'] in {'completed','completed_degraded'},result
    assert [h['action'] for h in result['calls']]==['compare_measurements']
    assert len(result['model_rounds'])==2 and len(result['input_snapshot']['roots'])==2


@pytest.mark.parametrize('mode',['bad','override','ask'])
def test_forged_candidate_arguments_or_question_never_execute(case,planner,mode):
    planner.mode=mode;task=create(case).json();result=run_to_end(case,task)
    assert result['status']=='failed' and result['draft'] is None and not result['calls']
    assert result['model_rounds'][0]['state']=='failed'


def test_early_finish_is_not_forced_into_rule_tools(case,planner):
    planner.mode='finish';task=create(case).json();result=run_to_end(case,task)
    assert result['status']=='refused' and not result['calls'] and len(result['model_rounds'])==1
    assert 'model_insufficient_evidence' in result['draft']['missing_requirements']


def test_configuration_pinned_and_rules_default_remains_available(case,planner,monkeypatch):
    task=create(case).json();monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','ollama');result=run_to_end(case,task)
    assert all(r['provider']=='deepseek' for r in result['model_rounds'])
    monkeypatch.setenv('VIBRADIAG_NUMERIC_EXECUTOR','rules');other=create(case).json();result=run_to_end(case,other)
    assert result['executor']=='rules_graph_v1' and not result['model_called']


def test_confirmation_required_before_first_model(case,planner):
    with transaction() as c:c.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(case['confirmation'])))
    task=create(case).json();execute(task);result=read(case,task)
    assert result['status']=='waiting_information' and not planner.calls and not result['model_rounds']


@pytest.mark.parametrize('window',['decision','tool'])
def test_receipt_before_checkpoint_reuses_without_duplicate_model_or_tool(case,planner,monkeypatch,window):
    original=TaskSaver.put;crashed=False
    def crash(self,*args,**kwargs):
        nonlocal crashed
        with transaction() as c:
            count=c.execute(text('SELECT count(*) FROM '+('agent_model_rounds' if window=='decision' else 'agent_calls')+" WHERE task_id=:id AND state='completed'"),dict(id=self.task_id)).scalar_one()
        if count and not crashed:crashed=True;tasks.recover_interrupted()
        return original(self,*args,**kwargs)
    monkeypatch.setattr(TaskSaver,'put',crash);task=create(case).json();execute(task);paused=read(case,task)
    assert crashed and paused['status']=='interrupted'
    monkeypatch.setattr(TaskSaver,'put',original);assert resume(case,paused).status_code==202
    result=run_to_end(case,task)
    assert result['status']=='completed' and len(result['calls'])==1 and len(planner.calls)==2
    assert len(result['budget']['reservations'])==3


def test_end_before_business_commit_revalidates_without_model_calls(case,planner,monkeypatch):
    original=model_numeric_graph.numeric.finish
    def crash(*args):tasks.recover_interrupted();return original(*args)
    monkeypatch.setattr(model_numeric_graph.numeric,'finish',crash);task=create(case).json();execute(task);paused=read(case,task)
    assert paused['status']=='interrupted' and len(planner.calls)==2
    monkeypatch.setattr(model_numeric_graph.numeric,'finish',original);assert resume(case,paused).status_code==202
    result=run_to_end(case,task);assert result['status']=='completed' and len(planner.calls)==2


def test_unknown_model_outcome_not_retried(case,planner):
    planner.hook=lambda context:tasks.recover_interrupted()
    task=create(case).json();execute(task);paused=read(case,task)
    assert paused['status']=='interrupted';planner.hook=None;assert resume(case,paused).status_code==202
    result=run_to_end(case,task)
    assert result['status']=='failed' and len(planner.calls)==1 and not result['calls']


def test_cancel_during_decision_blocks_tool_and_late_commit(case,planner):
    entered=Event();release=Event()
    def block(context):entered.set();assert release.wait(10)
    planner.hook=block;task=create(case).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker=pool.submit(execute,task)
        try:
            assert entered.wait(10);running=read(case,task)
            response=case['client'].post('/api/v1/agent-tasks/'+task['id']+'/cancel',json=dict(expected_version=running['version']),headers={'Idempotency-Key':str(uuid4())})
            assert response.status_code==200
        finally:release.set()
        worker.result(timeout=10)
    result=read(case,task);assert result['status']=='cancelled' and not result['calls'] and result['draft'] is None


def test_revoked_confirmation_cannot_finish_or_keep_saved_draft(case,planner):
    first=create(case).json();assert run_to_end(case,first)['draft']
    def revoke(context):
        if context['step']==1:
            with transaction() as c:c.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(case['confirmation'])))
    planner.hook=revoke;second=create(case).json();execute(second);result=read(case,second)
    assert result['draft'] is None and result['status']=='failed'
    assert read(case,first)['draft'] is None


def test_partial_finish_preserves_facts_and_names_remaining_tools(case,planner):
    planner.stop_after=1;task=create(case,'bearing_observations').json();result=run_to_end(case,task)
    assert result['status']=='completed_degraded' and len(result['calls'])==1
    assert 'model_finished_with_remaining_candidates' in result['draft']['missing_requirements']


def test_revoked_scope_between_model_and_dispatch_cannot_run_tool(case,planner):
    def revoke(context):
        with transaction() as c:c.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer AND asset_id=:asset'),case)
    planner.hook=revoke;task=create(case).json();execute(task)
    assert case['client'].get('/api/v1/agent-tasks/'+task['id']).status_code==404
    with transaction() as c:
        row=c.execute(text('SELECT status,draft FROM agent_tasks WHERE id=:id'),dict(id=UUID(task['id']))).mappings().one()
        assert row['status']=='failed' and row['draft'] is None
        assert c.execute(text('SELECT count(*) FROM agent_calls WHERE task_id=:id'),dict(id=UUID(task['id']))).scalar_one()==0


def test_explicit_tool_retry_keeps_step_and_reuses_original_decision(case,planner,monkeypatch):
    from vibradiag.infrastructure.persistence.analysis_store import AnalysisStore
    original=AnalysisStore.publish
    def fail(*args,**kwargs):raise OSError('Synthetic storage failure')
    monkeypatch.setattr(AnalysisStore,'publish',fail);task=create(case).json();execute(task);paused=read(case,task)
    assert paused['status']=='interrupted' and paused['calls'][0]['state']=='failed' and len(planner.calls)==1
    monkeypatch.setattr(AnalysisStore,'publish',original);assert resume(case,paused).status_code==202
    result=run_to_end(case,task)
    assert result['status']=='completed' and len(planner.calls)==2
    assert len(result['calls'])==2 and len(result['budget']['reservations'])==4
    assert result['calls'][0]['state']=='failed' and result['calls'][1]['state']=='completed'
    assert result['draft']['observations'][0]['citations'][0]['id']==result['calls'][1]['resource_id']
