"""Export the current M3 rule runtime contract; no LLM decision or publication authority."""
from hashlib import sha256
import json
from pydantic import TypeAdapter
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.agents.formal_contracts import (CreateAgentTask,ResumeAgentTask,AgentDecision,
    ExploratoryDraft,ExecutionBudget,TaskStatus,MethodKnowledgeScope)
from vibradiag.agents.model_validation import ValidationDiagnostics
from vibradiag.agents.model_contracts import MethodDecision


def contract():
    return {'contract_version':'m3-t05c3b-runtime-contract-v1','implementation_stage':'Persistent rules and opt-in model-driven method/numerical graphs',
        'schemas':{'create_task':CreateAgentTask.model_json_schema(),'resume_task':ResumeAgentTask.model_json_schema(),
            'model_decision':TypeAdapter(AgentDecision).json_schema(),'unpublished_draft':ExploratoryDraft.model_json_schema(),
            'internal_budget':ExecutionBudget.model_json_schema(),'task_status':TypeAdapter(TaskStatus).json_schema(),
            'method_knowledge_scope':MethodKnowledgeScope.model_json_schema(),
            'model_validation_diagnostics':ValidationDiagnostics.model_json_schema(),
            'method_v2_decision':TypeAdapter(MethodDecision).json_schema()},
        'model_validation':{'version':'model-validation-diagnostics-v1','failed_schema_rounds_only':True,
            'legacy_rounds_not_backfilled':True,'raw_response_retained':False,'raw_validator_messages_retained':False,
            'max_issues':8,'max_location_depth':6,'unknown_field_names_redacted':True},
        'limits':{'model_logical_rounds':8,'logical_tool_calls':20,'automatic_retries_per_logical_call':1,'active_execution_ms':120000},
        'budget_semantics':{'reserve_before_dispatch':True,'unknown_outcome_does_not_refund':True,
            'resume_preserves_reservations_and_time':True,'manual_wait_is_not_active_execution':True,
            'format_repair_is_new_model_round':True,'network_retry_consumes_attempt_and_elapsed_time':True},
        'server_owned':['organization and actor scope','input and confirmation resolution','candidate IDs and payloads',
            'question IDs','logical call keys','evidence resolution/validity','budgets and model configuration'],
        'knowledge':{'graph_version':'rules-knowledge-v1','executor':'rules_knowledge_v1','action':'search_published_knowledge',
            'developer_content_allowed':False,'citation_kind':'knowledge_chunk','permitted_claim':'method_explanation',
            'semantic_relevance_qualified':False,'generated_answer':False,'durable_retrieval_binding':True},
        'model_method':{'graph_version':'model-method-v1','executor':'model_method_v1','enabled_by':'VIBRADIAG_METHOD_EXECUTOR=model',
            'default_mode':'rules','decision_drives_authorized_search':True,'extractive_draft_only':True,'configuration_pinned_at_acceptance':True},
        'model_method_v2':{'graph_version':'model-method-v2','executor':'model_method_v2','enabled_by':'VIBRADIAG_METHOD_EXECUTOR=model_v2',
            'default_mode':'rules','explicit_response_examples':True,'additional_fields_rejected':True,'format_repair':False,
            'decision_phase':'decision_method_v2','extract_phase':'extractive_method_v2','legacy_method_policy_preserved':True,
            'configuration_pinned_at_acceptance':True,'legitimate_refusal_preserved':True},
        'model_numeric':{'graph_version':'model-numeric-v1','executor':'model_numeric_v1','enabled_by':'VIBRADIAG_NUMERIC_EXECUTOR=model',
            'default_mode':'rules','decision_drives_authorized_tools':True,'max_graph_tools':5,'max_graph_decisions':6,
            'free_form_generated_draft':False,'server_assembled_cited_draft':True,'configuration_pinned_at_acceptance':True},
        'model_numeric_v2':{'graph_version':'model-numeric-v2','executor':'model_numeric_v2','enabled_by':'VIBRADIAG_NUMERIC_EXECUTOR=model_v2',
            'default_mode':'rules','requirement_scopes':True,'raw_requirements_retained':True,'diagnostic_release_allowed':False,
            'legacy_numeric_policy_preserved':True,'legitimate_refusal_preserved':True},
        'not_implemented_here':['open-ended diagnostic generation','retrieval relevance qualification',
            'knowledge frontend','formal model/draft semantic evaluation'],
        'formal_diagnostic_release':False,'factory_trial_approved':False}


def main():
    target=PROJECT_ROOT/'docs/contracts/m3-agent-contract.json'
    target.write_text(json.dumps(contract(),ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'contract':target.relative_to(PROJECT_ROOT).as_posix(),'sha256':sha256(target.read_bytes()).hexdigest(),
        'formal_rule_runtime_implemented':True,'model_method_decision_integrated':True,'numerical_model_decision_integrated':True}))


if __name__=='__main__':main()
