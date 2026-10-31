"""Benchmark-only one-call router over registered rule workflows, never a production executor."""
from dataclasses import asdict
from hashlib import sha256
import json
from uuid import uuid5
from pydantic import TypeAdapter
from vibradiag.agents.formal_contracts import AgentDecision,ChooseAction,FinishDecision
from vibradiag.agents.formal_policy import logical_call_key,reserve_call
from vibradiag.agents.rules_graph import RuleExecution
from vibradiag.infrastructure.llm.formal_gateway import Gateway,digest
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services import agent_task_service as tasks,agent_knowledge_service as knowledge
from vibradiag.domain.measurement_errors import MeasurementError


def execute(task,principal,configuration,receipt_path):
    identifier,token=task['id'],task['lease_token'];trace=None;record=None
    def save():
        temp=receipt_path.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf8');temp.replace(receipt_path)
    try:
        run=RuleExecution(task,principal);run.prepare()  # Same required input confirmation before paying for routing.
        row=run.scope();names=['full_rules']+(['basic_only'] if row['goal']=='bearing_observations' else [])
        plans={str(uuid5(identifier,'benchmark-workflow/'+name)):name for name in names}
        context=dict(step=0,goal=row['goal'],description=row['payload']['description'][:1000],completed=[],
            candidates=[dict(candidate_id=k,action='registered_workflow:'+v) for k,v in plans.items()],
            workflow_definitions=dict(full_rules='Run the fixed rules workflow for this explicit goal, using only valid deterministic tools and prerequisite checks.',
                basic_only='Run basic analysis only and mark the bearing workflow incomplete. No intermediate model feedback.'),
            limitation='Benchmark-only one-call workflow routing; server resolves all tool payloads and checks actual prerequisites.')
        schema=TypeAdapter(AgentDecision).json_schema();key=logical_call_key(identifier,row['input_fingerprint'],dict(evaluation_router=digest(context),configuration=digest(asdict(configuration))))
        record=dict(state='reserved_unknown',task_id=str(identifier),configuration=asdict(configuration),context_sha256=digest(context),schema_sha256=digest(schema),trace=None)
        with transaction() as c:
            row=tasks._running(c,identifier,token);tasks._authorize(c,principal,identifier,write=True,current_only=True)
            budget,_=reserve_call(tasks._active_budget(row),'model',key)
            tasks._change(c,row,'running',budget=budget.model_dump(mode='json'),charge=False)
            tasks._event(c,row,'benchmark.router_reserved',dict(context_sha256=digest(context),configuration_sha256=configuration.fingerprint))
        save()
        def guard():return knowledge._control(identifier,token,principal)
        raw,trace=Gateway(configuration).invoke('numeric_decision',context,schema,timeout=guard(),guard=guard)
        output=TypeAdapter(AgentDecision).validate_json(raw)
        if isinstance(output,ChooseAction):
            if str(output.candidate_id) not in plans:raise MeasurementError('BENCHMARK_PLAN_INVALID','Unregistered workflow',status=409)
            plan=plans[str(output.candidate_id)]
        elif isinstance(output,FinishDecision) and output.outcome=='insufficient_evidence':plan='refuse'
        else:raise MeasurementError('BENCHMARK_PLAN_INVALID','Unsupported workflow decision',status=409)
        guard();record.update(state='completed',selected_workflow=plan,raw_output_sha256=sha256(raw.encode()).hexdigest(),trace=trace);save()
        with transaction() as c:
            row=tasks._running(c,identifier,token);tasks._authorize(c,principal,identifier,write=True,current_only=True)
            tasks._event(c,row,'benchmark.router_completed',dict(selected_workflow=plan,trace=trace))
        if plan=='full_rules':tasks.execute_claimed(task)
        elif plan=='basic_only':
            result=run.dispatch('analyze_basic')
            tasks._finish(identifier,token,principal,'completed_degraded',(tasks._observation(result['analysis_id'],'basic_analysis','numeric_fact','基础指标由确定性工具计算。'),),('benchmark_workflow_incomplete','diagnostic_profile_not_qualified'))
        else:tasks._finish(identifier,token,principal,'refused',(),('single_router_insufficient_evidence',))
    except (tasks.YieldTask,tasks.Fenced):pass
    except Exception as error:
        if record is not None:
            record.update(state='failed_or_unknown',trace=trace,error_code=getattr(error,'code','BENCHMARK_ROUTER_FAILED'));save()
        try:
            with transaction() as c:
                row=tasks._running(c,identifier,token);tasks._change(c,row,'failed',error=getattr(error,'code','BENCHMARK_ROUTER_FAILED'))
        except tasks.Fenced:pass
    return record
