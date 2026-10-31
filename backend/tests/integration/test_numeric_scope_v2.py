import json
from uuid import UUID,uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from sqlalchemy import text
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.graph_store import TaskSaver
from vibradiag.services import agent_task_service as tasks,agent_model_service as models
from vibradiag.agents import model_numeric_graph
from test_tool_runs import case
from test_matching_runs import matching
from test_agent_tasks import create,read,resume,execute,manual_executor
from test_model_numeric_graph import run_to_end


@pytest.fixture
def planner(monkeypatch):
    monkeypatch.setenv('VIBRADIAG_NUMERIC_EXECUTOR','model_v2');monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    class Fake:
        calls=[];finish=False;hook=None
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            self.calls.append(dict(phase=phase,context=context,cfg=self.cfg));hook=type(self).hook
            if hook:hook(context)
            frequency=next((r for r in context['completed'] if r['action']=='calculate_bearing_frequencies'),None)
            stop=frequency and frequency.get('frequency_uncertainty_complete') is False
            if self.finish or stop or not context['candidates']:output=dict(action='finish',outcome='insufficient_evidence' if stop or self.finish else 'analysis_only',rationale='Honor actual scoped condition')
            else:
                item=next((r for r in context['candidates'] if r['action']=='calculate_bearing_frequencies'),context['candidates'][0])
                output=dict(action='choose_action',candidate_id=item['candidate_id'],rationale='Offered exploratory step, not diagnosis')
            return json.dumps(output),dict(provider=self.cfg.provider,requested_model=self.cfg.model)
    monkeypatch.setattr(models,'Gateway',Fake);return Fake


def test_complete_frequency_continues_past_envelope_diagnostic_limits(matching,planner):
    task=create(matching,'bearing_observations').json();result=run_to_end(matching,task)
    assert result['status']=='completed_degraded',result
    assert result['executor']=='model_numeric_v2' and result['graph']['version']=='model-numeric-v2'
    assert len(result['calls'])==5 and len(result['model_rounds'])==6
    assert all(r['phase']=='numeric_decision_v2' for r in result['model_rounds'])
    step=next(c['context'] for c in planner.calls if c['context']['step']==3)
    assert step['completed'][1]['frequency_uncertainty_complete'] is True
    assert 'qualified_numeric_profile' in step['completed'][2]['requirement_scopes']['diagnostic_release_limitations']
    assert step['candidates'] and not step['candidates'][0]['diagnostic_release_allowed']
    assert 'diagnostic_profile_not_qualified' in result['draft']['missing_requirements']


def test_incomplete_frequency_can_still_stop_without_forcing_remaining_tool(case,planner):
    task=create(case,'bearing_observations').json();result=run_to_end(case,task)
    assert result['status']=='completed_degraded' and [c['action'] for c in result['calls']]==['analyze_basic','calculate_bearing_frequencies']
    assert len(result['model_rounds'])==3 and 'model_finished_with_remaining_candidates' in result['draft']['missing_requirements']


def test_refusal_is_not_overridden_by_scoped_context(case,planner):
    planner.finish=True;task=create(case).json();result=run_to_end(case,task)
    assert result['status']=='refused' and not result['calls'] and len(planner.calls)==1


def test_v1_tasks_keep_original_context_and_policy_after_default_changes(case,planner,monkeypatch):
    monkeypatch.setenv('VIBRADIAG_NUMERIC_EXECUTOR','model');task=create(case).json()
    monkeypatch.setenv('VIBRADIAG_NUMERIC_EXECUTOR','model_v2');result=run_to_end(case,task)
    assert result['executor']=='model_numeric_v1' and result['graph']['version']=='model-numeric-v1'
    assert all(c['phase']=='numeric_decision' for c in planner.calls)
    assert all('requirement_semantics' not in c['context'] for c in planner.calls)


@pytest.mark.parametrize('window',['decision','tool'])
def test_v2_completed_receipt_recovery_reuses_same_model_and_tool(case,planner,monkeypatch,window):
    original=TaskSaver.put;crashed=False
    def crash(self,*args,**kwargs):
        nonlocal crashed
        with transaction() as c:count=c.execute(text('SELECT count(*) FROM '+('agent_model_rounds' if window=='decision' else 'agent_calls')+" WHERE task_id=:id AND state='completed'"),dict(id=self.task_id)).scalar_one()
        if count and not crashed:crashed=True;tasks.recover_interrupted()
        return original(self,*args,**kwargs)
    monkeypatch.setattr(TaskSaver,'put',crash);task=create(case).json();execute(task);paused=read(case,task)
    assert paused['status']=='interrupted' and crashed
    monkeypatch.setattr(TaskSaver,'put',original);assert resume(case,paused).status_code==202
    result=run_to_end(case,task);assert result['status']=='completed' and len(planner.calls)==2 and len(result['calls'])==1


def test_v2_cancel_during_model_still_fences_late_result(case,planner):
    entered=Event();release=Event()
    def block(context):entered.set();assert release.wait(10)
    planner.hook=block;task=create(case).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker=pool.submit(execute,task)
        try:
            assert entered.wait(10);running=read(case,task)
            response=case['client'].post('/api/v1/agent-tasks/'+task['id']+'/cancel',json=dict(expected_version=running['version']),headers={'Idempotency-Key':str(uuid4())});assert response.status_code==200
        finally:release.set()
        worker.result(timeout=10)
    result=read(case,task);assert result['status']=='cancelled' and not result['calls'] and result['draft'] is None


def test_new_v2_configuration_pinned_at_acceptance(case,planner,monkeypatch):
    task=create(case).json();monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','ollama');result=run_to_end(case,task)
    assert result['status']=='completed' and all(r['provider']=='deepseek' for r in result['model_rounds'])


def test_v2_wrong_phase_cannot_reserve_or_call_provider(case,planner):
    from vibradiag.domain.measurement_errors import MeasurementError
    task=create(case).json();claimed=tasks.claim_next(UUID(task['id']))
    with pytest.raises(MeasurementError) as error:models.run_round(claimed['id'],claimed['lease_token'],case['principal'],'numeric_decision',step=0)
    assert error.value.code=='NUMERIC_MODEL_PHASE_MISMATCH' and not planner.calls
    tasks.execute_claimed(claimed);assert read(case,task)['status']=='completed'


def test_v2_end_checkpoint_revalidates_final_receipt_without_new_model(case,planner,monkeypatch):
    original=model_numeric_graph.numeric.finish
    def crash(*args):tasks.recover_interrupted();return original(*args)
    monkeypatch.setattr(model_numeric_graph.numeric,'finish',crash);task=create(case).json();execute(task);paused=read(case,task)
    assert paused['status']=='interrupted' and len(planner.calls)==2
    monkeypatch.setattr(model_numeric_graph.numeric,'finish',original);assert resume(case,paused).status_code==202
    result=run_to_end(case,task);assert result['status']=='completed' and len(planner.calls)==2
