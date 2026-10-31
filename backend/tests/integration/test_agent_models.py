import json
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from vibradiag.agents.formal_policy import PolicyStop
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services import agent_model_service as models,agent_task_service as tasks,agent_knowledge_service as knowledge_service
from test_agent_knowledge import knowledge,model,prepared,create,read
from test_knowledge_documents import action


@pytest.fixture
def rounds(knowledge,model,monkeypatch):
    class Fake:
        calls=[];hook=None;replacement=None
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            self.calls.append((phase,context,self.cfg))
            if self.hook:self.hook(phase,context)
            output=self.replacement or (dict(action='choose_action',candidate_id=context['candidates'][0]['candidate_id'],rationale='Read current method context') if phase=='decision' else
                dict(outcome='cited_context',selections=[dict(source_id=context['sources'][0]['source_id'],quote=context['sources'][0]['text'][:70])]))
            return json.dumps(output),dict(provider=self.cfg.provider,requested_model=self.cfg.model,served_model=self.cfg.model,usage=dict(prompt_tokens=50,completion_tokens=30))
    monkeypatch.setattr(models,'Gateway',Fake)
    revision,_=prepared(knowledge);task=create(knowledge);claimed=tasks.claim_next(UUID(task['id']))
    data=dict(knowledge,task=task,claimed=claimed,revision=revision,fake=Fake)
    yield data
    with transaction() as c:
        row=c.execute(text('SELECT * FROM agent_tasks WHERE id=:id'),dict(id=UUID(task['id']))).mappings().one()
        if row['status'] not in tasks.TERMINAL:tasks._change(c,row,'cancelled',error='TEST_CLEANUP')


def run(data,phase='decision',**kwargs):return models.run_round(UUID(data['task']['id']),data['claimed']['lease_token'],data['principal'],phase,**kwargs)
def retrieve(data):
    payload=knowledge_service.prepare(UUID(data['task']['id']),data['claimed']['lease_token'],data['principal'])
    return tasks._dispatch(UUID(data['task']['id']),data['claimed']['lease_token'],'search_published_knowledge',payload.model_dump(mode='json'),data['principal'])


def test_reserved_bound_decision_replay_and_no_automatic_tool_execution(rounds):
    first=run(rounds);second=run(rounds)
    assert first['state']=='completed' and second['id']==first['id'] and second['reused']
    assert len(rounds['fake'].calls)==1 and not first['executed_decision']
    result=read(rounds,rounds['task'])
    assert result['model_called'] and result['calls']==[] and result['draft'] is None
    assert len(result['budget']['reservations'])==1 and result['budget']['reservations'][0]['kind']=='model'


def test_local_cloud_choice_is_pinned_per_task_no_midtask_fallback(rounds,monkeypatch):
    run(rounds,provider='ollama');monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    assert run(rounds)['reused']
    with pytest.raises(MeasurementError) as caught:run(rounds,provider='deepseek')
    assert caught.value.code=='MODEL_CONFIGURATION_PINNED' and len(rounds['fake'].calls)==1


def test_extractive_draft_requires_bound_current_sources_and_does_not_publish(rounds):
    with pytest.raises(MeasurementError) as caught:run(rounds,'extractive_draft')
    assert caught.value.code=='MODEL_SOURCE_REQUIRED'
    retrieve(rounds);output=run(rounds,'extractive_draft')
    assert output['output']['selections'] and not output['published_draft']
    phase,context,cfg=rounds['fake'].calls[-1]
    assert set(context)=={'question','sources'} and all(set(s)=={'source_id','text','untrusted_content'} for s in context['sources'])
    result=read(rounds,rounds['task']);assert result['status']=='running' and result['draft'] is None


@pytest.mark.parametrize('output',[dict(action='choose_action',candidate_id=str(uuid4()),rationale='wrong'),
    dict(action='choose_action',candidate_id=str(uuid4()),rationale='wrong',tool_args={'url':'https://example.invalid'}),
    dict(action='finish',outcome='analysis_only',rationale='unsupported')])
def test_invalid_decision_spends_reservation_and_cannot_replay_as_success(rounds,output):
    rounds['fake'].replacement=output
    with pytest.raises(MeasurementError):run(rounds)
    result=read(rounds,rounds['task'])
    assert result['model_rounds'][0]['state']=='failed' and len(result['budget']['reservations'])==1
    with pytest.raises(MeasurementError) as caught:run(rounds)
    assert caught.value.code=='MODEL_ROUND_CLOSED_OR_UNKNOWN' and len(rounds['fake'].calls)==1


def test_paraphrase_or_foreign_source_is_not_a_grounded_quote(rounds):
    retrieve(rounds);source=rounds['client'].get('/api/v1/agent-tasks/'+rounds['task']['id']).json()['knowledge_references'][0]
    rounds['fake'].replacement=dict(outcome='cited_context',selections=[dict(source_id=source['id'],quote='invented fault conclusion')])
    with pytest.raises(MeasurementError) as caught:run(rounds,'extractive_draft')
    assert caught.value.code=='MODEL_QUOTE_UNGROUNDED'


def test_cancellation_fences_late_model_output(rounds):
    def cancel(*args):
        current=read(rounds,rounds['task'])
        tasks.cancel_task(UUID(current['id']),current['version'],str(uuid4()),rounds['principal'])
    rounds['fake'].hook=cancel
    with pytest.raises(tasks.Fenced):run(rounds)
    result=read(rounds,rounds['task'])
    assert result['status']=='cancelled' and result['model_rounds'][0]['state']=='interrupted' and result['draft'] is None


def test_withdrawn_during_generation_does_not_commit_output(rounds):
    retrieve(rounds)
    def withdraw(*args):assert action(rounds,rounds['revision'],'withdraw').status_code==200
    rounds['fake'].hook=withdraw
    with pytest.raises(MeasurementError) as caught:run(rounds,'extractive_draft')
    assert caught.value.code=='MODEL_SOURCE_UNAVAILABLE'
    with transaction() as c:assert c.execute(text('SELECT validated_output FROM agent_model_rounds WHERE task_id=:id'),dict(id=UUID(rounds['task']['id']))).scalar_one() is None


def test_model_budget_exhaustion_blocks_provider_before_dispatch(rounds,monkeypatch):
    with transaction() as c:
        row=tasks._running(c,UUID(rounds['task']['id']),rounds['claimed']['lease_token'])
        tasks._change(c,row,'running',budget=dict(policy_version='agent-budget-v1',active_ms=0,reservations=[dict(kind='model',key=f'{i:064x}',retries_used=0) for i in range(8)]))
    with pytest.raises(PolicyStop):run(rounds)
    assert not rounds['fake'].calls
    def no_runtime():raise MeasurementError('ANALYSIS_UNAVAILABLE','No owned execution lease',status=503)
    monkeypatch.setattr(tasks.runtime,'ensure',no_runtime)
    with pytest.raises(MeasurementError) as caught:run(rounds)
    assert caught.value.code=='ANALYSIS_UNAVAILABLE' and not rounds['fake'].calls


def test_history_and_configuration_are_immutable_and_context_is_minimal(rounds):
    run(rounds)
    with transaction() as c:
        row=c.execute(text('SELECT * FROM agent_model_rounds WHERE task_id=:id'),dict(id=UUID(rounds['task']['id']))).mappings().one()
        assert set(row['context'])=={'question','candidates','allowed_finish'}
    for statement in ['DELETE FROM agent_model_rounds WHERE task_id=:id',
                      "UPDATE agent_model_rounds SET context='{}' WHERE task_id=:id",
                      'DELETE FROM agent_model_bindings WHERE task_id=:id']:
        with pytest.raises(DBAPIError),transaction() as c:c.execute(text(statement),dict(id=UUID(rounds['task']['id'])))


def test_unknown_response_recovery_does_not_refund_or_free_retry(rounds):
    def lost(*args):tasks.recover_interrupted()
    rounds['fake'].hook=lost
    with pytest.raises(tasks.Fenced):run(rounds)
    result=read(rounds,rounds['task'])
    assert result['status']=='interrupted' and result['model_rounds'][0]['state']=='interrupted'
    assert len(result['budget']['reservations'])==1 and result['model_call_activity']=='unknown'
