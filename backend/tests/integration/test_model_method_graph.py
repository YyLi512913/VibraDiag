from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.graph_store import TaskSaver
from vibradiag.services import agent_task_service as tasks,agent_model_service as models,model_method_service as final
from vibradiag.agents import model_method_graph
from test_agent_knowledge import knowledge,model,prepared,create,execute,read,resume
from test_knowledge_documents import action


@pytest.fixture
def gateway(knowledge,model,monkeypatch):
    monkeypatch.setenv('VIBRADIAG_METHOD_EXECUTOR','model');monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    class Fake:
        calls=[];decision='search';generation='quote';hook=None
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            self.calls.append(dict(phase=phase,context=context,configuration=self.cfg))
            hook=type(self).hook
            if hook:hook(phase,context)
            if phase=='decision':
                if self.decision=='fail':raise MeasurementError('MODEL_PROVIDER_UNAVAILABLE','safe fixture',status=503)
                if self.decision=='finish':output=dict(action='finish',outcome='insufficient_evidence',rationale='Cannot use offered context')
                else:output=dict(action='choose_action',candidate_id=context['candidates'][0]['candidate_id'] if self.decision=='search' else str(uuid4()),rationale='Get authorized method evidence')
            else:
                if self.generation=='fail':raise MeasurementError('MODEL_PROVIDER_UNAVAILABLE','safe fixture',status=503)
                output=dict(outcome='insufficient_evidence',selections=[]) if self.generation=='refuse' else dict(outcome='cited_context',selections=[dict(source_id=context['sources'][0]['source_id'],quote=context['sources'][0]['text'][:60] if self.generation=='quote' else 'invented facts')])
            return json.dumps(output),dict(provider=self.cfg.provider,requested_model=self.cfg.model,served_model=self.cfg.model,usage=dict(prompt_tokens=40,completion_tokens=30))
    monkeypatch.setattr(models,'Gateway',Fake)
    revision,run=prepared(knowledge);model.calls.clear()
    return dict(data=knowledge,model=model,fake=Fake,revision=revision,index=run)


def test_model_selects_search_then_grounded_unpublished_extract(gateway):
    data=gateway['data'];task=create(data);execute(task);result=read(data,task)
    assert result['executor']=='model_method_v1' and result['graph']['version']=='model-method-v1'
    assert result['status']=='completed_degraded' and result['model_called'] and result['embedding_called']
    assert [r['phase'] for r in result['model_rounds']]==['decision','extractive_draft']
    assert [c['action'] for c in result['calls']]==['search_published_knowledge']
    assert [r['kind'] for r in result['budget']['reservations']].count('model')==2 and len(result['budget']['reservations'])==3
    observation=result['draft']['observations'][0]
    assert observation['text'].startswith(final.QUOTE_PREFIX)
    source=next(h for h in result['knowledge_references'] if h['id']==observation['citations'][0]['id'])
    assert observation['text'][len(final.QUOTE_PREFIX):] in source['text']
    assert result['draft']['missing_requirements']==['knowledge_relevance_not_qualified']
    assert result['draft']['publication_state']=='unpublished_draft'


def test_model_finish_does_not_force_tool_or_generation(gateway):
    gateway['fake'].decision='finish';data=gateway['data'];task=create(data);execute(task);result=read(data,task)
    assert result['status']=='refused' and result['calls']==[] and not result['embedding_called']
    assert len(result['model_rounds'])==1 and result['draft']['missing_requirements']==['model_insufficient_evidence']
    assert not gateway['model'].calls


@pytest.mark.parametrize('mode',['bad','fail'])
def test_invalid_or_unavailable_decision_does_not_dispatch(gateway,mode):
    gateway['fake'].decision=mode;data=gateway['data'];task=create(data);execute(task);result=read(data,task)
    assert result['status']=='refused' and result['calls']==[]
    assert result['draft']['observations']==[] and result['error_code']=='MODEL_DECISION_UNAVAILABLE'
    assert result['model_rounds'][0]['state']=='failed'


@pytest.mark.parametrize('mode',['bad','fail'])
def test_failed_generation_preserves_only_server_marked_context(gateway,mode):
    gateway['fake'].generation=mode;data=gateway['data'];task=create(data);execute(task);result=read(data,task)
    assert result['status']=='completed_degraded' and result['error_code']=='MODEL_GENERATION_UNAVAILABLE'
    assert all(o['text']==final.CONTEXT_TEXT for o in result['draft']['observations'])
    assert 'model_generation_unavailable' in result['draft']['missing_requirements']
    assert result['model_rounds'][-1]['state']=='failed'


def test_model_can_refuse_given_sources_without_inventing_extract(gateway):
    gateway['fake'].generation='refuse';data=gateway['data'];task=create(data);execute(task);result=read(data,task)
    assert result['status']=='refused' and result['draft']['observations']==[]
    assert result['error_code']=='MODEL_NO_SUPPORTED_EXTRACT'


def test_scope_clarification_precedes_any_model_call(gateway):
    data=gateway['data'];task=create(data,scope=False);execute(task);paused=read(data,task)
    assert paused['status']=='waiting_information' and paused['model_rounds']==[] and not gateway['fake'].calls
    choice=next(item['id'] for item in paused['question']['choices'] if item['equipment']=='motor')
    assert resume(data,paused,choice).status_code==202
    execute(task);assert read(data,task)['status']=='completed_degraded'


def test_configuration_is_pinned_at_acceptance_and_rules_stay_available(gateway,monkeypatch):
    data=gateway['data'];task=create(data)
    assert task['input_snapshot']['model_configuration']['provider']=='deepseek'
    monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','ollama');execute(task)
    assert all(call['configuration'].provider=='deepseek' for call in gateway['fake'].calls)
    monkeypatch.setenv('VIBRADIAG_METHOD_EXECUTOR','rules')
    legacy=create(data);execute(legacy);old=read(data,legacy)
    assert old['executor']=='rules_knowledge_v1' and not old['model_called']


def test_cancel_while_model_waits_cannot_commit_a_quote(gateway):
    data=gateway['data'];entered=Event();release=Event()
    def block(phase,context):
        if phase=='extractive_draft':entered.set();assert release.wait(10)
    gateway['fake'].hook=block;task=create(data)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker=pool.submit(execute,task)
        try:
            assert entered.wait(10);running=read(data,task)
            response=data['client'].post('/api/v1/agent-tasks/'+task['id']+'/cancel',json=dict(expected_version=running['version']),headers={'Idempotency-Key':str(uuid4())})
            assert response.status_code==200,response.text
        finally:release.set()
        worker.result(timeout=10)
    result=read(data,task)
    assert result['status']=='cancelled' and result['draft'] is None and result['model_rounds'][-1]['state']=='interrupted'


def test_withdrawal_during_generation_refuses_and_saved_draft_invalidates(gateway):
    data=gateway['data'];first=create(data);execute(first);assert read(data,first)['draft']
    def withdraw(phase,context):
        if phase=='extractive_draft':assert action(data,gateway['revision'],'withdraw').status_code==200
    gateway['fake'].hook=withdraw;second=create(data);execute(second);result=read(data,second)
    assert result['status']=='refused' and result['knowledge_references']==[]
    assert read(data,first)['draft'] is None


def test_model_receipt_commit_before_checkpoint_reuses_without_second_call(gateway,monkeypatch):
    data=gateway['data'];original=TaskSaver.put;crashed=False
    def crash(self,*args,**kwargs):
        nonlocal crashed
        if not crashed:
            with transaction() as c:completed=c.execute(text("SELECT count(*) FROM agent_model_rounds WHERE task_id=:id AND phase='decision' AND state='completed'"),dict(id=self.task_id)).scalar_one()
            if completed:crashed=True;tasks.recover_interrupted()
        return original(self,*args,**kwargs)
    monkeypatch.setattr(TaskSaver,'put',crash)
    task=create(data);execute(task);paused=read(data,task)
    assert crashed and paused['status']=='interrupted'
    monkeypatch.setattr(TaskSaver,'put',original);assert resume(data,paused).status_code==202
    execute(task);result=read(data,task)
    assert result['status']=='completed_degraded'
    assert [call['phase'] for call in gateway['fake'].calls].count('decision')==1
    assert len(result['budget']['reservations'])==3


def test_end_before_task_commit_recovers_only_from_ledgers(gateway,monkeypatch):
    data=gateway['data'];original=model_method_graph.finish_task
    def interrupted(*args,**kwargs):tasks.recover_interrupted();return original(*args,**kwargs)
    monkeypatch.setattr(model_method_graph,'finish_task',interrupted)
    task=create(data);execute(task);paused=read(data,task)
    assert paused['status']=='interrupted' and len(gateway['fake'].calls)==2
    monkeypatch.setattr(model_method_graph,'finish_task',original)
    assert resume(data,paused).status_code==202
    execute(task);assert read(data,task)['status']=='completed_degraded' and len(gateway['fake'].calls)==2


def test_unknown_model_outcome_is_not_retried_or_used_as_success(gateway):
    data=gateway['data']
    def recover(phase,context):
        if phase=='decision':tasks.recover_interrupted()
    gateway['fake'].hook=recover;task=create(data);execute(task);paused=read(data,task)
    assert paused['status']=='interrupted' and paused['model_rounds'][0]['state']=='interrupted'
    gateway['fake'].hook=None;assert resume(data,paused).status_code==202
    execute(task);result=read(data,task)
    assert result['status']=='refused' and len(gateway['fake'].calls)==1 and result['calls']==[]


def test_unknown_executor_never_falls_back_to_legacy_rules(gateway):
    data=gateway['data'];template=create(data);identifier=uuid4()
    with transaction() as c:
        row=c.execute(text('SELECT * FROM agent_tasks WHERE id=:id'),dict(id=UUID(template['id']))).mappings().one()
        snapshot=dict(row['input_snapshot'],executor='unsupported_future_executor')
        c.execute(text('''INSERT INTO agent_tasks(id,organization_id,requested_by,session_id,goal,payload,payload_hash,input_snapshot,input_fingerprint)
            VALUES (:id,:org,:actor,:session,'explain_method',CAST(:payload AS jsonb),:hash,CAST(:snapshot AS jsonb),:fingerprint)'''),
            dict(id=identifier,org=row['organization_id'],actor=row['requested_by'],session=row['session_id'],payload=json.dumps(row['payload']),
                hash=row['payload_hash'],snapshot=json.dumps(snapshot),fingerprint=tasks.fingerprint(snapshot)))
    task=dict(id=str(identifier));execute(task);result=read(data,task)
    assert result['status']=='failed' and result['error_code']=='TASK_EXECUTOR_UNSUPPORTED' and result['calls']==[]
    assert not gateway['fake'].calls
