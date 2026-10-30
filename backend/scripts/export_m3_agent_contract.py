"""Export M3-T01 schemas and policy limits; no runnable Agent or publication authority."""
from hashlib import sha256
import json
from pydantic import TypeAdapter
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.agents.formal_contracts import (CreateAgentTask,ResumeAgentTask,AgentDecision,
    ExploratoryDraft,ExecutionBudget,TaskStatus)


def contract():
    return {'contract_version':'m3-t01-design-contract-v1','implementation_stage':'DTOs and pure policy checks only',
        'schemas':{'create_task':CreateAgentTask.model_json_schema(),'resume_task':ResumeAgentTask.model_json_schema(),
            'model_decision':TypeAdapter(AgentDecision).json_schema(),'unpublished_draft':ExploratoryDraft.model_json_schema(),
            'internal_budget':ExecutionBudget.model_json_schema(),'task_status':TypeAdapter(TaskStatus).json_schema()},
        'limits':{'model_logical_rounds':8,'logical_tool_calls':20,'automatic_retries_per_logical_call':1,'active_execution_ms':120000},
        'budget_semantics':{'reserve_before_dispatch':True,'unknown_outcome_does_not_refund':True,
            'resume_preserves_reservations_and_time':True,'manual_wait_is_not_active_execution':True,
            'format_repair_is_new_model_round':True,'network_retry_consumes_attempt_and_elapsed_time':True},
        'server_owned':['organization and actor scope','input and confirmation resolution','candidate IDs and payloads',
            'question IDs','logical call keys','evidence resolution/validity','budgets and model configuration'],
        'not_implemented_here':['formal task API','LangGraph runtime with durable checkpoint',
            'worker queue/lease/CAS ledger','published knowledge search','formal model/draft semantic evaluation'],
        'formal_diagnostic_release':False,'factory_trial_approved':False}


def main():
    target=PROJECT_ROOT/'docs/contracts/m3-agent-contract.json'
    target.write_text(json.dumps(contract(),ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'contract':target.relative_to(PROJECT_ROOT).as_posix(),'sha256':sha256(target.read_bytes()).hexdigest(),
        'formal_agent_runtime_implemented':False}))


if __name__=='__main__':main()
