import json
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.graph_store import TaskSaver
from vibradiag.services import agent_model_service as models,agent_task_service as tasks
from vibradiag.agents import model_method_graph
from vibradiag.infrastructure.llm.formal_gateway import METHOD_V2_POLICY_SHA256,POLICY_SHA256
from test_agent_knowledge import knowledge,model,prepared,create,execute,read,resume
from test_knowledge_documents import action


@pytest.fixture
def v2(knowledge,model,monkeypatch):
    monkeypatch.setenv('VIBRADIAG_METHOD_EXECUTOR','model_v2');monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    class Fake:
        calls=[];decision='search';generation='quote';hook=None
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            self.calls.append(dict(phase=phase,context=context,schema=schema,cfg=self.cfg))
            hook=type(self).hook
            if hook:hook(phase,context)
            if phase in {'decision','decision_method_v2'}:
                output=dict(action='finish',outcome='insufficient_evidence',rationale='No useful candidate') if self.decision=='finish' else dict(action='choose_action',candidate_id=context['candidates'][0]['candidate_id'],rationale='Retrieve current sources')
                if self.decision=='extra':output['arguments']={}
                if self.decision=='foreign':output['candidate_id']=str(uuid4())
            else:
                output=dict(outcome='insufficient_evidence',selections=[]) if self.generation=='refuse' else dict(outcome='cited_context',selections=[dict(source_id=context['sources'][0]['source_id'],quote=context['sources'][0]['text'][:60])])
            return json.dumps(output),dict(provider=self.cfg.provider,requested_model=self.cfg.model,policy_sha256=self.cfg.policy_sha256,usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
    monkeypatch.setattr(models,'Gateway',Fake)
    revision,index=prepared(knowledge)
    return dict(data=knowledge,fake=Fake,revision=revision,index=index)


def test_v2_uses_explicit_examples_and_real_bound_retrieval(v2):
    data=v2['data'];task=create(data);execute(task);result=read(data,task)
    assert result['status']=='completed_degraded' and result['executor']=='model_method_v2' and result['graph']['version']=='model-method-v2'
    assert [r['phase'] for r in result['model_rounds']]==['decision_method_v2','extractive_method_v2']
    first=v2['fake'].calls[0];ctx=first['context']
    assert ctx['candidates'][0]['tool_kind']=='search_published_knowledge' and 'action' not in ctx['candidates'][0]
    assert set(ctx['response_examples'][0])=={'action','candidate_id','rationale'}
    assert set(ctx['response_examples'][1])=={'action','outcome','rationale'}
    assert ctx['response_examples'][0]['candidate_id']==ctx['candidates'][0]['candidate_id']
    assert 'AskInformation' not in first['schema']['$defs']
    assert all(r['cfg'].policy_sha256==METHOD_V2_POLICY_SHA256 for r in v2['fake'].calls)
    assert result['draft']['publication_state']=='unpublished_draft' and result['draft']['missing_requirements']==['knowledge_relevance_not_qualified']


@pytest.mark.parametrize('decision',['extra','foreign','finish'])
def test_bad_object_or_legal_finish_does_not_dispatch(v2,decision):
    data=v2['data'];v2['fake'].decision=decision;task=create(data);execute(task);result=read(data,task)
    assert result['status']=='refused' and not result['calls'] and len(v2['fake'].calls)==1
    assert len(result['budget']['reservations'])==1
    if decision=='extra':
        assert result['model_rounds'][0]['state']=='failed'
        assert result['model_rounds'][0]['validation_diagnostics']['issues'][0]['kind']=='extra_forbidden'
    if decision=='finish':assert result['error_code']=='MODEL_FINISHED_WITHOUT_RETRIEVAL'


def test_model_may_refuse_sources_without_quote(v2):
    data=v2['data'];v2['fake'].generation='refuse';task=create(data);execute(task);result=read(data,task)
    assert result['status']=='refused' and result['error_code']=='MODEL_NO_SUPPORTED_EXTRACT'
    assert result['draft']['observations']==[] and len(v2['fake'].calls)==2


def test_v1_and_v2_keep_accepted_configs_after_default_changes(v2,monkeypatch):
    data=v2['data'];new=create(data)
    monkeypatch.setenv('VIBRADIAG_METHOD_EXECUTOR','model');old=create(data)
    monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','ollama');monkeypatch.setenv('VIBRADIAG_METHOD_EXECUTOR','rules')
    execute(old);execute(new)
    assert read(data,old)['graph']['version']=='model-method-v1' and read(data,new)['graph']['version']=='model-method-v2'
    assert [c['phase'] for c in v2['fake'].calls]==['decision','extractive_draft','decision_method_v2','extractive_method_v2']
    assert [c['cfg'].policy_sha256 for c in v2['fake'].calls]==[POLICY_SHA256,POLICY_SHA256,METHOD_V2_POLICY_SHA256,METHOD_V2_POLICY_SHA256]
    assert all(c['cfg'].provider=='deepseek' for c in v2['fake'].calls)


def test_wrong_phase_is_rejected_without_reserving_or_calling(v2):
    data=v2['data'];task=create(data);claimed=tasks.claim_next(UUID(task['id']))
    with pytest.raises(MeasurementError) as caught:models.run_round(claimed['id'],claimed['lease_token'],data['principal'],'decision')
    assert caught.value.code=='MODEL_PHASE_INVALID' and not v2['fake'].calls
    assert read(data,task)['budget']['reservations']==[]
    with transaction() as c:
        row=tasks._running(c,claimed['id'],claimed['lease_token']);tasks._change(c,row,'cancelled')


def test_revoked_asset_scope_hides_v2_task(v2):
    data=v2['data'];task=create(data,scope=dict(equipment='motor',component='bearing',asset_id=str(data['asset'])))
    execute(task);assert read(data,task)['draft']
    with transaction() as c:c.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer AND asset_id=:asset'),data)
    assert data['client'].get('/api/v1/agent-tasks/'+task['id']).status_code==404


def test_withdrawal_after_generation_hides_new_and_existing_draft(v2):
    data=v2['data'];first=create(data);execute(first);assert read(data,first)['draft']
    def withdraw(phase,context):
        if phase=='extractive_method_v2':assert action(data,v2['revision'],'withdraw').status_code==200
    v2['fake'].hook=withdraw;second=create(data);execute(second);result=read(data,second)
    assert result['status']=='refused' and result['draft'] is None and not result['knowledge_references']
    assert result['model_rounds'][-1]['state']=='failed' and read(data,first)['draft'] is None


def test_cancel_during_generation_fences_late_result(v2):
    data=v2['data'];task=create(data)
    def cancel(phase,context):
        if phase=='extractive_method_v2':
            current=read(data,task);tasks.cancel_task(UUID(task['id']),current['version'],str(uuid4()),data['principal'])
    v2['fake'].hook=cancel;execute(task);result=read(data,task)
    assert result['status']=='cancelled' and result['draft'] is None and result['model_rounds'][-1]['state']=='interrupted'


@pytest.mark.parametrize('window',['decision','knowledge','end'])
def test_recovery_reuses_actual_ledgers_and_version(v2,monkeypatch,window):
    data=v2['data'];task=create(data);original=TaskSaver.put;finish=model_method_graph.finish_task;crashed=False
    def crash(self,*args,**kwargs):
        nonlocal crashed
        if not crashed and window!='end':
            with transaction() as c:
                ready=c.execute(text("SELECT count(*) FROM "+('agent_model_rounds' if window=='decision' else 'agent_calls')+" WHERE task_id=:id AND state='completed'"),dict(id=self.task_id)).scalar_one()
            if ready:crashed=True;tasks.recover_interrupted()
        return original(self,*args,**kwargs)
    def lost(*args,**kwargs):
        nonlocal crashed
        crashed=True;tasks.recover_interrupted();return finish(*args,**kwargs)
    monkeypatch.setattr(TaskSaver,'put',crash)
    if window=='end':monkeypatch.setattr(model_method_graph,'finish_task',lost)
    execute(task);paused=read(data,task);assert crashed and paused['status']=='interrupted'
    monkeypatch.setattr(TaskSaver,'put',original);monkeypatch.setattr(model_method_graph,'finish_task',finish)
    assert resume(data,paused).status_code==202
    execute(task);result=read(data,task)
    assert result['status']=='completed_degraded' and result['graph']['version']=='model-method-v2'
    assert len(v2['fake'].calls)==2 and len(result['calls'])==1 and len(result['budget']['reservations'])==3
