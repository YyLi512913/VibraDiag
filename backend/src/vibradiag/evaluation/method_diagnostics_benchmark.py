"""New observation under unchanged method policy, not reconstruction of old responses."""
from hashlib import sha256
import json
from vibradiag.core.paths import BACKEND_ROOT,PROJECT_ROOT
from vibradiag.evaluation.method_comparison_benchmark import run as base_run
from vibradiag.evaluation.method_comparison_review import review

PROTOCOL=BACKEND_ROOT/'config/agent-method-diagnostics-v1.json'


def run(private,progress=lambda value:None):
    spec=json.loads(PROTOCOL.read_text(encoding='utf8'))
    previous=PROJECT_ROOT/spec['previous_receipt']
    if sha256(previous.read_bytes()).hexdigest()!=spec['previous_receipt_sha256']:raise RuntimeError('Original failure receipt changed')
    result=base_run(private,progress,protocol_path=PROTOCOL)
    if result['configuration']['policy_sha256']!=spec['expected_method_policy_sha256']:raise RuntimeError('Observation requires unchanged model policy')
    failures=[dict(case_id=row['case_id'],phase=r['phase'],diagnostics=r['validation_diagnostics']) for row in result['rows'] for r in row['model_rounds'] if r['error_code']=='MODEL_OUTPUT_SCHEMA_INVALID']
    result.update(quality_audit=review(result,spec),schema_failures=failures,schema_failure_observed=bool(failures),
                  all_observed_schema_failures_have_diagnostics=all(f['diagnostics'] is not None for f in failures),
                  previous_receipt_sha256=spec['previous_receipt_sha256'],policy_changed=False,original_failure_root_cause_reconstructed=False)
    return result
