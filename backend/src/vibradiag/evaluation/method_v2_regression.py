"""Known-case method-v2 regression, with repetitions and scoring frozen before calls."""
from hashlib import sha256
import json
from vibradiag.core.paths import BACKEND_ROOT,PROJECT_ROOT
from vibradiag.infrastructure.llm.formal_gateway import configuration,METHOD_V2_PHASES
from vibradiag.evaluation.method_comparison_benchmark import run as base_run
from vibradiag.evaluation.method_comparison_review import review

PROTOCOL=BACKEND_ROOT/'config/agent-method-v2-regression-v1.json'


def run(private,progress=lambda value:None):
    spec=json.loads(PROTOCOL.read_text(encoding='utf8'));cfg=configuration('deepseek',purpose='method_v2')
    if cfg.policy_sha256!=spec['expected_method_policy_sha256']:raise RuntimeError('Frozen method-v2 policy required')
    for source in spec['previous_receipts']:
        if sha256((PROJECT_ROOT/source['path']).read_bytes()).hexdigest()!=source['sha256']:raise RuntimeError('Previous observation changed')
    result=base_run(private,progress,protocol_path=PROTOCOL,method_mode='model_v2')
    quality=review(result,spec,extract_phase='extractive_method_v2');quality['post_measurement_review']=False
    bindings=all(row['executor']=='model_method_v2' and row['graph']['version']=='model-method-v2' and row['model_rounds'] and
                 all(r['phase'] in METHOD_V2_PHASES and r['trace'] and r['trace']['policy_sha256']==cfg.policy_sha256 for r in row['model_rounds']) for row in result['rows'])
    result.update(raw_rubric_summary=result['summary'],raw_rubric_all_cases_met_expectations=result['all_cases_met_expectations'],
                  summary=quality['summary'],quality_audit=quality,audit_protocol_timing='frozen_before_this_measurement',
                  graph_and_policy_bindings_match=bool(bindings),all_cases_met_expectations=quality['all_cases_met_expectations'] and bool(bindings),
                  diagnostic_schema_failures=sum(r['error_code']=='MODEL_OUTPUT_SCHEMA_INVALID' for row in result['rows'] for r in row['model_rounds']))
    return result
