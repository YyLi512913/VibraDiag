import json
from uuid import uuid4,UUID
from sqlalchemy import text
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services import agent_model_service as models,agent_task_service as tasks
from vibradiag.evaluation import single_router
from vibradiag.evaluation.agent_benchmark import run
from vibradiag.infrastructure.llm.formal_gateway import configuration
from test_tool_runs import case
from test_agent_tasks import create,read,manual_executor


def test_three_adapters_use_real_services_and_keep_development_results_separate(database,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_AGENT_WORKER_ENABLED','false');monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    class Fake:
        calls=[]
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            self.calls.append(context);items=context['candidates']
            output=dict(action='choose_action',candidate_id=items[0]['candidate_id'],rationale='Registered candidate') if items else dict(action='finish',outcome='analysis_only',rationale='No remaining tool')
            return json.dumps(output),dict(provider=self.cfg.provider,requested_model=self.cfg.model,usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
    monkeypatch.setattr(models,'Gateway',Fake);monkeypatch.setattr(single_router,'Gateway',Fake)
    result=run(tmp_path)
    assert len(result['rows'])==15 and result['all_cases_met_expectations'],result['summary']
    assert len(Fake.calls)==18 and result['summary']['rules']['model_reserved']==0
    assert result['summary']['single_router']['model_confirmed']==4 and result['summary']['agent']['model_confirmed']==14
    assert all(r['metrics']['model_calls_reserved']==0 for r in result['rows'] if r['case_id']=='D04')
    assert not result['independent_holdout'] and not result['semantic_quality_qualified'] and not result['factory_trial_approved']
    assert all(not r['production_model_called_field'] for r in result['rows'] if r['strategy']=='single_router')


def test_single_router_invalid_plan_preserves_attempt_and_never_dispatches(case,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_NUMERIC_EXECUTOR','rules')
    class Bad:
        def __init__(self,cfg):pass
        def invoke(self,*args,**kwargs):return json.dumps(dict(action='choose_action',candidate_id=str(uuid4()),rationale='forged workflow')),dict(provider='deepseek')
    monkeypatch.setattr(single_router,'Gateway',Bad)
    task=create(case).json();claimed=tasks.claim_next(UUID(task['id']))
    receipt=single_router.execute(claimed,case['principal'],configuration('deepseek',purpose='numeric'),tmp_path/'router.json')
    actual=read(case,task)
    assert actual['status']=='failed' and not actual['calls'] and actual['draft'] is None
    assert receipt['state']=='failed_or_unknown' and receipt['trace'] and len(actual['budget']['reservations'])==1
