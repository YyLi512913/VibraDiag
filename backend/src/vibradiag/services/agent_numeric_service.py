"""Server-owned numerical candidates. Model outputs never supply execution parameters or claims."""
from uuid import UUID,uuid5
from pydantic import TypeAdapter
from sqlalchemy import text
from vibradiag.agents.formal_contracts import AgentDecision,CandidateAction,TaskScope,ExploratoryDraft,ResolvedReference
from vibradiag.agents.formal_policy import logical_call_key,validate_draft_references
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services import agent_task_service as tasks,comparison_service as comparisons
from vibradiag.agents.rules_graph import RuleExecution


def calls(c,identifier):
    # Explicit retries append attempts; graph steps count stable logical operations.
    return c.execute(text('''SELECT * FROM (
        SELECT DISTINCT ON (operation_key) *,min(call_no) OVER (PARTITION BY operation_key) AS first_call_no
        FROM agent_calls WHERE task_id=:id ORDER BY operation_key,call_no DESC
        ) latest ORDER BY first_call_no'''),dict(id=identifier)).mappings().all()


def source(call,principal):
    if call['state'] not in {'completed','unavailable'} or not call['resource_id']:tasks.conflict('NUMERIC_PARENT_UNAVAILABLE')
    result=tasks._read_source(call,principal)
    if result['status']!='completed':tasks.conflict('NUMERIC_PARENT_UNAVAILABLE')
    if not result.get('input_confirmation_active',True) or not result.get('parent_confirmations_active',True):tasks.conflict('INPUT_CONFIRMATION_REVOKED')
    if result.get('is_current_bearing_revision') is False:tasks.conflict('STALE_TASK_BINDING')
    return result


def base(row,principal):
    confirmation=tasks._confirmation(row['measurement_revision_id'],principal)
    if not confirmation:tasks.conflict('INPUT_CONFIRMATION_REVOKED')
    return dict(measurement_revision_id=str(row['measurement_revision_id']),confirmation_id=str(confirmation))


def prepare_step(task,principal,step):
    run=RuleExecution(task,principal);row=run.scope();run.base()
    if row['goal']=='compare_measurements':
        with transaction() as c:declaration,_,_=comparisons.resolve_declaration(c,principal,row['payload']['comparison_declaration_id'],write=True,current_only=True)
        if not tasks._confirmation(declaration['before_revision_id'],principal):tasks._wait(run.id,run.token,'before_input_confirmation_required')
    if row['goal']=='bearing_observations' and step>0:
        run.select('profile_id');run.select('context_id')


def prepared(c,row,principal,step):
    if row['input_snapshot']['executor']!='model_numeric_v1' or row['goal']=='explain_method':tasks.conflict('NUMERIC_MODEL_SCOPE_REQUIRED')
    if type(step) is not int or not 0<=step<=5:tasks.conflict('NUMERIC_STEP_INVALID')
    history=calls(c,row['id'])
    if not step<=len(history)<=step+1:tasks.conflict('NUMERIC_STEP_HISTORY_MISMATCH')
    history=history[:step];results=[source(call,principal) for call in history]
    roots=tuple(UUID(r['revision_id']) for r in row['input_snapshot']['roots'])
    scope=TaskScope(task_id=row['id'],organization_id=row['organization_id'],measurement_revision_ids=roots)
    offered=[];payloads={};common=base(row,principal)
    def add(action,**payload):
        body=dict(common,**payload);key=logical_call_key(row['id'],row['input_fingerprint'],dict(action=action,payload=body))
        candidate=CandidateAction(id=uuid5(row['id'],'numeric/'+key),task_id=row['id'],organization_id=row['organization_id'],
            measurement_revision_ids=roots,kind=action,logical_call_key=key)
        offered.append(candidate);payloads[str(candidate.id)]=(action,body)
    if not history:
        if row['goal']=='compare_measurements':
            declaration,_,_=comparisons.resolve_declaration(c,principal,row['payload']['comparison_declaration_id'],write=True,current_only=True)
            before=tasks._confirmation(declaration['before_revision_id'],principal)
            if not before:tasks.conflict('INPUT_CONFIRMATION_REVOKED')
            add('compare_measurements',tool_name='compare_measurements',declaration_id=str(declaration['id']),before_confirmation_id=str(before))
        else:add('analyze_basic')
    elif row['goal']=='bearing_observations':
        # All IDs originate in validated user selections and the task's actual parent receipts.
        profile=row['selection'].get('profile_id');context=row['selection'].get('context_id')
        if not profile or not context:tasks.conflict('NUMERIC_SELECTION_REQUIRED')
        actions=[h['action_name'] for h in history]
        envelope=next((r for h,r in zip(history,results) if h['action_name']=='analyze_envelope' and not h['payload'].get('context_id')),None)
        frequency=next((r for h,r in zip(history,results) if h['action_name']=='calculate_bearing_frequencies'),None)
        bound=next((r for h,r in zip(history,results) if h['action_name']=='analyze_envelope' and h['payload'].get('context_id')),None)
        if not envelope:add('analyze_envelope',tool_name='analyze_envelope',profile_id=profile,mode='exploratory')
        if not frequency:add('calculate_bearing_frequencies',tool_name='calculate_bearing_frequencies',context_id=context)
        eligible=bool(envelope and frequency and envelope['result_status']=='ok' and frequency['result_status']=='ok' and frequency['result']['summary'].get('uncertainty_complete'))
        if eligible and not bound:add('analyze_envelope',tool_name='analyze_envelope',profile_id=profile,context_id=context,mode='exploratory')
        if eligible and bound and bound['result_status']=='ok' and 'match_frequency_evidence' not in actions:
            basic=next(r for h,r in zip(history,results) if h['action_name']=='analyze_basic')
            add('match_frequency_evidence',tool_name='match_frequency_evidence',context_id=context,envelope_run_id=bound['tool_execution_id'],
                frequency_run_id=frequency['tool_execution_id'],basic_analysis_id=basic['analysis_id'])
    context=dict(step=step,goal=row['goal'],description=row['payload']['description'][:1000],
        completed=[dict(call_id=str(h['id']),action=h['action_name'],status=r.get('result_status',r['status']),
                        missing_requirements=r.get('result',{}).get('missing_requirements',[])[:8]) for h,r in zip(history,results)],
        candidates=[dict(candidate_id=str(a.id),action=a.kind,variant='context_bound' if payloads[str(a.id)][1].get('context_id') else 'unbound') for a in offered],
        limitation='Exploratory numerical tools only. No qualified diagnostic profile or fault conclusion.')
    return row,principal,context,TypeAdapter(AgentDecision).json_schema(),dict(scope=scope,candidates=tuple(offered),payloads=payloads)


def dispatch_step(task,principal,step):
    from . import agent_model_service as models
    with transaction() as c:
        decision=models.completed_output(c,task['id'],task['lease_token'],principal,'numeric_decision',step=step)
        row=tasks._running(c,task['id'],task['lease_token']);_,current,_=tasks._authorize(c,principal,task['id'],write=True,current_only=True)
        _,_,_,_,data=prepared(c,row,current,step)
        if decision['action']!='choose_action':tasks.conflict('NUMERIC_DECISION_NOT_EXECUTABLE')
        action,payload=data['payloads'][decision['candidate_id']]
    # _dispatch repeats live authorization and uses its own bounded idempotency/retry ledger.
    return tasks._dispatch(task['id'],task['lease_token'],action,payload,principal)


def finish(task,principal):
    from . import agent_model_service as models
    with transaction() as c:
        row=tasks._running(c,task['id'],task['lease_token']);_,current,_=tasks._authorize(c,principal,task['id'],write=True,current_only=True)
        history=calls(c,task['id']);step=len(history)
        decision=models.completed_output(c,task['id'],task['lease_token'],principal,'numeric_decision',step=step)
        if decision['action']!='finish':tasks.conflict('NUMERIC_FINISH_REQUIRED')
        # Lock mutable provenance while resolving immutable artifacts and constructing the final draft.
        root_ids=sorted((UUID(root['revision_id']) for root in row['input_snapshot']['roots']),key=str)
        for identifier in root_ids:tasks.input_revision(c,current,identifier,write=True,lock=True)
        c.execute(text('SELECT id FROM input_confirmations WHERE revision_id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHARE'),dict(ids=root_ids)).all()
        c.execute(text('SELECT b.id FROM asset_components b WHERE b.id IN (SELECT d.component_id FROM diagnostic_contexts d WHERE d.measurement_revision_id=ANY(CAST(:ids AS uuid[]))) ORDER BY b.id FOR SHARE'),dict(ids=root_ids)).all()
        observations=[];references=[];missing=[]
        for call in history:
            result=source(call,current);basic=call['resource_kind']=='analysis';kind='basic_analysis' if basic else 'tool_run';claim='numeric_fact' if basic else 'exploratory_observation'
            identifier=str(call['resource_id']);status=result.get('result_status',result['status'])
            message='基础指标与幅度谱由确定性工具计算，来源可追溯。' if basic else call['action_name']+'运行状态：'+status+'；探索结果不构成故障或维修有效结论。'
            observations.append(tasks._observation(identifier,kind,claim,message))
            references.append(ResolvedReference(kind=kind,id=identifier,organization_id=row['organization_id'],measurement_revision_ids=tuple(UUID(r['revision_id']) for r in row['input_snapshot']['roots']),permitted_claim=claim,current_and_valid=True))
            missing.extend(result.get('result',{}).get('missing_requirements',[]))
            if call['action_name']=='compare_measurements':
                summary=result['result']['summary'];missing.extend(summary.get('reasons',[])+summary.get('related_reasons',[]))
                if summary['comparison_status']!='comparable':missing.append('comparison_not_comparable')
        if row['goal']=='bearing_observations':
            missing.append('diagnostic_profile_not_qualified')
            if not any(h['action_name']=='match_frequency_evidence' for h in history):missing.append('matching_not_completed')
        _,_,context,_,_=prepared(c,row,current,step)
        if context['candidates']:missing.append('model_finished_with_remaining_candidates')
        if decision['outcome']=='insufficient_evidence' or not observations:missing.append('model_insufficient_evidence')
        missing=list(dict.fromkeys(missing))[:20]
        draft=ExploratoryDraft(status='insufficient_evidence' if missing else 'analysis_only',summary='模型选择受控数值工具，草稿由服务器依据实际回执组装。',
            observations=tuple(observations),missing_requirements=tuple(missing),limitations=('模型不提供计算参数或数值结论；未取得故障、健康、严重度或维修有效结论资格。','模型编排质量尚未通过独立对照评测，全部结果须人工复核。'),
            next_steps=('核对原始数据、单位、参数来源及工具适用条件。',)).model_dump(mode='json')
        validate_draft_references(draft,TaskScope(task_id=row['id'],organization_id=row['organization_id'],measurement_revision_ids=tuple(UUID(r['revision_id']) for r in row['input_snapshot']['roots'])),references)
        tasks._change(c,row,'refused' if not observations else 'completed_degraded' if missing else 'completed',draft=draft)
