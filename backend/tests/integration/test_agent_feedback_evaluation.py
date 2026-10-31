import json
from vibradiag.evaluation.feedback_benchmark import run
from vibradiag.evaluation import single_router
from vibradiag.services import agent_model_service as models,agent_task_service as tasks


def test_feedback_rules_router_agent_and_scoped_faults_use_actual_results(database,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_AGENT_WORKER_ENABLED','false');monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    original=tasks._dispatch
    class Fake:
        calls=[]
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            self.calls.append(context);items=context['candidates'];completed=context.get('completed',[])
            frequency=next((r for r in completed if r['action']=='calculate_bearing_frequencies'),None)
            if frequency and frequency['missing_requirements'] or not items:output=dict(action='finish',outcome='analysis_only',rationale='Read the actual missing prerequisites')
            else:
                candidate=next((r for r in items if r['action']=='calculate_bearing_frequencies'),items[0])
                output=dict(action='choose_action',candidate_id=candidate['candidate_id'],rationale='Registered frequency-first policy')
            return json.dumps(output),dict(provider=self.cfg.provider,usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
    monkeypatch.setattr(models,'Gateway',Fake);monkeypatch.setattr(single_router,'Gateway',Fake)
    result=run(tmp_path)
    assert len(result['rows'])==18 and result['all_cases_met_expectations'],[(r['case_id'],r['strategy'],r['status'],r['error_code'],r['metrics']) for r in result['rows']]
    assert tasks._dispatch is original
    assert all(r['selected_workflow']=='feedback_rules' for r in result['rows'] if r['strategy']=='single_router')
    assert all(r['metrics']['fault_injected'] for r in result['rows'] if r['case_id'] in {'F05','F06'})
    assert result['summary']['rules']['model_reserved']==0 and result['summary']['single_router']['model_confirmed']==6
    assert result['summary']['agent']['model_confirmed']==22
    assert result['summary_functional']['agent']['runs']==4 and result['summary_lifecycle']['agent']['runs']==2
    assert result['summary_by_partition']['prospective_synthetic']['rules']['runs']==3
    assert result['admission']['synthetic_waveform_bytes_disjoint'] and not result['admission']['independent_holdout_admitted']
    assert not result['factory_trial_approved'] and not result['semantic_quality_qualified']
