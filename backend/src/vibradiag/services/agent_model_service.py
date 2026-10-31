"""T04a internal method model rounds. Does not execute a decision or publish a generated draft."""
from dataclasses import asdict
from hashlib import sha256
import json
from uuid import UUID,uuid4,uuid5
from pydantic import TypeAdapter,ValidationError
from sqlalchemy import text
from vibradiag.agents.formal_contracts import AgentDecision,CandidateAction,TaskScope,ChooseAction,FinishDecision
from vibradiag.agents.model_contracts import ExtractiveMethodDraft
from vibradiag.agents.formal_policy import validate_decision,logical_call_key,reserve_call,PolicyStop
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.llm.formal_gateway import Gateway,ModelConfig,configuration,digest,PROMPT_VERSION
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.schemas.knowledge_indexes import KnowledgeSearch
from vibradiag.services import agent_task_service as tasks,agent_knowledge_service as knowledge


def _prepared(c,identifier,token,principal,phase,*,lock=False):
    row=tasks._running(c,identifier,token);_,current,_=tasks._authorize(c,principal,identifier,write=True,current_only=True)
    if row['goal']!='explain_method' or row['input_snapshot']['executor']!='rules_knowledge_v1':
        raise MeasurementError('MODEL_METHOD_SCOPE_REQUIRED','This model stage accepts explicit method tasks only',status=409)
    selected=knowledge.scope(row)
    if selected is None:raise MeasurementError('MODEL_SCOPE_REQUIRED','Resolve method scope before a model call',status=409)
    try:query=KnowledgeSearch(query=row['selection'].get('knowledge_query',row['payload']['description']),**selected.model_dump())
    except ValidationError:raise MeasurementError('MODEL_QUERY_REQUIRED','Resolve a valid short method query first',status=422) from None
    scope=TaskScope(task_id=identifier,organization_id=row['organization_id'],measurement_revision_ids=())
    if phase=='decision':
        key=logical_call_key(identifier,row['input_fingerprint'],dict(action='search_published_knowledge',payload=query.model_dump(mode='json')))
        candidate=CandidateAction(id=uuid5(identifier,'method-search/'+key),task_id=identifier,organization_id=row['organization_id'],
            measurement_revision_ids=(),kind='search_published_knowledge',logical_call_key=key)
        context=dict(question=query.query,candidates=[dict(candidate_id=str(candidate.id),action=candidate.kind)],allowed_finish='insufficient_evidence')
        return row,current,context,TypeAdapter(AgentDecision).json_schema(),dict(scope=scope,candidates=(candidate,))
    if phase!='extractive_draft':raise MeasurementError('MODEL_PHASE_INVALID','Unsupported formal model phase',status=422)
    call=c.execute(text("SELECT * FROM agent_calls WHERE task_id=:id AND action_name='search_published_knowledge' AND state IN ('completed','unavailable') ORDER BY call_no DESC LIMIT 1"),dict(id=identifier)).mappings().first()
    if not call or not call['resource_id']:raise MeasurementError('MODEL_SOURCE_REQUIRED','A bound completed retrieval is required',status=409)
    source=knowledge.resolve(call['resource_id'],current,identifier,call['id'],c,lock=lock)
    if not source['current_and_valid'] or not source['hits']:raise MeasurementError('MODEL_SOURCE_UNAVAILABLE','Current authorized source context is unavailable',status=409)
    texts={h['id']:h['text'][:400] for h in source['hits']}
    context=dict(question=query.query,sources=[dict(source_id=k,text=v,untrusted_content=True) for k,v in texts.items()])
    return row,current,context,ExtractiveMethodDraft.model_json_schema(),dict(texts=texts)


def _validate(phase,raw,prepared):
    try:
        if phase=='decision':
            output=TypeAdapter(AgentDecision).validate_json(raw)
            validate_decision(output,prepared['scope'],prepared['candidates'],())
            if not isinstance(output,(ChooseAction,FinishDecision)) or isinstance(output,FinishDecision) and output.outcome!='insufficient_evidence':
                raise PolicyStop('MODEL_DECISION_UNSUPPORTED')
        else:
            output=ExtractiveMethodDraft.model_validate_json(raw)
            for selected in output.selections:
                body=prepared['texts'].get(str(selected.source_id))
                if body is None or selected.quote not in body:raise PolicyStop('MODEL_QUOTE_UNGROUNDED')
        return output.model_dump(mode='json')
    except (ValidationError,PolicyStop) as error:
        raise MeasurementError(error.code if isinstance(error,PolicyStop) else 'MODEL_OUTPUT_SCHEMA_INVALID','Model output did not pass local policy checks',status=409) from None


def _fence(c,token):c.execute(text("SELECT set_config('vibradiag.model_lease',:token,true)"),dict(token=str(token)))


def run_round(identifier,token,principal,phase,*,provider=None):
    tasks.runtime.ensure()
    identifier=UUID(str(identifier));model_id=None
    with transaction() as c:
        row,current,context,schema,prepared=_prepared(c,identifier,token,principal,phase)
        if tasks._active_budget(row).active_ms>=120000:raise PolicyStop('ACTIVE_TIME_EXHAUSTED')
        binding=c.execute(text('SELECT * FROM agent_model_bindings WHERE task_id=:id'),dict(id=identifier)).mappings().first()
        try:cfg=ModelConfig(**binding['configuration']) if binding else configuration(provider)
        except (ValueError,TypeError):tasks.conflict('MODEL_POLICY_OR_CONFIGURATION_CHANGED')
        if binding and cfg.fingerprint!=binding['configuration_sha256']:tasks.conflict('MODEL_CONFIGURATION_INTEGRITY')
        if provider is not None and cfg!=configuration(provider):tasks.conflict('MODEL_CONFIGURATION_PINNED')
        key=logical_call_key(identifier,row['input_fingerprint'],dict(phase=phase,context_sha256=digest(context),configuration_sha256=cfg.fingerprint,schema_sha256=digest(schema),prompt_version=PROMPT_VERSION))
        existing=c.execute(text('SELECT * FROM agent_model_rounds WHERE task_id=:id AND logical_key=:key'),dict(id=identifier,key=key)).mappings().first()
        if existing:
            if existing['state']!='completed':tasks.conflict('MODEL_ROUND_CLOSED_OR_UNKNOWN')
            if digest(existing['validated_output'])!=existing['output_sha256']:tasks.conflict('MODEL_ROUND_INTEGRITY')
            output=_validate(phase,json.dumps(existing['validated_output']),prepared)
            return dict(id=str(existing['id']),state='completed',output=output,trace=existing['trace'],reused=True,executed_decision=False,published_draft=False)
        budget,_=reserve_call(tasks._active_budget(row),'model',key)
        row=tasks._change(c,row,'running',budget=budget.model_dump(mode='json'),charge=False)
        _fence(c,token)
        if not binding:c.execute(text('''INSERT INTO agent_model_bindings(task_id,organization_id,actor_id,configuration,configuration_sha256)
            VALUES (:task,:org,:actor,CAST(:config AS jsonb),:sha)'''),dict(task=identifier,org=current.organization_id,actor=current.user_id,config=json.dumps(asdict(cfg)),sha=cfg.fingerprint))
        number=c.execute(text('SELECT count(*)+1 FROM agent_model_rounds WHERE task_id=:id'),dict(id=identifier)).scalar_one()
        model_id=uuid4()
        c.execute(text('''INSERT INTO agent_model_rounds(id,task_id,organization_id,configuration_sha256,round_no,phase,logical_key,lease_token,context,context_sha256,output_schema,schema_sha256,prompt_version)
            VALUES (:id,:task,:org,:config,:number,:phase,:key,:lease,CAST(:context AS jsonb),:contextsha,CAST(:schema AS jsonb),:schemasha,:version)'''),
            dict(id=model_id,task=identifier,org=current.organization_id,config=cfg.fingerprint,number=number,phase=phase,key=key,lease=token,
                context=json.dumps(context,ensure_ascii=False),contextsha=digest(context),schema=json.dumps(schema),schemasha=digest(schema),version=PROMPT_VERSION))
        tasks._event(c,row,'model_reserved',dict(model_round_id=str(model_id),phase=phase,provider=cfg.provider,configuration_sha256=cfg.fingerprint,context_sha256=digest(context)))
    trace=None;raw=None
    try:
        def guard():
            tasks.runtime.ensure()
            return knowledge._control(identifier,token,principal)
        raw,trace=Gateway(cfg).invoke(phase,context,schema,timeout=guard(),guard=guard)
        output=_validate(phase,raw,prepared)
        with transaction() as c:
            row,current,live_context,live_schema,live_prepared=_prepared(c,identifier,token,principal,phase,lock=True)
            if digest(live_context)!=digest(context) or digest(live_schema)!=digest(schema):tasks.conflict('MODEL_CONTEXT_CHANGED')
            if tasks._active_budget(row).active_ms>=120000:raise PolicyStop('ACTIVE_TIME_EXHAUSTED')
            _validate(phase,json.dumps(output),live_prepared);_fence(c,token)
            c.execute(text("UPDATE agent_model_rounds SET state='completed',validated_output=CAST(:output AS jsonb),output_sha256=:sha,raw_output_sha256=:raw,trace=CAST(:trace AS jsonb),finished_at=now() WHERE id=:id"),
                dict(id=model_id,output=json.dumps(output,ensure_ascii=False),sha=digest(output),raw=sha256(raw.encode()).hexdigest(),trace=json.dumps(trace)))
            tasks._change(c,row,'running',budget=tasks._active_budget(row).model_dump(mode='json'),charge=False)
        return dict(id=str(model_id),state='completed',output=output,trace=trace,reused=False,executed_decision=False,published_draft=False)
    except Exception as error:
        try:
            with transaction() as c:
                row=tasks._running(c,identifier,token);_fence(c,token)
                c.execute(text("UPDATE agent_model_rounds SET state='failed',finished_at=now(),error_code=:error,raw_output_sha256=:raw,trace=CAST(:trace AS jsonb) WHERE id=:id AND state='running'"),
                    dict(id=model_id,error=getattr(error,'code','MODEL_ROUND_FAILED'),raw=sha256(raw.encode()).hexdigest() if raw else None,trace=json.dumps(trace) if trace else None))
                tasks._change(c,row,'running')
        except tasks.Fenced:pass
        except PolicyStop:
            with transaction() as c:
                row=tasks._running(c,identifier,token);tasks._change(c,row,'timed_out',error='ACTIVE_TIME_EXHAUSTED')
        raise
