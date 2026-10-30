from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4
import numpy as np
import pytest
from vibradiag.analysis.basic import compute_basic
from vibradiag.analysis.envelope import analyze_envelope
from vibradiag.analysis.comparison import compare_measurements
from vibradiag.domain.tool_results import ToolInputError
from vibradiag.schemas.comparison import ComparisonCapture

SOURCE=dict(kind='synthetic_exact',reference='Analytic capture/operating-condition fixture; no field calibration',version='1')


def pair(envelope=False):
    org,asset,point,component,bearing=[str(uuid4()) for _ in range(5)]
    count=48322 if envelope else 4096
    t=np.arange(count)/12000
    x=2*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t) if envelope else 2*np.cos(2*np.pi*40*np.arange(count)/count)
    captures=[]; basics=[]; envelopes=[]
    for name,scale,time in [('before',1.,'2026-01-01T00:00:00Z'),('after',.5,'2026-01-01T00:01:00Z')]:
        values=x*scale; revision=str(uuid4())
        capture=dict(organization_id=org,measurement_id=str(uuid4()),measurement_revision_id=revision,file_object_id=str(uuid4()),
            input_sha256=sha256(values.tobytes()).hexdigest(),basic_execution_id=str(uuid4()),
            metadata=dict(sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=asset,measurement_point_id=point,
                direction='horizontal_radial',rpm=1800.,load_value=100.,load_unit='Nm',acquired_at=time,source_type='synthetic'),
            provenance=dict(acquisition_id=name,root_acquisition_refs=[name],source=SOURCE,checked=True),
            timing=dict(time_reference='start',source=SOURCE,checked=True),
            speed=dict(target=dict(measurement_revision_id=revision,component_id=component,bearing_revision_id=bearing),rpm=1800.,
                rpm_basis='synthetic_exact',rpm_source=SOURCE,rpm_checked=True,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=SOURCE,
                speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),
            acquisition=dict(low_hz=100.,high_hz=5000.,source=SOURCE,checked=True),
            measurement_chain=dict(configuration_sha256='d'*64,source=SOURCE,checked=True),
            load=dict(quantity='torque',reference='shaft-output-torque',condition='constant_steady_checked',source=SOURCE,checked=True))
        captures.append(capture);basics.append(compute_basic(values,12000.,'m/s2').summary)
        if envelope:
            capture['envelope_execution_id']=str(uuid4())
            envelopes.append(analyze_envelope(values,dict(sampling_rate_hz=12000,acceleration_unit='m/s2',band_low_hz=2000,
                band_high_hz=4000,modulation_low_hz=10,modulation_high_hz=500,selection_reason='Fixed analytic fixture')))
    parameters=dict(order_checked=True,order_source=SOURCE,load_tolerance=dict(absolute=0.,unit='Nm',quantity='torque',reference='shaft-output-torque',source=SOURCE,checked=True))
    return dict(before=captures[0],after=captures[1],before_basic=basics[0],after_basic=basics[1],parameters=parameters,
        **(dict(before_envelope=envelopes[0],after_envelope=envelopes[1]) if envelope else {}))


def block(result,reason):
    assert result.status=='unavailable',result
    assert result.summary['comparison_status']=='not_comparable'
    assert reason in result.summary['reasons']
    assert result.summary['metrics']=={}


def test_same_scoped_conditions_have_correct_deltas_and_no_repair_claim():
    data=pair(); result=compare_measurements(**data)
    assert result.status=='ok' and result.summary['comparison_status']=='comparable'
    rms=result.summary['metrics']['rms']
    assert rms['before']==pytest.approx(2**.5)
    assert rms['after']==pytest.approx(2**.5/2)
    assert rms['delta']==pytest.approx(-2**.5/2)
    assert rms['relative_change_percent']==pytest.approx(-50.)
    assert result.summary['metrics']['mean']['relative_change_percent'] is None
    assert result.summary['repair_effectiveness'] is None and result.summary['health_assessment'] is None
    assert not result.summary['diagnostic_evidence_eligible']
    assert result.summary['statistical_independence']=='not_established'
    assert 'measurement_uncertainty_not_supplied_or_unchecked' in rms['limitations']
    json.dumps(result.metadata(),allow_nan=False)


@pytest.mark.parametrize('path,value,reason',[
    ('metadata.asset_id',None,'known_asset_required'),('metadata.asset_id',str(uuid4()),'different_assets'),
    ('metadata.measurement_point_id',None,'known_measurement_point_required'),
    ('metadata.measurement_point_id',str(uuid4()),'different_measurement_points'),
    ('metadata.direction','unknown','known_direction_required'),('metadata.direction','axial','different_directions'),
    ('speed.target.component_id',str(uuid4()),'different_components'),
    ('speed.target.bearing_revision_id',str(uuid4()),'bearing_parameter_revision_changed'),
    ('timing.checked',False,'after_checked_capture_start_time_required'),
    ('metadata.acquired_at',None,'after_checked_capture_start_time_required'),
    ('provenance.checked',False,'after_acquisition_lineage_required'),
    ('provenance.root_acquisition_refs',[],'after_acquisition_lineage_required'),
    ('measurement_chain.checked',False,'after_checked_measurement_chain_required'),
    ('measurement_chain.configuration_sha256','e'*64,'different_measurement_chains'),
    ('acquisition.high_hz',4000.,'different_acquisition_bandwidths'),
    ('acquisition.checked',False,'after_checked_acquisition_bandwidth_required'),
    ('speed.rpm_basis','estimated','after_reliable_current_speed_required'),
    ('speed.speed_condition','variable','after_checked_steady_speed_reference_required'),
    ('speed.rpm_uncertainty_rpm',None,'after_speed_uncertainty_required'),
    ('load.condition','variable','after_checked_steady_load_required'),
    ('load.reference','motor-input-torque','different_load_quantity_or_reference'),
])
def test_incomplete_or_different_conditions_block_comparison(path,value,reason):
    data=pair(); target=data['after']; keys=path.split('.')
    for key in keys[:-1]: target=target[key]
    target[keys[-1]]=value
    if path=='metadata.asset_id' and value is None:
        data['after']['metadata']['measurement_point_id']=None
    block(compare_measurements(**data),reason)


def test_missing_load_values_and_unknown_units_do_not_produce_percentages():
    data=pair(); data['after']['metadata'].pop('load_value');data['after']['metadata'].pop('load_unit')
    block(compare_measurements(**data),'after_load_value_required')
    data=pair();data['after']['metadata']['load_unit']='horsepower'
    block(compare_measurements(**data),'unsupported_load_units_or_quantity')


def test_order_reference_and_overlapping_or_reversed_capture_times():
    data=pair();data['parameters']['order_checked']=False
    block(compare_measurements(**data),'checked_acquisition_order_required')
    for time in ('2025-12-31T23:59:00Z','2026-01-01T00:00:00.1Z'):
        data=pair();data['after']['metadata']['acquired_at']=time
        block(compare_measurements(**data),'capture_order_reversed_or_overlapping')


@pytest.mark.parametrize('field', ['measurement_id','file_object_id','input_sha256','basic_execution_id'])
def test_duplicate_or_same_source_not_independent_before_after(field):
    data=pair();data['after'][field]=data['before'][field]
    result=compare_measurements(**data)
    assert result.status=='unavailable' and result.summary['comparison_status']=='related_or_same_acquisition'
    assert result.summary['metrics']=={} and result.summary['distinct_declared_acquisitions'] is None


def test_shared_roots_and_normalized_unit_exports_are_related():
    data=pair();data['after']['provenance']['root_acquisition_refs']=['before','derived-window']
    result=compare_measurements(**data)
    assert result.summary['comparison_status']=='related_or_same_acquisition'
    assert result.summary['shared_acquisition_refs']==['before']
    data=pair();data['before']['normalized_signal_sha256']=data['after']['normalized_signal_sha256']='c'*64
    assert 'identical_normalized_samples' in compare_measurements(**data).summary['related_reasons']


def test_speed_reference_one_percent_and_sourced_absolute_bound():
    data=pair();data['after']['metadata']['rpm']=data['after']['speed']['rpm']=1818.
    assert compare_measurements(**data).status=='ok'
    data['after']['metadata']['rpm']=data['after']['speed']['rpm']=1818.1
    block(compare_measurements(**data),'speed_difference_exceeds_declared_tolerance')
    for side in ('before','after'):
        data[side]['speed']['rpm_uncertainty_rpm']=10.
    result=compare_measurements(**data)
    assert result.status=='ok' and result.summary['checks']['speed']['tolerance_rpm']==20.


def test_load_units_convert_only_with_matching_quantity_and_reference():
    data=pair();data['after']['metadata'].update(load_value=.1,load_unit='kN*m')
    result=compare_measurements(**data)
    assert result.status=='ok' and result.summary['checks']['load']['before']==result.summary['checks']['load']['after']==100.
    data['after']['metadata'].update(load_value=.101)
    block(compare_measurements(**data),'load_difference_exceeds_declared_tolerance')
    data['parameters']['load_tolerance']['absolute']=1.
    assert compare_measurements(**data).status=='ok'
    data['after']['load']['reference']='rated-percentage'
    block(compare_measurements(**data),'load_tolerance_scope_mismatch')


def test_percent_load_requires_shared_denominator_reference():
    data=pair()
    for side in ('before','after'):
        data[side]['metadata']['load_unit']='%';data[side]['load'].update(quantity='relative_load',reference='rated-shaft-torque-v1')
    data['parameters']['load_tolerance'].update(unit='%',quantity='relative_load',reference='rated-shaft-torque-v1')
    assert compare_measurements(**data).status=='ok'
    data['after']['load']['reference']='rated-electrical-power-v1'
    block(compare_measurements(**data),'different_load_quantity_or_reference')


def test_g_and_si_acceleration_can_compare_canonical_metrics():
    data=pair(); n=data['before_basic']['sample_count']; x=np.cos(2*np.pi*40*np.arange(n)/n)
    data['before']['metadata']['acceleration_unit']='g'
    data['before_basic']=compute_basic(x,12000.,'g').summary
    data['after_basic']=compute_basic(x*9.80665,12000.,'m/s2').summary
    result=compare_measurements(**data)
    assert result.status=='ok'
    assert result.summary['metrics']['rms']['delta']==pytest.approx(0.,abs=1e-14)


def test_zero_baseline_and_unavailable_shape_metrics_are_null_not_huge_rates():
    data=pair();data['before_basic']=compute_basic(np.zeros(4096),12000.,'m/s2').summary
    result=compare_measurements(**data)
    assert result.summary['metrics']['rms']['relative_change_percent'] is None
    assert result.summary['metrics']['rms']['percent_reason']=='ZERO_BASELINE'
    assert result.summary['metrics']['crest_factor']['delta'] is None
    assert result.summary['metrics']['crest_factor']['delta_reason']=='METRIC_UNAVAILABLE'
    assert result.summary['metrics']['pearson_kurtosis']['before_reason']=='ZERO_VARIANCE'


def test_stated_uncertainty_limits_percentage_and_does_not_mean_significance():
    data=pair()
    for side in ('before','after'):
        data[side]['metric_uncertainties']={'rms':dict(absolute=2.,unit='m/s2',source=SOURCE,checked=True)}
    row=compare_measurements(**data).summary['metrics']['rms']
    assert row['relative_change_percent'] is None and row['percent_reason']=='BASELINE_WITHIN_STATED_UNCERTAINTY'
    assert 'change_within_stated_uncertainty' in row['limitations']


def test_float_resolution_is_flagged_and_unrepresentable_deltas_or_ratios_not_nan():
    data=pair()
    data['before_basic']['metrics']['rms']['value']=1.
    data['after_basic']['metrics']['rms']['value']=np.nextafter(1.,2.)
    row=compare_measurements(**data).summary['metrics']['rms']
    assert 'change_at_float64_resolution' in row['limitations']
    data['before_basic']['metrics']['mean']['value']=-1e308
    data['after_basic']['metrics']['mean']['value']=1e308
    row=compare_measurements(**data).summary['metrics']['mean']
    assert row['delta'] is None and row['delta_reason']=='NUMERIC_RANGE_UNSUPPORTED'
    data['before_basic']['metrics']['rms']['value']=1e-300
    data['after_basic']['metrics']['rms']['value']=1e300
    result=compare_measurements(**data)
    assert result.summary['metrics']['rms']['relative_change_percent'] is None
    assert result.summary['metrics']['rms']['percent_reason']=='NUMERIC_RANGE_UNSUPPORTED'
    json.dumps(result.metadata(),allow_nan=False)


def test_subnormal_baseline_does_not_report_an_exaggerated_percent():
    data=pair();data['before_basic']['metrics']['rms']['value']=np.nextafter(0.,1.)
    row=compare_measurements(**data).summary['metrics']['rms']
    assert row['relative_change_percent'] is None and row['percent_reason']=='BASELINE_AT_NUMERIC_RESOLUTION'


@pytest.mark.parametrize('change',['org','target_revision','rpm_override','nan_metric','metric_unit','wrong_config','count_bool','naive_time','uncertainty_unit','copy'])
def test_invalid_or_conflicting_internal_inputs_rejected(change):
    data=pair()
    if change=='org': data['after']['organization_id']=str(uuid4())
    elif change=='target_revision': data['after']['speed']['target']['measurement_revision_id']=str(uuid4())
    elif change=='rpm_override': data['after']['speed']['rpm']=1700.
    elif change=='nan_metric': data['after_basic']['metrics']['rms']['value']=float('nan')
    elif change=='metric_unit': data['after_basic']['metrics']['rms']['unit']='g'
    elif change=='wrong_config': data['after_basic']['processing']['window']='rectangular'
    elif change=='count_bool': data['after_basic']['sample_count']=True
    elif change=='naive_time': data['after']['metadata']['acquired_at']='2026-01-01T00:01:00'
    elif change=='uncertainty_unit': data['after']['metric_uncertainties']={'rms':dict(absolute=1.,unit='1',source=SOURCE,checked=True)}
    else:
        model=ComparisonCapture.model_validate(data['after']);data['after']=model.model_copy(update={'input_sha256':'wrong'})
    with pytest.raises(ToolInputError): compare_measurements(**data)


def test_real_source_cannot_reuse_exact_synthetic_attestations():
    data=pair();data['after']['metadata']['source_type']='field_upload'
    with pytest.raises(ToolInputError): compare_measurements(**data)


def test_non_synthetic_zero_uncertainty_is_not_exact():
    data=pair()
    data['after']['metric_uncertainties']={'rms':dict(absolute=0.,unit='m/s2',source=dict(SOURCE,kind='user_statement'),checked=True)}
    with pytest.raises(ToolInputError): compare_measurements(**data)


def test_checked_equal_envelope_profiles_compare_full_effective_segment():
    data=pair(envelope=True); result=compare_measurements(**data)
    assert result.status=='ok',result
    for name in ('envelope_mean','envelope_rms','envelope_peak'):
        assert result.summary['metrics'][name]['relative_change_percent']==pytest.approx(-50.,abs=1e-8)


def test_envelope_profile_difference_and_missing_parent_do_not_silently_compare():
    data=pair(envelope=True)
    t=np.arange(48322)/12000;x=(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
    data['after_envelope']=analyze_envelope(x,dict(sampling_rate_hz=12000,acceleration_unit='m/s2',band_low_hz=1900,
        band_high_hz=4100,modulation_low_hz=10,modulation_high_hz=500,selection_reason='Different exploratory band'))
    block(compare_measurements(**data),'different_envelope_processing')
    data=pair(envelope=True);data.pop('after_envelope');data['after'].pop('envelope_execution_id')
    block(compare_measurements(**data),'two_successful_envelope_parents_required')


def test_envelope_tampered_shape_unit_and_source_identity_rejected():
    for change in ('shape','unit','id'):
        data=pair(envelope=True)
        if change=='shape': data['after_envelope'].arrays['envelope_m_s2']=np.ones(1024)
        elif change=='unit': data['after_envelope'].summary['output_unit']='g'
        else: data['after'].pop('envelope_execution_id')
        with pytest.raises(ToolInputError): compare_measurements(**data)


def test_numeric_range_of_load_conversion_is_explicit_failure():
    data=pair();data['after']['metadata'].update(load_value=1e308,load_unit='kN*m')
    result=compare_measurements(**data)
    assert result.status=='failed' and result.error_code=='NUMERIC_RANGE_UNSUPPORTED'


def test_input_summaries_unchanged_and_repeated_results_identical():
    data=pair();saved=deepcopy(data)
    first=compare_measurements(**data);second=compare_measurements(**data)
    assert data==saved and first.metadata()==second.metadata()


def test_sample_counts_and_sampling_rates_must_match_without_resampling():
    data=pair();data['after_basic']=compute_basic(np.ones(8192),12000.,'m/s2').summary
    block(compare_measurements(**data),'different_original_sample_counts')
    data=pair();data['after']['metadata']['sampling_rate_hz']=24000.
    data['after_basic']=compute_basic(np.ones(4096),24000.,'m/s2').summary
    block(compare_measurements(**data),'different_sampling_rates')


def test_unchecked_or_missing_load_tolerance_is_not_implicitly_zero():
    data=pair();data['parameters'].pop('load_tolerance')
    block(compare_measurements(**data),'checked_load_tolerance_required')
    data=pair();data['parameters']['load_tolerance']['checked']=False
    block(compare_measurements(**data),'checked_load_tolerance_required')


def test_direction_other_notes_are_not_silently_equated():
    data=pair()
    data['before']['metadata'].update(direction='other',direction_note='sensor-normal-to-mount-A')
    data['after']['metadata'].update(direction='other',direction_note='sensor-normal-to-mount-B')
    block(compare_measurements(**data),'different_direction_notes')


def test_no_bandwidth_inference_from_sampling_rate():
    data=pair();data['before'].pop('acquisition')
    block(compare_measurements(**data),'before_checked_acquisition_bandwidth_required')
    data=pair();data['after']['acquisition']['high_hz']=6000.
    block(compare_measurements(**data),'after_bandwidth_must_be_below_nyquist')


def test_envelope_band_and_segment_must_match_the_declared_acquisition():
    data=pair(envelope=True)
    for side in ('before','after'): data[side]['acquisition']['high_hz']=3500.
    block(compare_measurements(**data),'before_envelope_band_outside_acquisition_bandwidth')
    data=pair(envelope=True)
    segment=data['after_envelope'].summary['effective_segment']
    segment['start_index']+=1;segment['stop_index_exclusive']+=1
    block(compare_measurements(**data),'different_envelope_effective_segments')


def test_speed_uncertainty_overflow_cannot_turn_tolerance_into_infinity():
    data=pair()
    for side in ('before','after'):
        data[side]['metadata']['rpm']=data[side]['speed']['rpm']=1.7e308
        data[side]['speed']['rpm_uncertainty_rpm']=1e308
    result=compare_measurements(**data)
    assert result.status=='failed' and result.error_code=='NUMERIC_RANGE_UNSUPPORTED'


def test_duplicate_source_takes_precedence_but_retains_missing_conditions():
    data=pair();data['after']['input_sha256']=data['before']['input_sha256']
    data['after']['metadata'].pop('load_value');data['after']['metadata'].pop('load_unit')
    result=compare_measurements(**data)
    assert result.summary['comparison_status']=='related_or_same_acquisition'
    assert 'after_load_value_required' in result.summary['reasons']


def test_unchecked_metric_uncertainty_is_not_treated_as_a_measured_bound():
    data=pair();data['before']['metric_uncertainties']={'rms':dict(absolute=2.,unit='m/s2',source=SOURCE,checked=False)}
    row=compare_measurements(**data).summary['metrics']['rms']
    assert row['relative_change_percent']==pytest.approx(-50.)
    assert 'measurement_uncertainty_not_supplied_or_unchecked' in row['limitations']
