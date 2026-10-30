"""Check M2 evidence completeness without promoting missing diagnostic qualification."""
from hashlib import sha256
import json
import re
from xml.etree import ElementTree

from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.services.tool_contract import m2_tool_contract


def read(relative):return json.loads((PROJECT_ROOT/relative).read_text(encoding='utf8'))


def main():
    plan=read('docs/acceptance/m2-exit-plan.json')
    suite=ElementTree.parse(PROJECT_ROOT/'experiments/m2-tests.xml').getroot().find('testsuite')
    assert suite is not None
    assert all(int(suite.attrib[key])==0 for key in ('failures','errors','skipped'))
    tests=suite.findall('testcase')
    assert len(tests)==int(suite.attrib['tests'])
    documented=set(re.findall(r'^\| (M2-AT\d+-[A-Z]\d+) \|',(PROJECT_ROOT/'docs/06-m2-tool-design.md').read_text(encoding='utf8'),re.M))
    assert documented=={item['id'] for item in plan['cases']} and len(documented)==plan['expected_case_count']==18
    mapped=[]
    for case in plan['cases']:
        selectors=[]
        for selector in case['tests']:
            classname,name=selector.split('::')
            matched=[test for test in tests if test.attrib['classname']==classname and
                (test.attrib['name']==name or test.attrib['name'].startswith(name+'['))]
            assert matched, f'No executed test for {selector}'
            assert all(not any(test.find(tag) is not None for tag in ('failure','error','skipped')) for test in matched)
            selectors.append({'selector':selector,'executed_cases':len(matched)})
        mapped.append({'id':case['id'],'acceptance_status':case['status'],'executed_tests':selectors,'limitation':case['limitation']})
    receipts={}
    for path in plan['required_current_receipts']:
        receipt=read(path);assert receipt['passed']
        hashes=receipt['source_sha256']
        mismatch=[name for name,digest in hashes.items() if sha256((PROJECT_ROOT/name).read_bytes()).hexdigest()!=digest]
        assert not mismatch,f'Source fingerprint mismatch in {path}: {mismatch}'
        if 'full_backend_tests' in receipt:
            assert int(receipt['full_backend_tests']['tests'])==len(tests)
        receipts[path]={'sha256':sha256((PROJECT_ROOT/path).read_bytes()).hexdigest(),'source_entries_verified':len(hashes)}
    build=read('experiments/m2-frontend-build.json');assert build['passed']
    assert all(sha256((PROJECT_ROOT/path).read_bytes()).hexdigest()==digest for path,digest in build['source_sha256'].items())
    contract=read('docs/contracts/m2-tool-contract.json');assert contract==m2_tool_contract()
    baseline=read('experiments/m2-release-baseline.json')
    assert baseline['public_baseline']['effective_matching_coverage']==0.
    assert baseline['public_baseline']['accuracy'] is None
    assert not contract['authority_boundary']['diagnostic_release_approved']
    tool=read('experiments/m2-acceptance-tool-http.json')
    assert tool['mixed_concurrency']['observed_two_running'] and tool['mixed_concurrency']['over_capacity_http_status']==429
    comparison=read('experiments/m2-acceptance-comparison-http.json')
    assert comparison['business_tables']==27 and comparison['actual_process_recovery']['same_resource_id']
    paths=[p for folder in ('backend/src','backend/migrations','backend/scripts','backend/tests','frontend/src','datasets')
        for p in (PROJECT_ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts
        and p.suffix in {'.py','.sql','.ts','.vue','.css','.json'}]
    paths += [PROJECT_ROOT/path for path in ('backend/requirements.lock.txt','frontend/package-lock.json',
        'docs/contracts/m2-tool-contract.json','docs/acceptance/m2-exit-plan.json','experiments/m2-tests.xml',
        'experiments/m2-frontend-build.json','experiments/m2-matching-v1-regression.json',*plan['required_current_receipts'])]
    result={'status':'conditionally_passed','software_stage_complete':True,'stage':'M2 v0.2 local exploratory tool software',
        'approved_next_step':'M3 bounded exploratory agent development; no diagnostic release',
        'test_results':{key:suite.attrib[key] for key in ('tests','failures','errors','skipped','time')},
        'case_results':mapped,'case_status_counts':{'passed':sum(row['acceptance_status']=='passed' for row in mapped),
            'limited':sum(row['acceptance_status']=='limited' for row in mapped)},
        'current_receipts':receipts,'frontend_build_passed':True,'migration_head':'0012_comparison_runs','business_tables':27,
        'tool_count':4,'matching_algorithm_version':'frequency-matching-v2','release_conditions':plan['release_conditions'],
        'm3_entry_constraints':plan['m3_entry_constraints'],'public_strict_matching_coverage':0.,'fault_accuracy':None,
        'diagnostic_profiles_qualified':0,'factory_trial_approved':False,'sealed_test_released':False,
        'source_sha256':{path.relative_to(PROJECT_ROOT).as_posix():sha256(path.read_bytes()).hexdigest() for path in sorted(set(paths))}}
    (PROJECT_ROOT/'experiments/m2-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({key:result[key] for key in ('status','software_stage_complete','test_results','case_status_counts','tool_count','factory_trial_approved')}))


if __name__=='__main__':main()
