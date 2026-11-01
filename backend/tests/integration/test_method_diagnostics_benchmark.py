import json
from vibradiag.evaluation.method_diagnostics_benchmark import run
from vibradiag.services import agent_model_service as models
from test_knowledge_indexes import model


def test_new_observation_captures_errors_without_semantic_refusal_credit(database,model,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_AGENT_WORKER_ENABLED','false');monkeypatch.setenv('VIBRADIAG_FORMAL_LLM_PROVIDER','deepseek')
    monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'data'))
    class Fake:
        count=0;calls=0
        def __init__(self,cfg):self.cfg=cfg
        def invoke(self,phase,context,schema,**kwargs):
            type(self).calls+=1
            if phase=='decision':
                type(self).count+=1
                output=dict(action='PRIVATE_MODEL_VALUE') if self.count in {1,4,5} else dict(action='choose_action',candidate_id=context['candidates'][0]['candidate_id'],rationale='Retrieve')
            else:output=dict(outcome='insufficient_evidence',selections=[])
            return json.dumps(output),dict(provider=self.cfg.provider,requested_model=self.cfg.model,policy_sha256=self.cfg.policy_sha256,usage=dict(prompt_tokens=10,completion_tokens=5,total_tokens=15))
    monkeypatch.setattr(models,'Gateway',Fake)
    result=run(tmp_path)
    assert len(result['rows'])==5 and Fake.calls==6
    assert result['schema_failure_observed'] and result['all_observed_schema_failures_have_diagnostics']
    assert {r['case_id'] for r in result['schema_failures']}=={'K01','K04','K05'}
    for row in result['schema_failures']:
        data=row['diagnostics'];assert data['action_kind']=='other' and data['issues'][0]['kind']=='union_tag_invalid'
        assert 'PRIVATE_MODEL_VALUE' not in json.dumps(data)
    audit=result['quality_audit']
    assert audit['summary']['method_functional']==dict(runs=4,passed=2)
    assert audit['summary']['method_lifecycle']==dict(runs=1,passed=0)
    assert not audit['all_cases_met_expectations'] and not result['policy_changed']
    assert not result['original_failure_root_cause_reconstructed'] and not result['semantic_quality_qualified']
