from hashlib import sha256
import json
import runpy
from xml.etree import ElementTree as ET
import pytest
from vibradiag.core.paths import PROJECT_ROOT

audit=runpy.run_path(str(PROJECT_ROOT/'backend/scripts/m3_acceptance_evidence.py'))
verify=audit['verify']


@pytest.fixture
def evidence(tmp_path):
    def write(path,value):
        destination=tmp_path/path;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(value if isinstance(value,str) else json.dumps(value),encoding='utf8')
        return dict(path=path,sha256=sha256(destination.read_bytes()).hexdigest())
    write('backend/src/fixture.py','# frozen runtime\n')
    for path in audit['TOOLING']:write(path,'# separately tested tooling\n')
    suite=ET.Element('testsuite',tests='5',failures='0',errors='0',skipped='0',time='0.1')
    for i in range(5):ET.SubElement(suite,'testcase',classname='tests.demo',name='case_'+str(i))
    full=write('experiments/full.xml',ET.tostring(suite,encoding='unicode'))
    ids=['K01','K02','K03','K04','K05','K01-R2','K04-R2'];rows=[]
    for identifier in ids:
        rows.append(dict(case_id=identifier,executor='model_method_v2',graph=dict(version='model-method-v2'),
                         metrics=dict(lifecycle_injected=identifier in {'K04','K04-R2'}),
                         model_rounds=[dict(trace=dict(policy_sha256='fixture')) for _ in range(1 if identifier=='K03' else 2)]))
    flags=dict(independent_holdout=False,semantic_quality_qualified=False,factory_trial_approved=False)
    method=dict(flags,run_completed=True,all_cases_met_expectations=True,graph_and_policy_bindings_match=True,
                summary=dict(method_functional=dict(runs=5,passed=5),method_lifecycle=dict(runs=2,passed=2)),
                model_reserved=13,model_confirmed=13,model_unknown=0,diagnostic_schema_failures=0,
                audit_protocol_timing='frozen_before_this_measurement',quality_audit=dict(post_measurement_review=False),
                rows=rows,configuration=dict(policy_sha256='fixture'),source_fingerprints={'backend/src/fixture.py':audit['file_hash'](tmp_path,'backend/src/fixture.py')})
    numeric=dict(flags,run_completed=True,all_cases_met_expectations=True,rows=[{} for _ in range(8)],
                 summary=dict(agent=dict(expectations_met=8,model_confirmed=34)),admission=dict(independent_holdout_admitted=False,blockers=['no independent entity']))
    contract=dict(formal_diagnostic_release=False,factory_trial_approved=False,
                  **{k:dict(default_mode='rules') for k in ('model_method','model_method_v2','model_numeric','model_numeric_v2')})
    groups=sorted(audit['GROUPS'])
    plan=dict(version='m3-local-software-exit-v1',expected_full_test_count=5,expected_software_case_count=5,
              full_backend_tests=full,method_v2=write('experiments/method.json',method),numeric_v2=write('experiments/numeric.json',numeric),
              contract=write('docs/contract.json',contract),allowed_new_backend_sources=sorted(audit['TOOLING']),
              software_cases=[dict(id='L'+str(i),requirement=g,title=g,tests=['tests.demo::case_'+str(i)],limitation='local only') for i,g in enumerate(groups)],
              requirement_decisions=[dict(id=g,status='limited' if g in {'M3-AT01','M3-AT05'} else 'passed_local') for g in groups],
              qualification_blockers=['blocked']*4,
              historical_evidence=[dict(id='old',scope='historical only',**write('experiments/old.json',dict(passed=True,source_sha256={'backend/src/fixture.py':'old-hash'})))],
              retained_observations=[dict(expected_all_cases_met=False,**write('experiments/failure.json',dict(all_cases_met_expectations=False)))])
    def update(key,change):
        value=json.loads((tmp_path/plan[key]['path']).read_text(encoding='utf8'));change(value)
        plan[key]=write(plan[key]['path'],value)
    return tmp_path,plan,update,write


def test_local_pass_never_grants_full_m3_or_factory_admission(evidence):
    root,plan,_,_=evidence;result=verify(root,plan)
    assert result['local_software_conditional_pass'] and result['m4_development_ready']
    assert not result['m3_full_acceptance_pass'] and not result['factory_trial_approved'] and not result['formal_diagnostic_release']
    assert result['selected_unique_testcases']==5 and result['historical_evidence'][0]['historical_only']
    assert result['historical_evidence'][0]['source_drift_since_run']==['backend/src/fixture.py']


def test_changed_runtime_or_unverified_source_addition_blocks_exit(evidence):
    root,plan,_,write=evidence
    write('backend/src/new.py','# unverified\n')
    with pytest.raises(ValueError,match='source additions'):verify(root,plan)
    (root/'backend/src/new.py').unlink()
    write('backend/src/fixture.py','# changed\n')
    with pytest.raises(ValueError,match='Source changed'):verify(root,plan)


@pytest.mark.parametrize('mode',['aggregate_failure','testcase_skip','wrong_count','empty'])
def test_bad_junit_cannot_be_promoted(evidence,mode):
    root,plan,_,write=evidence
    tree=ET.parse(root/plan['full_backend_tests']['path']);suite=tree.getroot()
    if mode=='aggregate_failure':suite.set('failures','1')
    elif mode=='testcase_skip':ET.SubElement(suite.find('testcase'),'skipped')
    elif mode=='wrong_count':suite.set('tests','6')
    else:suite.clear();suite.set('tests','0')
    plan['full_backend_tests']=write(plan['full_backend_tests']['path'],ET.tostring(suite,encoding='unicode'))
    with pytest.raises(ValueError):verify(root,plan)


@pytest.mark.parametrize('mode',['missing_selector','duplicate_case','missing_group','promoted_requirement'])
def test_incomplete_traceability_or_silent_scope_promotion_fails(evidence,mode):
    root,plan,_,_=evidence
    if mode=='missing_selector':plan['software_cases'][0]['tests']=['tests.demo::not_executed']
    elif mode=='duplicate_case':plan['software_cases'][1]['id']=plan['software_cases'][0]['id']
    elif mode=='missing_group':plan['software_cases'][0]['requirement']='M3-AT02'
    else:plan['requirement_decisions'][0]['status']='passed'
    with pytest.raises(ValueError):verify(root,plan)


@pytest.mark.parametrize('key,change',[
    ('method_v2',lambda d:d.update(all_cases_met_expectations=False)),
    ('method_v2',lambda d:d.update(model_unknown=1)),
    ('method_v2',lambda d:d['rows'][0]['model_rounds'][0].update(trace=None)),
    ('method_v2',lambda d:d['rows'][3]['metrics'].update(lifecycle_injected=False)),
    ('numeric_v2',lambda d:d['admission'].update(independent_holdout_admitted=True)),
    ('contract',lambda d:d.update(formal_diagnostic_release=True)),
    ('contract',lambda d:d['model_method_v2'].update(default_mode='model_v2')),
])
def test_failed_model_evidence_or_released_qualification_is_rejected(evidence,key,change):
    root,plan,update,_=evidence;update(key,change)
    with pytest.raises(ValueError):verify(root,plan)


def test_original_failure_cannot_be_replaced_or_relabelled(evidence):
    root,plan,_,write=evidence
    item=plan['retained_observations'][0]
    write(item['path'],dict(all_cases_met_expectations=True))
    with pytest.raises(ValueError,match='Pinned evidence changed'):verify(root,plan)
    item['sha256']=audit['file_hash'](root,item['path'])
    with pytest.raises(ValueError,match='Original observation outcome changed'):verify(root,plan)


def test_repository_receipts_and_all_original_acceptance_groups_are_auditable():
    plan=json.loads((PROJECT_ROOT/'docs/acceptance/m3-exit-plan.json').read_text(encoding='utf8'))
    result=verify(PROJECT_ROOT,plan)
    assert len(result['software_cases'])==14 and result['test_results']['tests']==850
    assert result['runtime_unchanged_since_latest_full_suite'] and not result['full_suite_rerun']
