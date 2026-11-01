"""Audit pinned M3 evidence without running models, HTTP servers or database mutations."""
from datetime import datetime,timezone
import json
import runpy
from vibradiag.core.paths import PROJECT_ROOT,BACKEND_ROOT
from m3_acceptance_evidence import verify,junit,file_hash


def main():
    output=PROJECT_ROOT/'experiments/m3-acceptance.json'
    if output.exists():raise RuntimeError('Preserve existing stage acceptance; use a new version for a new decision')
    plan_path='docs/acceptance/m3-exit-plan.json'
    plan=json.loads((PROJECT_ROOT/plan_path).read_text(encoding='utf8'))
    verification='experiments/m3-t05d-audit-tests.xml';audit_tests,_=junit(PROJECT_ROOT/verification)
    result=verify(PROJECT_ROOT,plan)
    stored=json.loads((PROJECT_ROOT/plan['contract']['path']).read_text(encoding='utf8'))
    exported=runpy.run_path(str(BACKEND_ROOT/'scripts/export_m3_agent_contract.py'))['contract']()
    if stored!=exported:raise RuntimeError('Current runtime contract differs from its exporter')
    paths={plan_path,verification,'docs/acceptance/M3-acceptance.md','docs/11-m4-workbench-design.md',
           plan['full_backend_tests']['path'],plan['contract']['path'],plan['method_v2']['path'],plan['numeric_v2']['path'],
           *result['separately_tested_acceptance_tooling']}
    paths.update(r['path'] for r in plan['historical_evidence']+plan['retained_observations'])
    result.update(recorded_at=datetime.now(timezone.utc).isoformat(),protocol_version=plan['version'],
                  acceptance_tool_tests=audit_tests,acceptance_tool_tests_sha256=file_hash(PROJECT_ROOT,verification),
                  model_calls_added=0,database_or_server_started=False,live_resources_revalidated=False,
                  evidence_sha256={p:file_hash(PROJECT_ROOT,p) for p in sorted(paths)},
                  provenance='Repository bytes, pinned test/model receipts and executed test selectors; not independent attestation')
    with output.open('x',encoding='utf8',newline='\n') as stream:stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:result[key] for key in ('status','local_software_conditional_pass','m3_full_acceptance_pass','m4_development_ready','test_results','acceptance_tool_tests','factory_trial_approved')}))


if __name__=='__main__':main()
