"""Authoritative final method draft from revalidated model receipts and current citations."""
from sqlalchemy import text
from vibradiag.agents.formal_contracts import ExploratoryDraft
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services import agent_task_service as tasks,agent_model_service as models,agent_knowledge_service as knowledge

QUOTE_PREFIX='方法资料原文摘录（相关性待复核）：'
CONTEXT_TEXT='已定位已发布方法资料；模型摘录未完成，需人工核对原文与问题的相关性。'


def finish_task(identifier,token,principal):
    with transaction() as c:
        row=tasks._running(c,identifier,token);tasks._authorize(c,principal,identifier,write=True,current_only=True)
        decision_phase,extract_phase=models.method_phases(row)
        observations=[];missing=[];status='refused';error=None
        calls=c.execute(text('SELECT * FROM agent_calls WHERE task_id=:id AND resource_id IS NOT NULL ORDER BY call_no'),dict(id=identifier)).mappings().all()
        try:decision=models.completed_output(c,identifier,token,principal,decision_phase)
        except MeasurementError as failure:
            if failure.status in {401,403}:raise
            decision=None;missing=['model_decision_unavailable'];error='MODEL_DECISION_UNAVAILABLE'
        if decision and decision['action']=='finish':missing=['model_insufficient_evidence'];error='MODEL_FINISHED_WITHOUT_RETRIEVAL'
        if decision and decision['action']=='choose_action':
            call=next((item for item in calls if item['resource_kind']=='knowledge_retrieval'),None)
            source=knowledge.resolve(call['resource_id'],principal,identifier,call['id'],c,lock=True) if call else None
            hits=source['hits'] if source and source['current_and_valid'] else []
            if not hits:missing=['published_knowledge_not_found_or_no_longer_available'];error='PUBLISHED_KNOWLEDGE_UNAVAILABLE'
            else:
                try:output=models.completed_output(c,identifier,token,principal,extract_phase)
                except MeasurementError as failure:
                    if failure.status in {401,403}:raise
                    output=None
                if output and output['outcome']=='cited_context':
                    observations=[tasks._observation(item['source_id'],'knowledge_chunk','method_explanation',QUOTE_PREFIX+item['quote']) for item in output['selections']]
                    missing=['knowledge_relevance_not_qualified'];status='completed_degraded'
                elif output:
                    missing=['model_sources_not_sufficient'];error='MODEL_NO_SUPPORTED_EXTRACT'
                else:
                    observations=[tasks._observation(hit['id'],'knowledge_chunk','method_explanation',CONTEXT_TEXT) for hit in hits]
                    missing=['model_generation_unavailable','knowledge_relevance_not_qualified'];status='completed_degraded';error='MODEL_GENERATION_UNAVAILABLE'
        if not missing:missing=['model_decision_unavailable'];error='MODEL_DECISION_UNAVAILABLE'
        draft=ExploratoryDraft(status='insufficient_evidence',summary='模型方法资料草稿，尚需人工复核。' if observations else '当前无法形成有来源的方法资料草稿。',
            observations=tuple(observations),missing_requirements=tuple(missing),
            limitations=('模型仅选择受限候选和逐字摘录，不能作为设备故障、健康或维修有效结论。','相关性及工程适用性尚未通过独立评测；资料内容不作为工具指令。'),
            next_steps=('核对原文来源、相关性与适用条件；本草稿未发布，不自动生成维修动作。',)).model_dump(mode='json')
        knowledge.validate_draft(c,row,principal,draft,calls)
        tasks._change(c,row,status,draft=draft,error=error)
