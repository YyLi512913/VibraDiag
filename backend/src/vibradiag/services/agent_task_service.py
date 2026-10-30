"""M3-T02a durable rule-backed task scaffold. Versioned durable rules graph; no LLM or RAG decisions."""
from datetime import datetime,timezone
from hashlib import sha256
import json
from uuid import UUID,uuid4

from vibradiag.agents.graph_state import EXECUTOR,GRAPH_VERSION,GraphWait,defer_wait
from pydantic import TypeAdapter
from sqlalchemy import text
from vibradiag.agents.formal_contracts import CreateAgentTask,ResumeAgentTask,ExecutionBudget,ExploratoryDraft
from vibradiag.agents.formal_policy import reserve_call,reserve_retry,charge_active_time,logical_call_key,PolicyStop
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.analysis_store import json_bytes
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.schemas.analyses import AnalysisRequest
from vibradiag.schemas.tool_runs import ToolRunRequest
from vibradiag.services import persistent_analysis_service as basic,tool_run_service as tools,comparison_service as comparisons
from vibradiag.services.analysis_runtime import runtime
from vibradiag.services.diagnostic_context_service import input_revision
from vibradiag.services.identity_service import Principal,audit
from vibradiag.services.measurement_service import fresh,not_found
from vibradiag.services.tool_options_service import options

TERMINAL={'completed','completed_degraded','refused','failed','cancelled','superseded','budget_exhausted','timed_out'}
MAX_PENDING=32


class Fenced(Exception):pass
class YieldTask(Exception):pass


def conflict(code='TASK_VERSION_CONFLICT'):
    raise MeasurementError(code,'Task changed or conditions no longer match; reload before continuing',status=409)


def fingerprint(value):return sha256(json_bytes(value)).hexdigest()


def _identity(task):
    return Principal(task['requested_by'],task['organization_id'],task['session_id'],frozenset(),'')


def _event(connection,row,kind,details=None):
    connection.execute(text('''INSERT INTO agent_events(id,task_id,organization_id,task_version,kind,details)
        VALUES (:id,:task,:org,:version,:kind,CAST(:details AS jsonb))'''),
        dict(id=uuid4(),task=row['id'],org=row['organization_id'],version=row['version'],kind=kind,details=json_bytes(details or {}).decode()))
    audit(connection,row['organization_id'],row['requested_by'],'agent.'+kind,'agent_task',row['id'])


def _active_budget(row):
    elapsed=max(0,int((datetime.now(timezone.utc)-row['last_tick_at']).total_seconds()*1000)) if row['status']=='running' else 0
    return charge_active_time(row['budget'],elapsed)


def _change(connection,row,status,*,question=None,draft=None,error=None,budget=None,selection=None,session_id=None,resume=False,defer=False,charge=True):
    if charge and row['status']=='running':
        elapsed=max(0,int((datetime.now(timezone.utc)-row['last_tick_at']).total_seconds()*1000))
        budget=charge_active_time(budget or row['budget'],elapsed).model_dump(mode='json')
    if (budget or row['budget'])['active_ms']>=120000 and row['status']=='running' and status not in {'cancelled','superseded'}:
        if status=='running':raise PolicyStop('ACTIVE_TIME_EXHAUSTED')
        status='timed_out';question=None;draft=None;error='ACTIVE_TIME_EXHAUSTED'
    running=status=='running';terminal=status in TERMINAL
    result=connection.execute(text('''UPDATE agent_tasks SET status=:status,version=version+1,updated_at=now(),
        question=CAST(:question AS jsonb),draft=CAST(:draft AS jsonb),error_code=:error,budget=CAST(:budget AS jsonb),
        selection=CAST(:selection AS jsonb),session_id=:session,resume_count=resume_count+:resume,
        lease_token=:lease,last_tick_at=CASE WHEN :running THEN now() ELSE NULL END,
        finished_at=CASE WHEN :terminal THEN now() ELSE NULL END,
        not_before=CASE WHEN :defer THEN now()+interval '2 seconds' ELSE now() END
        WHERE id=:id AND version=:version RETURNING *'''),
        dict(id=row['id'],version=row['version'],status=status,question=json.dumps(question) if question else None,
            draft=json.dumps(draft,allow_nan=False) if draft else None,error=error,
            budget=json_bytes(budget or row['budget']).decode(),selection=json_bytes(selection if selection is not None else row['selection']).decode(),
            session=session_id or row['session_id'],resume=1 if resume else 0,
            lease=row['lease_token'] if running else None,running=running,terminal=terminal,defer=defer)).mappings().first()
    if not result:raise Fenced()
    if terminal:
        connection.execute(text("UPDATE agent_calls SET state='interrupted',finished_at=now(),error_code='TASK_CLOSED' WHERE task_id=:id AND state='running'"),dict(id=row['id']))
    _event(connection,result,status,{'error_code':error} if error else {})
    return result


def _roots(connection,current,payload):
    if payload.goal=='explain_method':return []
    if payload.goal=='compare_measurements':
        row,_,resolved=comparisons.resolve_declaration(connection,current,payload.comparison_declaration_id,write=True,current_only=True,lock=True)
        if row['after_revision_id']!=payload.measurement_revision_id:conflict('COMPARISON_INPUT_MISMATCH')
        pairs=[(ctx['revision'],ctx['record']) for ctx in resolved.values()]
    else:pairs=[input_revision(connection,current,payload.measurement_revision_id,write=True,lock=True)]
    roots=[]
    for revision,record in pairs:
        if record['current_revision_id']!=revision['id']:conflict('STALE_TASK_INPUT')
        item={'revision_id':str(revision['id']),'measurement_id':str(record['id']),'sha256':record['sha256']}
        if item not in roots:roots.append(item)
    return roots


def _authorize(connection,principal,identifier,*,write=False,lock=False,current_only=False):
    current=fresh(connection,principal,write=write)
    row=connection.execute(text('''SELECT * FROM agent_tasks WHERE id=:id AND organization_id=:org AND requested_by=:actor'''
        +(' FOR UPDATE' if lock else '')),dict(id=identifier,org=current.organization_id,actor=current.user_id)).mappings().first()
    if not row:not_found()
    if fingerprint(row['input_snapshot'])!=row['input_fingerprint'] or fingerprint(row['payload'])!=row['payload_hash']:
        conflict('TASK_SNAPSHOT_INTEGRITY')
    active=True
    for root in row['input_snapshot']['roots']:
        rev,record=input_revision(connection,current,UUID(root['revision_id']),write=write)
        if str(record['id'])!=root['measurement_id'] or record['sha256']!=root['sha256']:conflict('TASK_SOURCE_INTEGRITY')
        active=active and record['current_revision_id']==rev['id']
    if current_only and not active:conflict('STALE_TASK_INPUT')
    return row,current,bool(active)


def _command(connection,current,key,operation,payload,task_id=None):
    if not key or len(key)>128 or any(ord(c)<33 or ord(c)>126 for c in key):
        raise MeasurementError('IDEMPOTENCY_KEY_INVALID','A visible ASCII idempotency key of at most 128 characters is required',status=422)
    digest=sha256(key.encode()).hexdigest()
    lock=int.from_bytes(sha256(f'agent/{current.organization_id}/{current.user_id}/{digest}'.encode()).digest()[:8],'big',signed=True)
    connection.execute(text('SELECT pg_advisory_xact_lock(:key)'),dict(key=lock))
    request=fingerprint({'operation':operation,'task_id':str(task_id) if task_id else None,'payload':payload})
    existing=connection.execute(text('SELECT * FROM agent_commands WHERE organization_id=:org AND actor_id=:actor AND key_hash=:key'),
        dict(org=current.organization_id,actor=current.user_id,key=digest)).mappings().first()
    if existing and existing['request_hash']!=request:conflict('IDEMPOTENCY_CONFLICT')
    return existing,digest,request


def _save_command(connection,current,task_id,operation,digest,request):
    connection.execute(text('''INSERT INTO agent_commands(id,organization_id,actor_id,task_id,operation,key_hash,request_hash)
        VALUES (:id,:org,:actor,:task,:operation,:key,:request)'''),
        dict(id=uuid4(),org=current.organization_id,actor=current.user_id,task=task_id,operation=operation,key=digest,request=request))


def create_task(payload,key,principal):
    payload=CreateAgentTask.model_validate(payload);data=payload.model_dump(mode='json');runtime.ensure()
    with transaction() as connection:
        current=fresh(connection,principal,write=True)
        existing,digest,request=_command(connection,current,key,'create',data)
        if existing:
            _authorize(connection,current,existing['task_id']);identifier=existing['task_id'];status=200
        else:
            # Serialize per-actor admission independently of the request key.
            connection.execute(text('SELECT id FROM users WHERE id=:actor FOR UPDATE'),dict(actor=current.user_id))
            count=connection.execute(text('''SELECT count(*) FROM agent_tasks WHERE organization_id=:org AND requested_by=:actor
                AND status IN ('queued','running','waiting_information','interrupted')'''),dict(org=current.organization_id,actor=current.user_id)).scalar_one()
            if count>=MAX_PENDING:raise MeasurementError('AGENT_QUEUE_FULL','Too many unfinished tasks',status=429)
            roots=_roots(connection,current,payload);snapshot={'roots':roots,'executor':EXECUTOR}
            identifier=uuid4()
            before=next((root['revision_id'] for root in roots if root['revision_id']!=str(payload.measurement_revision_id)),None)
            if payload.goal=='compare_measurements' and before is None:before=str(payload.measurement_revision_id)
            connection.execute(text('''INSERT INTO agent_tasks(id,organization_id,requested_by,session_id,measurement_revision_id,
                before_revision_id,goal,payload,payload_hash,input_snapshot,input_fingerprint)
                VALUES (:id,:org,:actor,:session,:revision,:before,:goal,CAST(:payload AS jsonb),:hash,CAST(:snapshot AS jsonb),:fingerprint)'''),
                dict(id=identifier,org=current.organization_id,actor=current.user_id,session=current.session_id,revision=payload.measurement_revision_id,
                    before=before,goal=payload.goal,payload=json_bytes(data).decode(),hash=fingerprint(data),snapshot=json_bytes(snapshot).decode(),fingerprint=fingerprint(snapshot)))
            row=connection.execute(text('SELECT * FROM agent_tasks WHERE id=:id'),dict(id=identifier)).mappings().one()
            connection.execute(text('''INSERT INTO agent_graph_bindings(task_id,organization_id,requested_by,thread_id,graph_version,input_fingerprint)
                VALUES (:task,:org,:actor,:thread,:version,:fingerprint)'''),dict(task=identifier,org=current.organization_id,
                actor=current.user_id,thread=str(uuid4()),version=GRAPH_VERSION,fingerprint=row['input_fingerprint']))
            _event(connection,row,'accepted');_save_command(connection,current,identifier,'create',digest,request);status=202
    return read_task(identifier,principal),status


def read_task(identifier,principal):
    with transaction() as connection:
        row,current,active=_authorize(connection,principal,identifier)
        calls=connection.execute(text('SELECT * FROM agent_calls WHERE task_id=:id ORDER BY call_no'),dict(id=identifier)).mappings().all()
        events=connection.execute(text('SELECT task_version,kind,details,created_at FROM agent_events WHERE task_id=:id ORDER BY created_at,id LIMIT 200'),dict(id=identifier)).mappings().all()
    with transaction() as connection:
        binding=connection.execute(text('SELECT * FROM agent_graph_bindings WHERE task_id=:id'),dict(id=identifier)).mappings().first()
        checkpoint_count=connection.execute(text('SELECT count(*) FROM public_agent_checkpoints.checkpoints WHERE thread_id=:thread'),dict(thread=binding['thread_id'])).scalar_one() if binding else 0
    confirmations=True;contexts=True
    for call in calls:
        if call['resource_id']:
            result=_read_source(call,current)
            confirmations=confirmations and result.get('input_confirmation_active',True) and result.get('parent_confirmations_active',True)
            contexts=contexts and result.get('is_current_bearing_revision') is not False
    with transaction() as connection:
        _,_,latest_active=_authorize(connection,principal,identifier)
    valid=active and latest_active and confirmations and contexts
    return {'id':str(row['id']),'goal':row['goal'],'description':row['payload']['description'],'status':row['status'],
        'version':row['version'],'question':row['question'],'selection':row['selection'],'budget':row['budget'],
        'draft':row['draft'] if valid else None,'error_code':row['error_code'],'input_snapshot':row['input_snapshot'],
        'draft_validity':{'inputs_current':bool(active and latest_active),'confirmations_active':bool(confirmations),
            'contexts_current':bool(contexts),'usable':bool(valid and row['draft'])},
        'created_at':row['created_at'],'updated_at':row['updated_at'],'finished_at':row['finished_at'],
        'calls':[{'id':str(c['id']),'call_no':c['call_no'],'action':c['action_name'],'state':c['state'],
            'resource_kind':c['resource_kind'],'resource_id':str(c['resource_id']) if c['resource_id'] else None,
            'logical_call_key':c['logical_key'],'error_code':c['error_code']} for c in calls],
        'events':[dict(e) for e in events],'executor':row['input_snapshot']['executor'],'model_called':False,'formal_graph_executed':bool(checkpoint_count),
        'graph':{'version':binding['graph_version'],'checkpoint_count':checkpoint_count} if binding else None}


def list_tasks(principal,page,page_size):
    with transaction() as connection:
        current=fresh(connection,principal)
        identifiers=connection.execute(text('''SELECT id FROM agent_tasks WHERE organization_id=:org AND requested_by=:actor
            ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset'''),
            dict(org=current.organization_id,actor=current.user_id,limit=page_size,offset=(page-1)*page_size)).scalars().all()
    items=[]
    for identifier in identifiers:
        try:
            with transaction() as connection:row,_,active=_authorize(connection,principal,identifier)
            items.append({'id':str(identifier),'goal':row['goal'],'status':row['status'],'version':row['version'],'inputs_current':active})
        except MeasurementError as error:
            if error.status!=404:raise
    return {'items':items,'page':page,'page_size':page_size}


def resume_task(identifier,payload,key,principal):
    payload=ResumeAgentTask.model_validate(payload);data=payload.model_dump(mode='json')
    with transaction() as connection:
        current=fresh(connection,principal,write=True)
        existing,digest,request=_command(connection,current,key,'resume',data,identifier)
        row,_,_=_authorize(connection,current,identifier,write=True,lock=True,current_only=True)
        if not existing:
            if row['version']!=payload.expected_version or row['status'] not in {'waiting_information','interrupted'}:conflict()
            question=row['question']
            if question['id']!=str(payload.question_id):conflict('QUESTION_MISMATCH')
            selection=dict(row['selection'])
            if question.get('choices'):
                if payload.answer not in {choice['id'] for choice in question['choices']}:conflict('CHOICE_NOT_OFFERED')
                field=question['selection_field'];selection[field]=payload.answer
            status='budget_exhausted' if row['resume_count']>=20 or row['budget']['active_ms']>=120000 else 'queued'
            _change(connection,row,status,selection=selection,session_id=current.session_id if status=='queued' else row['session_id'],resume=status=='queued',
                error='RESUME_BUDGET_EXHAUSTED' if status=='budget_exhausted' else None)
            _save_command(connection,current,identifier,'resume',digest,request)
    return read_task(identifier,principal),200 if existing else 202


def cancel_task(identifier,expected_version,key,principal):
    data={'expected_version':expected_version}
    with transaction() as connection:
        current=fresh(connection,principal,write=True)
        existing,digest,request=_command(connection,current,key,'cancel',data,identifier)
        row,_,_=_authorize(connection,current,identifier,write=True,lock=True)
        if not existing:
            if row['version']!=expected_version or row['status'] in TERMINAL:conflict()
            _change(connection,row,'cancelled',error='USER_CANCELLED')
            _save_command(connection,current,identifier,'cancel',digest,request)
    from vibradiag.infrastructure.compute.runner import stop_task
    stop_task(identifier)
    return read_task(identifier,principal),200


def _running(connection,identifier,token):
    row=connection.execute(text('SELECT * FROM agent_tasks WHERE id=:id FOR UPDATE'),dict(id=identifier)).mappings().one()
    if row['status']!='running' or row['lease_token']!=token:raise Fenced()
    return row


def _wait(identifier,token,code,*,choices=None,selection_field=None,status='waiting_information'):
    if defer_wait.get():raise GraphWait(code,choices,selection_field,status)
    question={'id':str(uuid4()),'code':code,'choices':choices or [],'selection_field':selection_field,
        'note':'Answer text does not change measurement parameters, confirm inputs, or establish calibration.'}
    with transaction() as connection:
        row=_running(connection,identifier,token);_change(connection,row,status,question=question,error=code)
    raise YieldTask()


def _read_source(call,principal):
    if call['resource_kind']=='analysis':return basic.read_analysis(call['resource_id'],principal)
    return tools.read_run(call['resource_id'],principal)


def _confirmation(revision,principal):
    with transaction() as connection:
        current=fresh(connection,principal,write=True)
        input_revision(connection,current,revision,write=True)
        return connection.execute(text('''SELECT id FROM input_confirmations WHERE organization_id=:org AND actor_id=:actor
            AND revision_id=:revision AND revoked_at IS NULL'''),dict(org=current.organization_id,actor=current.user_id,revision=revision)).scalar_one_or_none()


def _selection(identifier,token,field,items):
    with transaction() as connection:row=_running(connection,identifier,token)
    choices=[{'id':str(item['id'])} for item in items]
    selected=row['selection'].get(field)
    if selected:
        if selected not in {choice['id'] for choice in choices}:conflict('STALE_TASK_BINDING')
        return selected
    if len(choices)!=1:_wait(identifier,token,'profile_required' if field=='profile_id' else 'target_context_required',choices=choices,selection_field=field)
    selected=choices[0]['id']
    with transaction() as connection:
        row=_running(connection,identifier,token);_change(connection,row,'running',selection=dict(row['selection'],**{field:selected}))
    return selected


def _dispatch(identifier,token,action,payload,principal):
    resource_kind='analysis' if action=='analyze_basic' else 'tool'
    with transaction() as connection:
        row=_running(connection,identifier,token);_authorize(connection,principal,identifier,write=True,current_only=True)
        operation=logical_call_key(identifier,row['input_fingerprint'],{'action':action,'payload':payload})
        previous=connection.execute(text('SELECT * FROM agent_calls WHERE task_id=:id AND operation_key=:operation ORDER BY call_no DESC LIMIT 1'),
            dict(id=identifier,operation=operation)).mappings().first()
        reserve_call(_active_budget(row),'tool',previous['logical_key'] if previous else operation)
        if previous and previous['state'] in {'completed','unavailable'}:
            reused=dict(previous);call=None
        else:
            reused=None;execution_payload=dict(payload)
            retry=False
            if previous and previous['resource_id'] and previous['attempt_epoch']<row['resume_count']:
                # Retry only an explicitly resumed failed/interrupted resource, never a still-running execution.
                existing=_read_source(previous,principal)
                if existing['status'] in {'failed','interrupted'}:
                    execution_payload['retry_of']=str(previous['resource_id']);retry=True
            if previous and not retry:
                call=dict(previous);execution_payload=call['payload']
                if call['state']!='running':
                    connection.execute(text("UPDATE agent_calls SET state='running',finished_at=NULL,error_code=NULL WHERE id=:id"),dict(id=call['id']))
            else:
                key=logical_call_key(identifier,row['input_fingerprint'],{'action':action,'payload':execution_payload})
                budget,_=reserve_call(_active_budget(row),'tool',key)
                row=_change(connection,row,'running',budget=budget.model_dump(mode='json'),charge=False)
                call={'id':uuid4(),'logical_key':key,'payload':execution_payload,'resource_kind':resource_kind}
                count=connection.execute(text('SELECT count(*) FROM agent_calls WHERE task_id=:id'),dict(id=identifier)).scalar_one()
                connection.execute(text('''INSERT INTO agent_calls(id,task_id,organization_id,call_no,operation_key,logical_key,
                    action_name,payload,attempt_epoch,state,resource_kind) VALUES (:id,:task,:org,:number,:operation,:key,:action,
                    CAST(:payload AS jsonb),:epoch,'running',:kind)'''),
                    dict(id=call['id'],task=identifier,org=row['organization_id'],number=count+1,operation=operation,key=key,
                        action=action,payload=json_bytes(execution_payload).decode(),epoch=row['resume_count'],kind=resource_kind))
                _event(connection,row,'call_reserved',{'call_id':str(call['id']),'action':action,'logical_key':key})
    if reused:
        result=_read_source(reused,principal)
        if result.get('is_current_bearing_revision') is False:conflict('STALE_TASK_BINDING')
        if not result.get('input_confirmation_active',True):conflict('INPUT_CONFIRMATION_REVOKED')
        return result
    try:
        from vibradiag.infrastructure.compute.runner import bound_task
        with bound_task(identifier,token,principal):
            if resource_kind=='analysis':result,_=basic.create_analysis(AnalysisRequest.model_validate(execution_payload),call['logical_key'],principal,uuid4())
            else:result,_=tools.create_run(TypeAdapter(ToolRunRequest).validate_python(execution_payload),call['logical_key'],principal,uuid4())
        rid=UUID(result['analysis_id'] if resource_kind=='analysis' else result['tool_execution_id'])
        state=result['status']
        if state=='completed':state={'unavailable':'unavailable','failed':'failed'}.get(result.get('result_status'),'completed')
        elif state=='running':state='interrupted'
        with transaction() as connection:
            row=_running(connection,identifier,token)
            _authorize(connection,principal,identifier,write=True,current_only=True)
            budget=_active_budget(row)
            connection.execute(text('''UPDATE agent_calls SET state=:state,resource_id=:resource,finished_at=now(),
                result_summary=CAST(:summary AS jsonb),error_code=:error WHERE id=:id'''),
                dict(id=call['id'],state=state,resource=rid,error=result.get('error_code'),
                    summary=json_bytes({'resource_id':str(rid),'status':result['status'],'result_status':result.get('result_status')}).decode()))
            _change(connection,row,'running',budget=budget.model_dump(mode='json'),charge=False)
        if state in {'failed','interrupted'}:_wait(identifier,token,'execution_retry_required',status='interrupted')
        return result
    except MeasurementError as error:
        deferred=False
        with transaction() as connection:
            row=_running(connection,identifier,token)
            connection.execute(text("UPDATE agent_calls SET state='interrupted',finished_at=now(),error_code=:error WHERE id=:id"),dict(id=call['id'],error=error.code))
            budget=_active_budget(row)
            if error.status==429:
                try:
                    budget=reserve_retry(budget,call['logical_key'])
                    _change(connection,row,'queued',budget=budget.model_dump(mode='json'),error='CAPACITY_WAIT',defer=True,charge=False)
                    deferred=True
                except PolicyStop:pass
            if not deferred:_change(connection,row,'running',budget=budget.model_dump(mode='json'),charge=False)
        if deferred:raise YieldTask()
        raise


def _finish(identifier,token,principal,status,observations,missing=(),error=None):
    draft=ExploratoryDraft(status='insufficient_evidence' if missing else 'analysis_only',
        summary='规则托管执行完成；尚未执行模型Agent诊断。' if not missing else '已保留可用分析；必要条件仍不足。',
        observations=tuple(observations),missing_requirements=tuple(dict.fromkeys(missing)),
        limitations=('持久规则执行基础，未执行DeepSeek模型自主决策。','探索数值不能直接用于故障定类、健康或维修有效结论。'),
        next_steps=('结合有来源的参数及人工复核；后续接入正式Agent与知识服务。',)).model_dump(mode='json')
    # Re-read all referenced outcomes after computation; no stale/corrupt result can be published.
    with transaction() as connection:
        row=_running(connection,identifier,token)
        calls=connection.execute(text('SELECT * FROM agent_calls WHERE task_id=:id AND resource_id IS NOT NULL'),dict(id=identifier)).mappings().all()
    for call in calls:
        source=_read_source(call,principal)
        if source.get('is_current_bearing_revision') is False:conflict('STALE_TASK_BINDING')
        if not source.get('input_confirmation_active',True) or not source.get('parent_confirmations_active',True):conflict('INPUT_CONFIRMATION_REVOKED')
    with transaction() as connection:
        row=_running(connection,identifier,token);_authorize(connection,principal,identifier,write=True,current_only=True)
        if row['budget']['active_ms']>=120000:status='timed_out';draft=None;error='ACTIVE_TIME_EXHAUSTED'
        _change(connection,row,status,draft=draft,error=error)


def _observation(identifier,kind,claim,text):
    return {'claim_kind':claim,'text':text,'citations':({'kind':kind,'id':identifier},)}


def execute_claimed(task):
    identifier=task['id'];token=task['lease_token'];principal=_identity(task)
    try:
        runtime.ensure()
        with transaction() as connection:
            _running(connection,identifier,token);row,current,_=_authorize(connection,principal,identifier,write=True,current_only=True)
        principal=current;payload=CreateAgentTask.model_validate(row['payload'])
        if row['input_snapshot']['executor']==EXECUTOR:
            from vibradiag.agents.rules_graph import execute
            execute(task,principal);return
        if payload.goal=='explain_method':
            _finish(identifier,token,principal,'refused',(),('published_knowledge_service_not_integrated',));return
        confirmation=_confirmation(payload.measurement_revision_id,principal)
        if not confirmation:_wait(identifier,token,'input_confirmation_required')
        base={'measurement_revision_id':str(payload.measurement_revision_id),'confirmation_id':str(confirmation)}
        if payload.goal=='compare_measurements':
            with transaction() as connection:
                declaration,_,_=comparisons.resolve_declaration(connection,principal,payload.comparison_declaration_id,write=True,current_only=True)
            before=_confirmation(declaration['before_revision_id'],principal)
            if not before:_wait(identifier,token,'before_input_confirmation_required')
            result=_dispatch(identifier,token,'compare_measurements',dict(base,tool_name='compare_measurements',
                declaration_id=str(payload.comparison_declaration_id),before_confirmation_id=str(before)),principal)
            summary=result['result']['summary'];missing=summary.get('reasons',[])+summary.get('related_reasons',[])
            if summary['comparison_status']!='comparable' and not missing:missing=['comparison_not_comparable']
            _finish(identifier,token,principal,'completed_degraded' if missing else 'completed',
                (_observation(result['tool_execution_id'],'tool_run','exploratory_observation','比较状态：'+summary['comparison_status']+'；差值不代表维修有效。'),),missing);return
        result=_dispatch(identifier,token,'analyze_basic',base,principal)
        observations=[_observation(result['analysis_id'],'basic_analysis','numeric_fact','基础指标与幅度谱已由确定性工具计算，来源可追溯。')]
        if payload.goal=='inspect_signal':_finish(identifier,token,principal,'completed',observations);return
        with transaction() as connection:
            row=_running(connection,identifier,token)
            revision,record=input_revision(connection,principal,payload.measurement_revision_id)
        available=options(record['id'],principal)
        profile=_selection(identifier,token,'profile_id',available['profiles'])
        envelope=_dispatch(identifier,token,'analyze_envelope',dict(base,tool_name='analyze_envelope',profile_id=profile,mode='exploratory'),principal)
        observations.append(_observation(envelope['tool_execution_id'],'tool_run','exploratory_observation','包络运行状态：'+str(envelope['result_status'])+'；不构成轴承故障结论。'))
        context=_selection(identifier,token,'context_id',[ctx for ctx in available['contexts'] if ctx['is_current_bearing_revision']])
        frequency=_dispatch(identifier,token,'calculate_bearing_frequencies',dict(base,tool_name='calculate_bearing_frequencies',context_id=context),principal)
        observations.append(_observation(frequency['tool_execution_id'],'tool_run','exploratory_observation','理想频率运行状态：'+str(frequency['result_status'])+'；仍受参数与不确定量条件限制。'))
        missing=list(frequency['result']['missing_requirements'])
        if envelope['result_status']=='ok' and frequency['result_status']=='ok' and frequency['result']['summary'].get('uncertainty_complete'):
            # Matching parents must share the exact context, so create an explicitly bound envelope parent.
            bound=_dispatch(identifier,token,'analyze_envelope',dict(base,tool_name='analyze_envelope',profile_id=profile,context_id=context,mode='exploratory'),principal)
            matching=_dispatch(identifier,token,'match_frequency_evidence',dict(base,tool_name='match_frequency_evidence',context_id=context,
                envelope_run_id=bound['tool_execution_id'],frequency_run_id=frequency['tool_execution_id'],basic_analysis_id=result['analysis_id']),principal)
            observations.append(_observation(matching['tool_execution_id'],'tool_run','exploratory_observation','频率匹配保留显著性、重合与替代解释；不是故障分类。'))
            missing.extend(matching['result']['missing_requirements'])
        else:missing.append('matching_parents_or_uncertainty_incomplete')
        missing.append('diagnostic_profile_not_qualified')
        _finish(identifier,token,principal,'completed_degraded',observations,missing)
    except GraphWait as wait:
        try:_wait(identifier,token,wait.code,choices=wait.choices,selection_field=wait.selection_field,status=wait.status)
        except (YieldTask,Fenced):pass
    except (YieldTask,Fenced):return
    except PolicyStop as error:
        try:
            with transaction() as connection:
                row=_running(connection,identifier,token);_change(connection,row,'timed_out' if error.code=='ACTIVE_TIME_EXHAUSTED' else 'budget_exhausted',error=error.code)
        except Fenced:pass
    except MeasurementError as error:
        try:
            if error.status==401:_wait(identifier,token,'session_reauthentication_required')
            if error.status==429:_wait(identifier,token,'execution_capacity_unavailable',status='interrupted')
            with transaction() as connection:
                row=_running(connection,identifier,token)
                _change(connection,row,'superseded' if error.code in {'STALE_TASK_INPUT','STALE_TASK_BINDING','REVISION_CONFLICT'} else 'failed',error=error.code)
        except (YieldTask,Fenced):pass
    except Exception:
        try:
            with transaction() as connection:
                row=_running(connection,identifier,token);_change(connection,row,'failed',error='TASK_EXECUTION_FAILED')
        except Fenced:pass


def claim_next(identifier=None):
    runtime.ensure()
    with transaction() as connection:
        row=connection.execute(text("SELECT * FROM agent_tasks WHERE status='queued' AND not_before<=now() AND (CAST(:id AS uuid) IS NULL OR id=:id) ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1"),dict(id=identifier)).mappings().first()
        if not row:return None
        if row['budget']['active_ms']>=120000:
            _change(connection,row,'budget_exhausted',error='ACTIVE_TIME_EXHAUSTED');return None
        token=uuid4()
        claimed=connection.execute(text("UPDATE agent_tasks SET status='running',lease_token=:token,last_tick_at=now(),version=version+1,updated_at=now() WHERE id=:id RETURNING *"),dict(id=row['id'],token=token)).mappings().one()
        _event(connection,claimed,'claimed');return dict(claimed)


def run_next():
    claimed=claim_next()
    if claimed:execute_claimed(claimed)
    return bool(claimed)


def recover_interrupted():
    with transaction() as connection:
        rows=connection.execute(text("SELECT * FROM agent_tasks WHERE status='running' FOR UPDATE")).mappings().all()
        for row in rows:
            elapsed=max(0,int((datetime.now(timezone.utc)-row['last_tick_at']).total_seconds()*1000))
            budget=charge_active_time(row['budget'],elapsed)
            connection.execute(text("UPDATE agent_calls SET state='interrupted',finished_at=now(),error_code='PROCESS_INTERRUPTED' WHERE task_id=:id AND state='running'"),dict(id=row['id']))
            question={'id':str(uuid4()),'code':'execution_retry_required','choices':[],'selection_field':None}
            _change(connection,row,'budget_exhausted' if budget.active_ms>=120000 else 'interrupted',
                question=None if budget.active_ms>=120000 else question,budget=budget.model_dump(mode='json'),error='PROCESS_INTERRUPTED',charge=False)
    return len(rows)
