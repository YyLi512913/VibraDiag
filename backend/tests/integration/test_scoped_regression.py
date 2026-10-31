import json
from vibradiag.evaluation.scoped_regression import run
from vibradiag.services import agent_model_service as models


def test_frozen_agent_only_regression_includes_repeats_and_keeps_admission_blocked(database,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_AGENT_WORKER_ENABLED','false');monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    class Fake:
        calls=[]
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            assert phase=='numeric_decision_v2';self.calls.append(context)
            frequency=next((r for r in context['completed'] if r['action']=='calculate_bearing_frequencies'),None)
            stop=frequency and frequency['frequency_uncertainty_complete'] is False
            if stop or not context['candidates']:out=dict(action='finish',outcome='insufficient_evidence' if stop else 'analysis_only',rationale='Scoped actual condition')
            else:
                candidate=next((c for c in context['candidates'] if c['action']=='calculate_bearing_frequencies'),context['candidates'][0])
                out=dict(action='choose_action',candidate_id=candidate['candidate_id'],rationale='Registered exploratory step')
            return json.dumps(out),dict(provider=self.cfg.provider,usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
    monkeypatch.setattr(models,'Gateway',Fake)
    result=run(tmp_path)
    assert len(result['rows'])==8 and result['all_cases_met_expectations']
    assert result['evaluated_strategies']==['agent'] and len(Fake.calls)==34
    assert result['summary_functional']['agent']['runs']==6 and result['summary_lifecycle']['agent']['runs']==2
    assert all(r['graph']['version']=='model-numeric-v2' for r in result['rows'])
    assert not result['admission']['independent_holdout_admitted'] and 'waveform_previously_used_for_development' in result['admission']['blockers']
    assert result['summary']['single_router']['runs']==0
