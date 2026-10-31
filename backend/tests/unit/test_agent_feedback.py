from copy import deepcopy
from vibradiag.evaluation.agent_admission import audit
from vibradiag.evaluation.feedback_benchmark import assess


def record(split,hash,family='analytic_am_v1'):return dict(split=split,signal_sha256=hash,source_family=family)


def test_different_waveform_bytes_do_not_establish_independent_holdout():
    result=audit([record('development','a'),record('prospective_synthetic','b')],[])
    assert result['synthetic_waveform_bytes_disjoint'] and not result['independent_holdout_admitted']
    assert 'same_source_family_across_partitions' in result['blockers']
    assert 'independent_annotations_missing' in result['blockers']
    assert not result['diagnostic_release'] and not result['factory_trial_approved']


def test_leakage_detected_by_bytes_even_if_names_or_groups_change():
    result=audit([record('development','same','entity-A'),record('prospective_synthetic','same','entity-B')],['same'],independent_annotations=True,blind_holdout=True)
    assert 'exact_waveform_overlap_between_partitions' in result['blockers']
    assert 'waveform_previously_used_for_development' in result['blockers']
    assert not result['synthetic_waveform_bytes_disjoint']


def test_synthetic_and_missing_partition_cannot_pass_with_claimed_annotations():
    result=audit([record('development','a')],[],independent_annotations=True,blind_holdout=True)
    assert 'both_partitions_required' in result['blockers']
    assert 'synthetic_controls_are_not_physical_entity_holdout' in result['blockers']


def case_and_task():
    case=dict(fault='none',uncertainty_complete=False)
    task=dict(status='completed_degraded',calls=[dict(action=a,resource_id=str(i),state='completed') for i,a in enumerate(('analyze_basic','calculate_bearing_frequencies'))],
        draft=dict(observations=[dict(citations=[dict(id='0',kind='basic_analysis')])],missing_requirements=['diagnostic_profile_not_qualified']),draft_validity=dict(usable=True))
    return case,task


def test_frequency_order_is_checked_separately_from_tool_counts():
    case,task=case_and_task();assert assess(case,task,2,2,False)['expectation_met']
    task['calls'].reverse();result=assess(case,task,2,2,False)
    assert result['tool_coverage_exact'] and not result['frequency_before_envelope'] and not result['expectation_met']


def test_fault_without_injection_or_with_late_tool_never_passes():
    case,task=case_and_task();case['fault']='cancel_after_frequency';task['status']='cancelled';task['draft']=None
    assert not assess(case,task,2,2,False)['expectation_met']
    assert assess(case,task,2,2,True)['expectation_met']
    task['calls'].append(dict(action='analyze_envelope',state='completed',resource_id='extra'))
    assert not assess(case,task,2,2,True)['expectation_met']


def test_invalid_or_unqualified_citations_fail_feedback_quality():
    case,task=case_and_task();task['draft']['observations'][0]['citations'][0]['id']='invented'
    assert not assess(case,task,2,2,False)['expectation_met']
    task['draft']['observations'][0]['citations'][0]['id']='0';task['draft']['missing_requirements']=[]
    assert not assess(case,task,2,2,False)['expectation_met']


def test_boolean_claims_and_new_entity_names_cannot_replace_verified_evidence():
    result=audit([record('development','a','named-device-A'),record('prospective_synthetic','b','named-device-B')],[],independent_annotations=True,blind_holdout=True)
    assert not result['independent_holdout_admitted']
    assert 'independent_entity_and_annotation_evidence_not_verified' in result['blockers']


def test_instruction_order_failure_is_not_misreported_as_cancel_fence_failure():
    case,task=case_and_task();case['fault']='cancel_after_frequency';task['status']='cancelled';task['draft']=None
    task['calls'].insert(1,dict(action='analyze_envelope',state='completed',resource_id='extra'))
    metric=assess(case,task,3,3,True,calls_at_injection=3)
    assert metric['lifecycle_boundary_valid'] and not metric['frequency_before_envelope'] and not metric['expectation_met']
    task['calls'].append(dict(action='calculate_bearing_frequencies',state='completed',resource_id='late'))
    assert not assess(case,task,3,3,True,calls_at_injection=3)['lifecycle_boundary_valid']
