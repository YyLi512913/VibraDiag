from copy import deepcopy
from dataclasses import replace
import json
from uuid import uuid4
import numpy as np
import pytest
from vibradiag.analysis.bearing_frequencies import calculate_bearing_frequencies
from vibradiag.analysis.envelope import VERSION as ENVELOPE_VERSION, analyze_envelope
from vibradiag.analysis.matching import match_frequency_evidence
from vibradiag.domain.tool_results import NumericToolResult, ToolInputError

EXACT=dict(kind='synthetic_exact',reference='Analytic fixture, not manufacturer parameters',version='1')


def fixture(count=48000):
    revision,context=uuid4(),uuid4()
    source=dict(measurement_revision_id=str(revision),context_id=str(context),input_sha256='a'*64)
    p=dict(envelope_source=dict(source,execution_id=str(uuid4())),frequency_source=dict(source,execution_id=str(uuid4())))
    params=dict(parameterization='coefficients',coefficients=dict(values=dict(FTF=13/30,BPFO=3.17,BPFI=5.83,BSF=2.17),
        unit='shaft_order',bsf_definition='spin',rotation_assumption='inner_ring_rotates_outer_fixed',
        applicability='Exact synthetic fixture',source=EXACT,checked=True,
        uncertainty=dict(FTF=0.,BPFO=0.,BPFI=0.,BSF=0.,source=EXACT)),conditions=dict(
        target=dict(measurement_revision_id=str(revision),component_id=str(uuid4()),bearing_revision_id=str(uuid4())),
        rpm=1800.,rpm_basis='synthetic_exact',rpm_source=EXACT,rpm_checked=True,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=EXACT,
        speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'))
    theory=calculate_bearing_frequencies(params)
    f=np.arange(count//2+1)*12000/count; a=np.ones_like(f)*.01
    env=NumericToolResult('analyze_envelope',ENVELOPE_VERSION,'ok',dict(sampling_rate_hz=12000.,input_sample_count=count+322,
        output_unit='m/s2',bin_spacing_hz=12000/count,evidence_preconditions_satisfied=False,
        effective_segment=dict(start_index=161,stop_index_exclusive=count+161,sample_count=count),
        configuration=dict(sampling_rate_hz=12000.,modulation_range_hz=[10.,500.],band_hz=[2000.,4000.])),
        dict(frequency_hz=f,amplitude_m_s2=a))
    return env,theory,p


def observe(env,theory,p,frequency=95.0,amplitude=1.,basic=None):
    env.arrays['amplitude_m_s2'][np.argmin(abs(env.arrays['frequency_hz']-frequency))]=amplitude
    return match_frequency_evidence(env,theory,p,basic_spectrum=basic)


def row(result,identifier='BPFO:1'):
    assert result.status=='ok',result
    return next(r for r in result.summary['rows'] if r['target_id']==identifier)


def test_local_peak_supported_in_window_with_exact_observation_and_limits():
    env,theory,p=fixture(); result=observe(env,theory,p); target=row(result)
    assert target['status']=='observed_support'
    peak=target['candidates'][0]
    assert peak['frequency_hz']==95 and peak['bin']==380
    assert peak['frequency_error_hz']==pytest.approx(-.1)
    assert peak['amplitude_m_s2']==1 and peak['amplitude_ratio']==100
    assert peak['prominence_ratio']==99
    assert target['half_width_hz']==pytest.approx(.5+.951)
    assert target['reference']['bin_count']>=20
    assert not result.summary['evidence_eligible'] and not result.summary['confounder_checks_complete']
    assert result.summary['diagnosis'] is None and result.summary['independent_acquisition_count']==1
    json.dumps(result.metadata(),allow_nan=False)


def test_flat_spectrum_and_nearest_bin_are_not_hits():
    env,theory,p=fixture(); target=row(match_frequency_evidence(env,theory,p))
    assert target['status']=='no_observed_support' and target['candidates']==[]


def test_outside_window_and_low_significance_no_support():
    env,theory,p=fixture()
    assert row(observe(env,theory,p,frequency=97.))['status']=='no_observed_support'
    env,theory,p=fixture()
    assert row(observe(env,theory,p,amplitude=.02))['status']=='no_observed_support'


def test_peak_on_window_edge_is_found_using_full_spectrum():
    env,theory,p=fixture()
    # Window93.649..96.551:96.5 is its last in-window FFT bin.
    result=observe(env,theory,p,frequency=96.5)
    assert any(c['frequency_hz']==96.5 for c in row(result)['candidates'])


def test_multiple_local_peaks_are_ambiguous_and_no_highest_peak_classification():
    env,theory,p=fixture()
    env.arrays['amplitude_m_s2'][376]=.6
    target=row(observe(env,theory,p))
    assert target['status']=='ambiguous' and 'multiple_peaks' in target['reasons']
    assert target['candidate_count']==2 and len(target['candidates'])==2


def test_multiple_insignificant_peaks_do_not_create_ambiguity():
    env,theory,p=fixture()
    env.arrays['amplitude_m_s2'][376]=.02
    target=row(observe(env,theory,p,amplitude=.02))
    assert target['candidate_count']==2
    assert all(not candidate['passes_thresholds'] for candidate in target['candidates'])
    assert target['status']=='no_observed_support' and 'multiple_peaks' not in target['reasons']


def test_one_significant_peak_with_weak_neighbors_retains_observed_support():
    env,theory,p=fixture()
    env.arrays['amplitude_m_s2'][376]=.02
    target=row(observe(env,theory,p))
    assert target['candidate_count']==2
    assert sum(candidate['passes_thresholds'] for candidate in target['candidates'])==1
    assert target['status']=='observed_support' and 'multiple_peaks' not in target['reasons']


def test_zero_spectrum_is_not_evaluable_not_health():
    env,theory,p=fixture(); env.arrays['amplitude_m_s2'][:]=0
    target=row(match_frequency_evidence(env,theory,p))
    assert target['status']=='not_evaluable' and 'no_dynamic_information' in target['reasons']


def test_zero_local_reference_uses_flagged_numerical_floor():
    env,theory,p=fixture(); env.arrays['amplitude_m_s2'][:]=0
    target=row(observe(env,theory,p))
    assert target['reference']['reference_floor_applied']
    assert target['reference']['median_or_floor_m_s2']==64*np.finfo(float).eps
    assert np.isfinite(target['candidates'][0]['amplitude_ratio'])


def test_short_observation_marks_resolution_limited_without_clamping():
    env,theory,p=fixture(1024)
    target=row(observe(env,theory,p))
    assert target['status']=='not_evaluable' and target['reasons']==['resolution_limited']
    assert target['half_width_hz']>target['target_hz']*.05


def test_out_of_range_window_not_clipped_into_no_support():
    env,theory,p=fixture()
    env.summary['configuration']['modulation_range_hz']=[94.,500.]
    target=row(observe(env,theory,p))
    assert target['status']=='not_evaluable' and 'window_outside_observation_range' in target['reasons']


def test_no_reference_bins_does_not_invent_zero_background():
    env,theory,p=fixture()
    env.summary['configuration']['modulation_range_hz']=[93.,97.]
    target=row(observe(env,theory,p))
    assert target['status']=='not_evaluable' and target['reference'] is None
    assert 'noise_reference_insufficient' in target['reasons']


def test_missing_uncertainty_is_unavailable_not_zero():
    env,theory,p=fixture(); params=deepcopy(theory.summary['parameters']); params['coefficients']['uncertainty']=None
    result=match_frequency_evidence(env,calculate_bearing_frequencies(params),p)
    assert result.status=='unavailable' and 'complete_frequency_and_speed_uncertainty' in result.missing_requirements


def test_speed_and_coefficient_uncertainty_preserve_cross_term():
    env,theory,p=fixture(); params=deepcopy(theory.summary['parameters'])
    params['conditions']['rpm_uncertainty_rpm']=18.
    params['coefficients']['uncertainty']['BPFO']=.0317
    result=match_frequency_evidence(env,calculate_bearing_frequencies(params),p)
    target=row(result)
    # h=1: nominal95.1, uncertainty0.951+0.951+0.00951; bins0.5 + slip0.951.
    assert target['half_width_hz']==pytest.approx(.5+.951+.951+.00951+.951)


@pytest.mark.parametrize('field,value',[('measurement_revision_id',str(uuid4())),('context_id',str(uuid4())),('input_sha256','b'*64)])
def test_mismatched_parent_identity_rejected(field,value):
    env,theory,p=fixture(); p['frequency_source'][field]=value
    with pytest.raises(ToolInputError): match_frequency_evidence(env,theory,p)


def test_wrong_theory_revision_and_tampered_units_and_values_rejected():
    for change in ('revision','unit','frequency'):
        env,theory,p=fixture()
        if change=='revision':
            params=deepcopy(theory.summary['parameters']); params['conditions']['target']['measurement_revision_id']=str(uuid4())
            theory=calculate_bearing_frequencies(params)
        elif change=='unit': theory.summary['frequency_unit']='rpm'
        else: theory.summary['frequencies']['BPFO']['frequency_hz']=123.
        with pytest.raises(ToolInputError): match_frequency_evidence(env,theory,p)


@pytest.mark.parametrize('change',['nan','negative','axis','shape','bool','unit','segment','version'])
def test_invalid_spectrum_or_parent_metadata_rejected(change):
    env,theory,p=fixture()
    if change=='nan': env.arrays['amplitude_m_s2'][10]=np.nan
    elif change=='negative': env.arrays['amplitude_m_s2'][10]=-1
    elif change=='axis': env.arrays['frequency_hz'][10]+=.1
    elif change=='shape': env.arrays['amplitude_m_s2']=np.ones(5)
    elif change=='bool': env.arrays['amplitude_m_s2']=env.arrays['amplitude_m_s2']>0
    elif change=='unit': env.summary['output_unit']='g'
    elif change=='segment': env.summary['effective_segment']['start_index']=0
    else: env=replace(env,tool_version='future')
    with pytest.raises(ToolInputError): match_frequency_evidence(env,theory,p)


def test_diagnostic_mode_requires_qualification_and_basic_parent():
    env,theory,p=fixture(); p['execution_mode']='diagnostic_evidence'
    result=match_frequency_evidence(env,theory,p)
    assert result.status=='unavailable'
    assert set(result.missing_requirements)=={'qualified_envelope_parent','same_input_basic_spectrum'}


def test_unavailable_parent_propagates_without_fake_rows():
    env,theory,p=fixture()
    result=match_frequency_evidence(replace(env,status='unavailable'),theory,p)
    assert result.status=='unavailable' and result.summary=={}


def test_shaft_overlap_and_2bsf_alias_do_not_create_independent_support():
    env,theory,p=fixture(); params=deepcopy(theory.summary['parameters'])
    params['coefficients']['values']['BPFO']=3.
    theory=calculate_bearing_frequencies(params)
    result=observe(env,theory,p,frequency=90.)
    target=row(result)
    assert target['status']=='ambiguous'
    assert any(c['kind']=='shaft_harmonic_overlap' for c in target['confounders'])
    alias=row(result,'BSF:2')
    assert alias['aliases']==['2BSF']
    assert not any(r['family']=='2BSF' for r in result.summary['rows'])
    assert row(result,'FTF:1')['auxiliary_only']


def test_shared_peak_identity_and_overlap_group_are_consistent():
    env,theory,p=fixture(); params=deepcopy(theory.summary['parameters'])
    params['coefficients']['values']['BPFI']=3.17
    theory=calculate_bearing_frequencies(params)
    result=observe(env,theory,p)
    left=row(result); right=row(result,'BPFI:1')
    assert left['overlap_group']==right['overlap_group'] and left['overlap_group'] is not None
    assert left['candidates'][0]['peak_id']==right['candidates'][0]['peak_id']
    assert left['status']==right['status']=='ambiguous'
    assert len(result.summary['unique_supporting_peak_ids'])==1


def basic_fixture(p, env, carriers):
    p['basic_source']=dict(p['envelope_source'],execution_id=str(uuid4()))
    count=env.summary['input_sample_count']
    f=np.arange(count//2+1)*12000/count; a=np.ones_like(f)*.001
    for frequency in carriers: a[np.argmin(abs(f-frequency))]=1.
    return dict(frequency_hz=f,amplitude_m_s2=a,sample_count=count)


def test_raw_carrier_beating_is_ambiguous_not_fault():
    env,theory,p=fixture(); basic=basic_fixture(p,env,[3000.,3095.])
    target=row(observe(env,theory,p,basic=basic))
    assert target['status']=='ambiguous'
    beating=next(c for c in target['confounders'] if c['kind']=='possible_carrier_beating')
    assert beating['pairs'][0]['difference_hz']==pytest.approx(95.,abs=12000/basic['sample_count'])


def test_raw_source_required_and_bounded_screen_does_not_claim_exclusion():
    env,theory,p=fixture(); basic=basic_fixture(p,env,[3000.])
    result=observe(env,theory,p,basic=basic)
    assert not result.summary['confounder_checks_complete']
    assert result.summary['confounder_checks']['carrier_beating']=='bounded_screen'
    assert result.summary['confounder_checks']['filter_sensitivity']=='not_checked'
    p.pop('basic_source')
    with pytest.raises(ToolInputError): match_frequency_evidence(env,theory,p,basic_spectrum=basic)


def test_scale_invariance_input_unchanged_and_deterministic():
    env,theory,p=fixture(); env.arrays['amplitude_m_s2'][380]=1.
    before=env.arrays['amplitude_m_s2'].copy()
    first=match_frequency_evidence(env,theory,p); second=match_frequency_evidence(env,theory,p)
    assert first.metadata()==second.metadata()
    np.testing.assert_array_equal(before,env.arrays['amplitude_m_s2'])
    env.arrays['amplitude_m_s2']*=1e100
    scaled=match_frequency_evidence(env,theory,p)
    assert row(scaled)['status']==row(first)['status']
    assert row(scaled)['candidates'][0]['amplitude_ratio']==pytest.approx(100)


def test_extreme_dynamic_range_or_floor_underflow_is_explicit_failure():
    env,theory,p=fixture(); env.arrays['amplitude_m_s2'][:]=0
    result=observe(env,theory,p,amplitude=np.nextafter(0.,1.))
    assert result.status=='failed' and result.error_code=='NUMERIC_RANGE_UNSUPPORTED'


def test_real_envelope_and_theory_pipeline_is_observational_only():
    env,theory,p=fixture()
    count=48322; t=np.arange(count)/12000
    actual=analyze_envelope(2*(1+.25*np.cos(2*np.pi*95*t))*np.cos(2*np.pi*3000*t),dict(
        sampling_rate_hz=12000,acceleration_unit='m/s2',band_low_hz=2000,band_high_hz=4000,
        modulation_low_hz=10,modulation_high_hz=500,selection_reason='Fixed mathematical test'))
    result=match_frequency_evidence(actual,theory,p)
    assert any(c['frequency_hz']==95. for c in row(result)['candidates'])
    assert not result.summary['evidence_eligible']


def test_zero_slip_can_leave_insufficient_local_background_without_silent_expansion():
    env,theory,p=fixture(); p['slip_allowance_relative']=0.
    target=row(observe(env,theory,p))
    assert target['status']=='not_evaluable' and 'noise_reference_insufficient' in target['reasons']


@pytest.mark.parametrize('slip',[True,-.1,.051,float('nan')])
def test_illegal_slip_configuration_rejected(slip):
    env,theory,p=fixture(); p['slip_allowance_relative']=slip
    with pytest.raises(ToolInputError): match_frequency_evidence(env,theory,p)


def test_harmonic_sideband_alias_and_nonpositive_targets_remain_explicit():
    env,theory,p=fixture(); params=deepcopy(theory.summary['parameters']); params['coefficients']['values']['BPFO']=.5
    result=match_frequency_evidence(env,calculate_bearing_frequencies(params),p)
    assert len(result.summary['rows'])==63
    assert row(result,'BPFO:1-2fr')['reasons']==['nonpositive_target']
    assert row(result,'BSF:2+2FTF')['sideband_spacing']=='FTF'
    assert row(result,'BPFO:5+2fr')['harmonic']==5


def test_raw_parent_must_be_full_original_not_effective_envelope_segment():
    env,theory,p=fixture(); basic=basic_fixture(p,env,[3000.])
    basic['sample_count']=48000
    with pytest.raises(ToolInputError): match_frequency_evidence(env,theory,p,basic_spectrum=basic)


def test_many_raw_carriers_are_bounded_and_check_remains_incomplete():
    env,theory,p=fixture(); basic=basic_fixture(p,env,np.arange(2100.,3900.,20.))
    result=observe(env,theory,p,basic=basic)
    assert result.summary['confounder_checks']['raw_peak_screen_truncated']
    assert not result.summary['confounder_checks_complete']


def test_odd_effective_length_and_small_physical_scale_preserve_peak_identity():
    env,theory,p=fixture(48001)
    result=observe(env,theory,p)
    assert row(result)['candidates'][0]['bin']==380
    env.arrays['amplitude_m_s2']*=1e-100
    small=match_frequency_evidence(env,theory,p)
    assert row(small)['candidates'][0]['peak_id']==row(result)['candidates'][0]['peak_id']
    assert row(small)['candidates'][0]['amplitude_ratio']==pytest.approx(100.)


def test_unmodulated_real_carrier_does_not_claim_support_or_health():
    _,theory,p=fixture(); t=np.arange(48322)/12000
    actual=analyze_envelope(2*np.cos(2*np.pi*3000*t),dict(sampling_rate_hz=12000,acceleration_unit='m/s2',
        band_low_hz=2000,band_high_hz=4000,modulation_low_hz=10,modulation_high_hz=500,selection_reason='Unmodulated control'))
    result=match_frequency_evidence(actual,theory,p)
    assert row(result)['status'] in {'no_observed_support','not_evaluable'}
    assert result.summary['diagnosis'] is None


def test_actual_two_carrier_pipeline_preserves_beating_alternative():
    from vibradiag.analysis.basic import compute_basic
    _,theory,p=fixture(); t=np.arange(48322)/12000
    x=2*np.cos(2*np.pi*3000*t)+.2*np.cos(2*np.pi*3095*t)
    actual=analyze_envelope(x,dict(sampling_rate_hz=12000,acceleration_unit='m/s2',band_low_hz=2000,band_high_hz=4000,
        modulation_low_hz=10,modulation_high_hz=500,selection_reason='Two-carrier counterexample'))
    basic=compute_basic(x,12000.,'m/s2')
    p['basic_source']=dict(p['envelope_source'],execution_id=str(uuid4()))
    result=match_frequency_evidence(actual,theory,p,basic_spectrum=dict(frequency_hz=basic.frequency,
        amplitude_m_s2=basic.amplitude,sample_count=len(x)))
    target=row(result)
    assert target['status']=='ambiguous'
    assert any(c['kind']=='possible_carrier_beating' for c in target['confounders'])
