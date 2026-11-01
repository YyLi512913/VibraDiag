import json
from vibradiag.evaluation.method_v2_regression import run
from vibradiag.services import agent_model_service as models
from test_knowledge_indexes import model


def test_frozen_repetitions_use_v2_tasks_and_predeclared_audit(database,model,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_AGENT_WORKER_ENABLED','false');monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'data'))
    class Fake:
        calls=[]
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            self.calls.append(phase)
            if phase=='decision_method_v2':output=context['response_examples'][0]
            else:
                source=next((s for s in context['sources'] if 'verify shaft speed' in s['text']),None)
                output=dict(outcome='cited_context',selections=[dict(source_id=source['source_id'],quote='verify shaft speed, bearing geometry and their uncertainty')]) if source else dict(outcome='insufficient_evidence',selections=[])
            return json.dumps(output),dict(provider=self.cfg.provider,requested_model=self.cfg.model,policy_sha256=self.cfg.policy_sha256,usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
    monkeypatch.setattr(models,'Gateway',Fake)
    result=run(tmp_path)
    assert result['all_cases_met_expectations'],[(r['case_id'],r['metrics']) for r in result['rows']]
    assert result['summary']['method_functional']==dict(runs=5,passed=5)
    assert result['summary']['method_lifecycle']==dict(runs=2,passed=2)
    assert len(result['rows'])==7 and len(Fake.calls)==13
    assert result['model_reserved']==result['model_confirmed']==13 and result['model_unknown']==0
    assert result['graph_and_policy_bindings_match'] and result['diagnostic_schema_failures']==0
    assert not result['quality_audit']['post_measurement_review'] and result['audit_protocol_timing']=='frozen_before_this_measurement'
    assert {r['case_id'] for r in result['rows'] if r['metrics']['lifecycle_injected']}=={'K04','K04-R2'}
    assert not result['semantic_quality_qualified'] and not result['factory_trial_approved']
