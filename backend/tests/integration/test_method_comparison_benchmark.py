import json
from vibradiag.evaluation.method_comparison_benchmark import run
from vibradiag.services import agent_model_service as models
from test_knowledge_indexes import model


def test_matrix_uses_real_tasks_sources_and_comparison_parents(database,model,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_AGENT_WORKER_ENABLED','false')
    monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'data'))
    class Fake:
        calls=[]
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            self.calls.append(phase)
            if phase=='decision':output=dict(action='choose_action',candidate_id=context['candidates'][0]['candidate_id'],rationale='Search authorized sources')
            else:
                source=next((s for s in context['sources'] if 'verify shaft speed' in s['text']),None)
                output=dict(outcome='cited_context',selections=[dict(source_id=source['source_id'],quote='verify shaft speed, bearing geometry and their uncertainty')]) if source else dict(outcome='insufficient_evidence',selections=[])
            return json.dumps(output),dict(provider=self.cfg.provider,requested_model=self.cfg.model,usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
    monkeypatch.setattr(models,'Gateway',Fake)
    result=run(tmp_path)
    assert result['all_cases_met_expectations'],[(r['case_id'],r['status'],r['metrics']) for r in result['rows']]
    assert result['summary']==dict(method=dict(runs=5,passed=5),comparison=dict(runs=4,passed=4))
    assert result['model_reserved']==result['model_confirmed']==9 and result['model_unknown']==0
    assert len(Fake.calls)==9
    by_id={r['case_id']:r for r in result['rows']}
    assert by_id['K04']['metrics']['lifecycle_injected'] and by_id['K04']['model_rounds'][-1]['state']=='failed'
    assert by_id['K04']['draft'] is None and by_id['K04']['normalized']['output_state']=='no_visible_draft'
    assert by_id['K03']['embedding_called'] is False
    assert all(by_id[k]['executor']=='rules_graph_v1' and by_id[k]['model_reserved']==0 for k in ('C01','C02','C03','C04'))
    assert by_id['C04']['inventory']['hashes']['before']==by_id['C04']['inventory']['hashes']['after']
    assert not result['independent_holdout'] and not result['semantic_quality_qualified'] and not result['factory_trial_approved']
