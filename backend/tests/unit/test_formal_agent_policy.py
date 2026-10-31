from uuid import uuid4
import pytest
from pydantic import TypeAdapter,ValidationError
from vibradiag.agents.formal_contracts import (CreateAgentTask,ResumeAgentTask,AgentDecision,
    ExecutionBudget,TaskScope,CandidateAction,CandidateQuestion,ResolvedReference,ExploratoryDraft)
from vibradiag.agents.formal_policy import (PolicyStop,logical_call_key,reserve_call,reserve_retry,
    charge_active_time,validate_decision,validate_draft_references)


def key(name):return logical_call_key('test-task','a'*64,{'name':name})


def scope():return TaskScope(task_id=uuid4(),organization_id=uuid4(),measurement_revision_ids=(uuid4(),))


def candidate(s):return CandidateAction(id=uuid4(),task_id=s.task_id,organization_id=s.organization_id,
    measurement_revision_ids=s.measurement_revision_ids,kind='read_basic_analysis',logical_call_key=key('basic'))


def draft(reference_id,kind='fact',claim='numeric_fact'):
    return ExploratoryDraft(status='analysis_only',summary='未发布的分析草稿',
        observations=({'claim_kind':claim,'text':'由已登记来源渲染的观察','citations':({'kind':kind,'id':reference_id},)},),
        missing_requirements=(),limitations=('探索观察，不能据此确诊。',),next_steps=())


@pytest.mark.parametrize('extra',[{'organization_id':str(uuid4())},{'checked':True},{'budget':999},
    {'model':'arbitrary-model'},{'file_path':'C:/private.csv'}])
def test_public_create_forbids_authority_or_runtime_overrides(extra):
    with pytest.raises(ValidationError):CreateAgentTask(goal='inspect_signal',description='查看频谱',measurement_revision_id=uuid4(),**extra)


def test_task_goal_and_input_binding_and_resume_version_are_explicit():
    assert CreateAgentTask(goal='explain_method',description='包络是什么').measurement_revision_id is None
    with pytest.raises(ValidationError):CreateAgentTask(goal='inspect_signal',description='查看频谱')
    with pytest.raises(ValidationError):CreateAgentTask(goal='compare_measurements',description='比较',measurement_revision_id=uuid4())
    with pytest.raises(ValidationError):ResumeAgentTask(expected_version=True,question_id=uuid4(),answer='补充')


def test_model_selects_only_server_candidate_id_and_cannot_send_tool_arguments():
    s=scope();c=candidate(s)
    decision={'action':'choose_action','candidate_id':c.id,'rationale':'先查基础分析'}
    assert validate_decision(decision,s,(c,),()).candidate_id==c.id
    with pytest.raises(ValidationError):TypeAdapter(AgentDecision).validate_python(decision|{'tool_name':'analyze_envelope','checked':True})
    with pytest.raises(PolicyStop,match='UNKNOWN'):validate_decision(decision|{'candidate_id':uuid4()},s,(c,),())
    with pytest.raises(PolicyStop,match='DUPLICATE'):validate_decision(decision,s,(c,c),())


@pytest.mark.parametrize('field',['task_id','organization_id','measurement_revision_ids'])
def test_other_task_org_or_input_candidates_cannot_be_selected(field):
    s=scope();c=candidate(s);data=c.model_dump();data[field]=(uuid4(),) if field=='measurement_revision_ids' else uuid4()
    wrong=CandidateAction.model_validate(data)
    with pytest.raises(PolicyStop,match='SCOPE_MISMATCH'):
        validate_decision({'action':'choose_action','candidate_id':wrong.id,'rationale':'选择'},s,(wrong,),())


def test_questions_are_registered_and_task_scoped():
    s=scope();q=CandidateQuestion(id=uuid4(),task_id=s.task_id,organization_id=s.organization_id,code='target_context_required')
    decision={'action':'ask_information','question_id':q.id,'rationale':'缺少目标上下文'}
    assert validate_decision(decision,s,(),(q,)).question_id==q.id
    other=scope()
    with pytest.raises(PolicyStop,match='QUESTION_SCOPE'):validate_decision(decision,other,(),(q,))


@pytest.mark.parametrize('kind,limit',[('model',8),('tool',20)])
def test_budget_counts_reservations_not_response_success_and_replay_is_free(kind,limit):
    budget=ExecutionBudget()
    for i in range(limit):budget,new=reserve_call(budget,kind,key(str(i)));assert new
    same,new=reserve_call(budget,kind,key('0'));assert not new and same==budget
    with pytest.raises(PolicyStop,match='CALL_BUDGET'):reserve_call(budget,kind,key('extra'))
    assert len(budget.reservations)==limit


def test_retry_is_bounded_and_does_not_reset_call_or_active_budget():
    budget,_=reserve_call(ExecutionBudget(),'model',key('one'));budget=charge_active_time(budget,5000)
    retried=reserve_retry(budget,key('one'))
    assert len(retried.reservations)==1 and retried.active_ms==5000
    assert retried.reservations[0].retries_used==1
    with pytest.raises(PolicyStop,match='RETRY_BUDGET'):reserve_retry(retried,key('one'))
    with pytest.raises(PolicyStop,match='NOT_RESERVED'):reserve_retry(budget,key('absent'))
    with pytest.raises(PolicyStop,match='CONFLICT'):reserve_call(budget,'tool',key('one'))


def test_active_time_saturates_and_serialized_resume_does_not_reset_it():
    budget=charge_active_time(ExecutionBudget(),119999)
    resumed=ExecutionBudget.model_validate_json(budget.model_dump_json())
    assert resumed.active_ms==119999
    exhausted=charge_active_time(resumed,100)
    assert exhausted.active_ms==120000
    with pytest.raises(PolicyStop,match='ACTIVE_TIME'):reserve_call(exhausted,'tool',key('after'))
    with pytest.raises(PolicyStop,match='ELAPSED'):charge_active_time(budget,True)
    with pytest.raises(PolicyStop,match='ELAPSED'):charge_active_time(budget,-1)


def test_model_copy_cannot_bypass_budget_validation():
    forged=ExecutionBudget().model_copy(update={'active_ms':999999})
    with pytest.raises(ValidationError):reserve_call(forged,'model',key('forged'))


def test_stable_call_key_changes_with_task_input_or_action():
    assert logical_call_key('task','a'*64,{'b':2,'a':1})==logical_call_key('task','a'*64,{'a':1,'b':2})
    assert key('one')!=key('two')
    assert logical_call_key('task-a','a'*64,{})!=logical_call_key('task-b','a'*64,{})
    assert logical_call_key('task','a'*64,{})!=logical_call_key('task','b'*64,{})


def test_citations_must_resolve_current_authorized_input_scoped_resources():
    s=scope();identifier=uuid4();d=draft(identifier)
    r=ResolvedReference(kind='fact',id=identifier,organization_id=s.organization_id,
        measurement_revision_ids=s.measurement_revision_ids,permitted_claim='numeric_fact',current_and_valid=True)
    assert validate_draft_references(d,s,(r,)).publication_state=='unpublished_draft'
    with pytest.raises(PolicyStop,match='UNRESOLVED'):validate_draft_references(d,s,())
    for changes in ({'organization_id':uuid4()},{'current_and_valid':False},
                    {'measurement_revision_ids':(uuid4(),)},{'measurement_revision_ids':()},
                    {'permitted_claim':'exploratory_observation'}):
        wrong=ResolvedReference.model_validate(r.model_dump()|changes)
        with pytest.raises(PolicyStop):validate_draft_references(d,s,(wrong,))


def test_knowledge_cannot_be_presented_as_a_measurement_fact():
    s=scope();identifier=uuid4();r=ResolvedReference(kind='knowledge_chunk',id=identifier,
        organization_id=s.organization_id,measurement_revision_ids=(),permitted_claim='method_explanation',current_and_valid=True)
    assert validate_draft_references(draft(identifier,'knowledge_chunk','method_explanation'),s,(r,))
    with pytest.raises(PolicyStop,match='ROLE_NOT_ALLOWED'):
        validate_draft_references(draft(identifier,'knowledge_chunk','numeric_fact'),s,(r,))


def test_output_contract_cannot_claim_diagnosis_or_invent_legacy_evidence_ids():
    data=draft(uuid4()).model_dump()
    with pytest.raises(ValidationError):ExploratoryDraft.model_validate(data|{'status':'diagnosed','fault_type':'inner_race'})
    data['observations'][0]['citations'][0]['id']='EV-001'
    with pytest.raises(ValidationError):ExploratoryDraft.model_validate(data)
    with pytest.raises(ValidationError):ExploratoryDraft(status='insufficient_evidence',summary='不足',observations=(),
        missing_requirements=(),limitations=('限制',),next_steps=())


def test_exported_contract_matches_schemas_and_actual_scope(monkeypatch):
    import importlib,json
    from vibradiag.core.paths import PROJECT_ROOT
    monkeypatch.syspath_prepend(str(PROJECT_ROOT/'backend/scripts'))
    exporter=importlib.import_module('export_m3_agent_contract')
    saved=json.loads((PROJECT_ROOT/'docs/contracts/m3-agent-contract.json').read_text(encoding='utf8'))
    assert saved==exporter.contract()
    assert not saved['formal_diagnostic_release']
    assert 'Numerical-task model candidate orchestration' in saved['not_implemented_here']
    assert saved['knowledge']['durable_retrieval_binding'] and not saved['knowledge']['generated_answer']
    assert saved['model_method']['decision_drives_authorized_search']
