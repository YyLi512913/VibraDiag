"""Read-only M3 evidence audit. A local software result never grants model/factory qualification."""
from hashlib import sha256
import json
from pathlib import Path
from xml.etree import ElementTree

TOOLING={
    'backend/scripts/m3_acceptance_evidence.py',
    'backend/scripts/verify_m3_acceptance.py',
    'backend/tests/unit/test_m3_acceptance.py',
}
GROUPS={'M3-AT01','M3-AT02','M3-AT03','M3-AT04','M3-AT05'}


def require(condition, message):
    if not condition:raise ValueError(message)


def file_hash(root, relative):
    path=(root/relative).resolve()
    require(path.is_relative_to(root.resolve()),'Evidence path must stay within the repository')
    return sha256(path.read_bytes()).hexdigest()


def pinned(root, item):
    require(file_hash(root,item['path'])==item['sha256'],'Pinned evidence changed: '+item['path'])
    return json.loads((root/item['path']).read_text(encoding='utf8'))


def junit(path):
    document=ElementTree.parse(path).getroot()
    suites=list(document.iter('testsuite'));cases=list(document.iter('testcase'))
    require(bool(suites) and bool(cases),'Nonempty JUnit report required')
    require(all(int(s.attrib.get(k,0))==0 for s in suites for k in ('failures','errors','skipped')),'JUnit reports failed or skipped tests')
    require(sum(int(s.attrib['tests']) for s in suites)==len(cases),'JUnit count does not match executed cases')
    require(all(not any(c.find(k) is not None for k in ('failure','error','skipped')) for c in cases),'JUnit testcase did not pass')
    identities=[(c.attrib['classname'],c.attrib['name']) for c in cases]
    require(len(set(identities))==len(identities),'Duplicate executed testcase identity')
    return dict(tests=len(cases),seconds=sum(float(s.attrib.get('time',0)) for s in suites)),identities


def source_inventory(root):
    return {p.relative_to(root).as_posix() for folder in ('backend/src','backend/tests','backend/scripts','backend/migrations','backend/config')
            for p in (root/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.json'} and '__pycache__' not in p.parts}


def map_cases(plan, identities):
    cases=plan['software_cases'];ids=[c['id'] for c in cases]
    require(len(ids)==len(set(ids))==plan['expected_software_case_count'],'Acceptance case IDs are incomplete or repeated')
    require({c['requirement'] for c in cases}==GROUPS,'All five original M3 acceptance groups must be mapped')
    output=[];used=set()
    for case in cases:
        require(bool(case['tests']),'Every software case requires executed tests')
        matches=[]
        for selector in case['tests']:
            classname,name=selector.split('::')
            found=[t for t in identities if t[0]==classname and (t[1]==name or t[1].startswith(name+'['))]
            require(bool(found),'No executed testcase for '+selector)
            used.update(found);matches.append(dict(selector=selector,executed_cases=len(found)))
        output.append(dict(id=case['id'],requirement=case['requirement'],title=case['title'],status='verified_local',
                           executed_tests=matches,limitation=case['limitation']))
    return output,len(used)


def verify(root, plan):
    root=Path(root).resolve()
    require(plan['version']=='m3-local-software-exit-v1','Unknown acceptance protocol')
    require(set(plan['allowed_new_backend_sources'])==TOOLING,'Only separately tested acceptance tooling may be added after the full suite')
    tests_ref=plan['full_backend_tests']
    require(file_hash(root,tests_ref['path'])==tests_ref['sha256'],'Pinned full JUnit changed')
    tests,identities=junit(root/tests_ref['path'])
    require(tests['tests']==plan['expected_full_test_count'],'Unexpected full-suite count')
    case_results,unique_selected=map_cases(plan,identities)
    method=pinned(root,plan['method_v2']);numeric=pinned(root,plan['numeric_v2']);contract=pinned(root,plan['contract'])
    require(method['run_completed'] and method['all_cases_met_expectations'] and method['graph_and_policy_bindings_match'],'Method regression did not pass')
    require(method['summary']['method_functional']==dict(runs=5,passed=5) and method['summary']['method_lifecycle']==dict(runs=2,passed=2),'Method development coverage changed')
    require(method['model_reserved']==method['model_confirmed']==13 and method['model_unknown']==method['diagnostic_schema_failures']==0,'Method model-call accounting differs')
    require(method['audit_protocol_timing']=='frozen_before_this_measurement' and not method['quality_audit']['post_measurement_review'],'Predeclared method quality audit required')
    require(len(method['rows'])==7 and len({r['case_id'] for r in method['rows']})==7,'Method task coverage differs')
    require(all(r['executor']=='model_method_v2' and r['graph']['version']=='model-method-v2' for r in method['rows']),'Wrong method graph')
    rounds=[m for r in method['rows'] for m in r['model_rounds']]
    require(len(rounds)==13 and all(m['trace'] and m['trace']['policy_sha256']==method['configuration']['policy_sha256'] for m in rounds),'Missing or mismatched model trace')
    require({r['case_id'] for r in method['rows'] if r['metrics']['lifecycle_injected']}=={'K04','K04-R2'},'Both real withdrawal injections required')
    require(numeric['run_completed'] and numeric['all_cases_met_expectations'] and len(numeric['rows'])==8,'Numeric known-case regression did not pass')
    require(numeric['summary']['agent']['expectations_met']==8 and numeric['summary']['agent']['model_confirmed']==34,'Numeric coverage differs')
    require(not numeric['admission']['independent_holdout_admitted'] and bool(numeric['admission']['blockers']),'Independent-holdout blockers must remain explicit')
    for receipt in (method,numeric):
        require(all(receipt.get(key) is False for key in ('independent_holdout','semantic_quality_qualified','factory_trial_approved')),'Development evidence cannot grant qualification')
    require(contract['formal_diagnostic_release'] is False and contract['factory_trial_approved'] is False,'Runtime contract must retain release blocks')
    require(all(contract[k]['default_mode']=='rules' for k in ('model_method','model_method_v2','model_numeric','model_numeric_v2')),'Rules must remain the packaged default')
    for path,digest in method['source_fingerprints'].items():
        require(file_hash(root,path)==digest,'Source changed since current full-suite/model measurement: '+path)
    expected=set(method['source_fingerprints']);added=source_inventory(root)-expected
    require(added==TOOLING,'Unverified backend source additions or missing acceptance tooling')
    for path in TOOLING:require((root/path).is_file(),'Acceptance tooling missing')
    historical=[]
    for item in plan['historical_evidence']:
        receipt=pinned(root,item)
        require(receipt['passed'] is True,'Historical execution evidence did not pass')
        drift=[p for p,h in receipt['source_sha256'].items() if not (root/p).is_file() or file_hash(root,p)!=h]
        historical.append(dict(id=item['id'],path=item['path'],sha256=item['sha256'],historical_only=True,
                               source_drift_since_run=drift,scope=item['scope']))
    failures=[]
    for item in plan['retained_observations']:
        receipt=pinned(root,item)
        require(receipt['all_cases_met_expectations'] is item['expected_all_cases_met'],'Original observation outcome changed')
        failures.append(dict(path=item['path'],sha256=item['sha256'],all_cases_met_expectations=receipt['all_cases_met_expectations']))
    require(len(plan['requirement_decisions'])==5 and {r['id'] for r in plan['requirement_decisions']}==GROUPS,'Original requirement decisions incomplete')
    require(all(r['status']=='limited' for r in plan['requirement_decisions'] if r['id'] in {'M3-AT01','M3-AT05'}),'Open-ended reasoning/full diagnostic evaluation is not fully admitted')
    require(len(plan['qualification_blockers'])>=4,'Qualification limitations must remain explicit')
    return dict(status='conditionally_passed_local_software',stage='M3 bounded exploratory Agent backend',
                local_software_conditional_pass=True,m3_full_acceptance_pass=False,m4_development_ready=True,
                test_results=tests,full_suite_rerun=False,selected_unique_testcases=unique_selected,software_cases=case_results,
                requirement_decisions=plan['requirement_decisions'],qualification_blockers=plan['qualification_blockers'],
                source_entries_verified=len(expected),runtime_unchanged_since_latest_full_suite=True,
                separately_tested_acceptance_tooling=sorted(TOOLING),historical_evidence=historical,retained_observations=failures,
                known_development_results=dict(method=method['summary'],numeric_agent=numeric['summary']['agent']),
                fault_accuracy=None,semantic_quality_qualified=False,independent_holdout_admitted=False,
                formal_diagnostic_release=False,factory_trial_approved=False,default_mode='rules',
                approved_next_step='M4 workbench development with current authorization, draft validity and release limitations')
