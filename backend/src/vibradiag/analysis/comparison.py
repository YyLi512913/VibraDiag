"""Conservative per-capture comparability and differences; no repair/health verdict."""
import math
from datetime import timedelta
import numpy as np

from vibradiag.analysis.basic import ALGORITHM_VERSION, CONFIG as BASIC_CONFIG
from vibradiag.analysis.envelope import VERSION as ENVELOPE_VERSION, configuration_sha256
from vibradiag.domain.tool_results import NumericRangeError, NumericToolResult, ToolInputError
from vibradiag.schemas.comparison import ComparisonCapture, ComparisonParameters, METRICS
from vibradiag.schemas.tools import validate_parameters

VERSION = 'measurement-comparison-v1'
CONFIG = dict(profile='comparison-v1', rpm_reference_relative_tolerance=.01,
    sampling='identical_rate_and_original_sample_count', basic_segment='full_file',
    envelope_segment='identical_effective_indices', delta='after-minus-before',
    percent='100*(after-before)/before; magnitude metrics only',
    load='same_quantity_reference_and_sourced_absolute_tolerance',
    change_resolution_guard_ulps=2)
LIMITATIONS = (
    'Comparability is conditional on supplied engineering declarations, not independent field verification',
    'Changes are numerical differences, not improvement, health or repair-effectiveness conclusions',
    'Developer rpm/load criteria are not validated universal industrial limits',
    'Source IDs and hashes require server-side authorization and integrity checks before persistence',
    'Distinct declared acquisition roots do not prove statistical independence',
)
BASIC_METRICS = METRICS[:7]
UNITS = {'N':('force',1.,'N'), 'kN':('force',1000.,'N'),
         'N*m':('torque',1.,'N*m'), 'Nm':('torque',1.,'N*m'), 'N·m':('torque',1.,'N*m'),
         'kN*m':('torque',1000.,'N*m'), 'W':('power',1.,'W'), 'kW':('power',1000.,'W'),
         '%':('relative_load',1.,'%')}


def invalid(message, field='results'):
    raise ToolInputError('COMPARISON_INPUT_INVALID', message, field)


def finite(value):
    if isinstance(value, (bool,str)) or not isinstance(value,(int,float,np.number)):
        invalid('Expected real numerical values')
    value=float(value)
    if not math.isfinite(value): invalid('Expected finite numerical values')
    return value


def supported(value):
    if not math.isfinite(value): raise NumericRangeError()
    return value


def declared(source, checked):
    return checked and source is not None


def load_value(value, unit, quantity):
    if unit not in UNITS or UNITS[unit][0] != quantity:
        return None
    _,factor,canonical = UNITS[unit]
    converted=supported(value*factor)
    if value!=0 and converted==0: raise NumericRangeError()
    return converted,canonical


def basic_metadata(summary, capture):
    if not isinstance(summary,dict): invalid('Expected a basic summary')
    try:
        if summary['processing'] != BASIC_CONFIG or summary['processing']['algorithm_version'] != ALGORITHM_VERSION:
            invalid('Unsupported or inconsistent basic processing configuration')
        count=summary['sample_count']; fs=finite(summary['sampling_rate_hz'])
        if type(count)!=int or not 1024<=count<=1_000_000 or fs<=0 or fs!=capture.metadata.sampling_rate_hz:
            invalid('Basic sample count or sampling rate does not match the input')
        if summary['input_unit']!=capture.metadata.acceleration_unit or summary['output_unit']!='m/s2':
            invalid('Basic input/output units do not match the capture')
        if not math.isclose(finite(summary['duration_s']),count/fs,rel_tol=1e-12,abs_tol=0.):
            invalid('Basic duration differs from full input duration')
        result={}
        if set(summary['metrics']) != set(BASIC_METRICS): invalid('Basic metric set is inconsistent')
        for name in BASIC_METRICS:
            item=summary['metrics'][name]; unit='1' if name in {'crest_factor','pearson_kurtosis'} else 'm/s2'
            if item['unit']!=unit: invalid('Basic metric units are inconsistent')
            value=item['value']
            if value is None:
                if item['status']!='unavailable' or not item['reason']: invalid('Missing metric must have an unavailable reason')
            else:
                value=finite(value)
                if name!='mean' and value<0: invalid('Magnitude metrics cannot be negative')
                if item['status']!='available' or item['reason'] is not None: invalid('Metric availability metadata is inconsistent')
            result[name]=dict(value=value,unit=unit,reason=item['reason'])
        return count,result
    except (KeyError,TypeError): invalid('Malformed basic result metadata')


def envelope_metadata(outcome, capture, count):
    if not isinstance(outcome,NumericToolResult) or outcome.tool_name!='analyze_envelope' or outcome.tool_version!=ENVELOPE_VERSION:
        invalid('Wrong envelope tool or version')
    if outcome.status!='ok': return None,None
    try:
        s=outcome.summary; segment=s['effective_segment']
        n=segment['sample_count']; start=segment['start_index']; stop=segment['stop_index_exclusive']
        if any(type(v)!=int for v in (n,start,stop)) or not 1024<=n<=count or not 0<=start<stop<=count or stop-start!=n:
            invalid('Envelope effective segment is inconsistent')
        if s['input_sample_count']!=count or s['sampling_rate_hz']!=capture.metadata.sampling_rate_hz or s['input_unit']!=capture.metadata.acceleration_unit or s['output_unit']!='m/s2':
            invalid('Envelope metadata differs from its capture')
        if configuration_sha256(s['parameters'])!=s['numeric_configuration_sha256']:
            invalid('Envelope configuration fingerprint is inconsistent')
        # Match stored configuration to revalidated parameters, not just a caller string.
        from vibradiag.analysis.envelope import numeric_configuration
        if numeric_configuration(s['parameters'])!=s['configuration']:
            invalid('Envelope processing snapshot is inconsistent')
        p=s['parameters']
        if p['sampling_rate_hz']!=s['sampling_rate_hz'] or p['acceleration_unit']!=s['input_unit']:
            invalid('Envelope parameter snapshot differs from its input')
        values=np.asarray(outcome.arrays['envelope_m_s2'])
        if values.ndim!=1 or values.dtype.kind not in 'fiu' or len(values)!=n or not np.isfinite(values).all() or np.any(values<0):
            invalid('Expected a full finite nonnegative envelope segment')
        peak=float(np.max(values)); scaled=values/peak if peak else np.zeros_like(values)
        if np.any((values!=0)&(scaled==0)): raise NumericRangeError()
        metrics={}
        for name,value in [('envelope_mean',float(scaled.mean())),('envelope_rms',math.sqrt(float(np.mean(scaled*scaled)))),('envelope_peak',1. if peak else 0.)]:
            physical=supported(value*peak)
            if value!=0 and peak!=0 and physical==0: raise NumericRangeError()
            metrics[name]=dict(value=physical,unit='m/s2',reason=None)
        return dict(fingerprint=s['numeric_configuration_sha256'],segment=segment,
            band=(p['band_low_hz'],p['band_high_hz']),configuration=s['configuration']),metrics
    except (KeyError,TypeError): invalid('Malformed envelope result metadata')


def metric_difference(name, before, after, b_uncertainty, a_uncertainty):
    b=before['value']; a=after['value']; unit=before['unit']
    row=dict(before=b,after=a,unit=unit,delta=None,relative_change_percent=None,
        delta_status='unavailable',percent_reason=None,limitations=[])
    if a is None or b is None:
        row.update(delta_reason='METRIC_UNAVAILABLE',percent_reason='METRIC_UNAVAILABLE',
            before_reason=before['reason'],after_reason=after['reason'])
        return row
    delta=a-b
    if not math.isfinite(delta):
        row.update(delta_reason='NUMERIC_RANGE_UNSUPPORTED',percent_reason='NUMERIC_RANGE_UNSUPPORTED')
        return row
    row.update(delta=delta,delta_status='available',delta_reason=None)
    resolution=max(math.ulp(a),math.ulp(b))
    # Division prevents overflowing 2*ulp near the largest finite number.
    if abs(delta)/2<=resolution: row['limitations'].append('change_at_float64_resolution')
    if b!=0 and abs(b)<=math.ulp(0.)*2: row['limitations'].append('baseline_at_float64_subnormal_resolution')
    uncertain=[]
    for u in (b_uncertainty,a_uncertainty):
        if u and u.checked: uncertain.append(u.absolute)
        else: uncertain.append(None)
    if None in uncertain:
        row['limitations'].append('measurement_uncertainty_not_supplied_or_unchecked')
    else:
        total=uncertain[0]+uncertain[1]
        if not math.isfinite(total): row['limitations'].append('uncertainty_sum_outside_numeric_range')
        elif abs(delta)<=total: row['limitations'].append('change_within_stated_uncertainty')
    if name=='mean': row['percent_reason']='SIGNED_MEAN_PERCENT_NOT_DEFINED'
    elif b==0: row['percent_reason']='ZERO_BASELINE'
    elif uncertain[0] is not None and abs(b)<=uncertain[0]:
        row['percent_reason']='BASELINE_WITHIN_STATED_UNCERTAINTY'
    elif abs(b)<=math.ulp(0.)*2:
        row['percent_reason']='BASELINE_AT_NUMERIC_RESOLUTION'
    else:
        try: percent=delta/b*100.
        except (OverflowError,ZeroDivisionError): percent=math.inf
        if not math.isfinite(percent) or (delta!=0 and percent==0): row['percent_reason']='NUMERIC_RANGE_UNSUPPORTED'
        else: row['relative_change_percent']=percent
    return row


def compare_measurements(before, after, before_basic, after_basic, parameters, *, before_envelope=None, after_envelope=None):
    """All inputs are internal snapshots; no database IDs are authenticated here."""
    b=validate_parameters(ComparisonCapture,before); a=validate_parameters(ComparisonCapture,after)
    p=validate_parameters(ComparisonParameters,parameters)
    if b.organization_id!=a.organization_id: invalid('Comparison cannot combine organizations','organization_id')
    try:
        nb,bmetrics=basic_metadata(before_basic,b); na,ametrics=basic_metadata(after_basic,a)
        if (b.envelope_execution_id is None)!=(before_envelope is None) or (a.envelope_execution_id is None)!=(after_envelope is None):
            invalid('Envelope identity and result must be provided together')
        eb,bem=envelope_metadata(before_envelope,b,nb) if before_envelope is not None else (None,None)
        ea,aem=envelope_metadata(after_envelope,a,na) if after_envelope is not None else (None,None)
        reasons=[]; related=[]; checks={}
        def require(condition, reason):
            if not condition and reason not in reasons: reasons.append(reason)
        # Duplicate/same-root detection applies even when other comparison fields are missing.
        if b.measurement_id==a.measurement_id or b.measurement_revision_id==a.measurement_revision_id: related.append('same_measurement_or_revision')
        if b.file_object_id==a.file_object_id: related.append('same_original_file_object')
        if b.input_sha256==a.input_sha256: related.append('identical_original_content')
        if b.normalized_signal_sha256 and b.normalized_signal_sha256==a.normalized_signal_sha256: related.append('identical_normalized_samples')
        if b.basic_execution_id==a.basic_execution_id: related.append('same_basic_execution')
        if b.envelope_execution_id is not None and b.envelope_execution_id==a.envelope_execution_id: related.append('same_envelope_execution')
        if b.provenance.acquisition_id and b.provenance.acquisition_id==a.provenance.acquisition_id: related.append('same_declared_acquisition')
        shared=sorted(set(b.provenance.root_acquisition_refs)&set(a.provenance.root_acquisition_refs))
        if shared: related.append('shared_acquisition_root')
        require(b.metadata.asset_id is not None and a.metadata.asset_id is not None,'known_asset_required')
        require(b.metadata.asset_id==a.metadata.asset_id,'different_assets')
        require(b.metadata.measurement_point_id is not None and a.metadata.measurement_point_id is not None,'known_measurement_point_required')
        require(b.metadata.measurement_point_id==a.metadata.measurement_point_id,'different_measurement_points')
        require(b.metadata.direction!='unknown' and a.metadata.direction!='unknown','known_direction_required')
        require(b.metadata.direction==a.metadata.direction,'different_directions')
        if b.metadata.direction=='other' or a.metadata.direction=='other': require(b.metadata.direction_note==a.metadata.direction_note,'different_direction_notes')
        require(b.speed.target is not None and a.speed.target is not None,'identified_target_required')
        if b.speed.target and a.speed.target:
            require(b.speed.target.component_id==a.speed.target.component_id,'different_components')
            require(b.speed.target.bearing_revision_id==a.speed.target.bearing_revision_id,'bearing_parameter_revision_changed')
        require(b.metadata.source_type!='unknown' and a.metadata.source_type!='unknown','known_input_source_required')
        require(b.metadata.source_type==a.metadata.source_type,'different_input_source_types')
        require(nb==na,'different_original_sample_counts')
        require(b.metadata.sampling_rate_hz==a.metadata.sampling_rate_hz,'different_sampling_rates')
        require(p.order_checked and p.order_source is not None,'checked_acquisition_order_required')
        if p.order_source and p.order_source.kind=='synthetic_exact' and (b.metadata.source_type!='synthetic' or a.metadata.source_type!='synthetic'):
            invalid('Exact synthetic order statement requires synthetic captures','order_source')
        for label,side,count in [('before',b,nb),('after',a,na)]:
            require(declared(side.provenance.source,side.provenance.checked) and bool(side.provenance.acquisition_id) and bool(side.provenance.root_acquisition_refs),label+'_acquisition_lineage_required')
            require(side.metadata.acquired_at is not None and side.timing.time_reference=='start' and declared(side.timing.source,side.timing.checked),label+'_checked_capture_start_time_required')
            require(side.acquisition is not None and declared(side.acquisition.source,side.acquisition.checked),label+'_checked_acquisition_bandwidth_required')
            if side.acquisition: require(side.acquisition.high_hz<side.metadata.sampling_rate_hz/2,label+'_bandwidth_must_be_below_nyquist')
            require(side.measurement_chain is not None and declared(side.measurement_chain.source,side.measurement_chain.checked),label+'_checked_measurement_chain_required')
            s=side.speed
            require(s.rpm is not None and s.rpm==side.metadata.rpm and s.rpm_basis in {'measured','synthetic_exact'} and declared(s.rpm_source,s.rpm_checked),label+'_reliable_current_speed_required')
            if s.rpm_source: require((s.rpm_basis=='synthetic_exact')==(s.rpm_source.kind=='synthetic_exact'),label+'_speed_source_basis_conflict')
            require(s.speed_condition=='constant_steady_checked' and s.rotation=='inner_ring_rotates_outer_fixed' and not s.parameters_conflict,label+'_checked_steady_speed_reference_required')
            require(s.rpm_uncertainty_rpm is not None and s.rpm_uncertainty_source is not None,label+'_speed_uncertainty_required')
            if s.rpm_uncertainty_rpm==0:
                require(s.rpm_basis=='synthetic_exact' and s.rpm_uncertainty_source is not None and s.rpm_uncertainty_source.kind=='synthetic_exact',label+'_zero_speed_uncertainty_requires_exact_source')
            require(side.load.condition=='constant_steady_checked' and side.load.quantity!='unknown' and bool(side.load.reference) and declared(side.load.source,side.load.checked),label+'_checked_steady_load_required')
            require(side.metadata.load_value is not None and side.metadata.load_unit is not None,label+'_load_value_required')
        if b.metadata.acquired_at and a.metadata.acquired_at:
            seconds=nb/b.metadata.sampling_rate_hz
            try:
                end=b.metadata.acquired_at+timedelta(seconds=seconds)
                require(a.metadata.acquired_at>=end and a.metadata.acquired_at>b.metadata.acquired_at,'capture_order_reversed_or_overlapping')
            except (OverflowError,ValueError): raise NumericRangeError() from None
        if b.acquisition and a.acquisition:
            require((b.acquisition.low_hz,b.acquisition.high_hz)==(a.acquisition.low_hz,a.acquisition.high_hz),'different_acquisition_bandwidths')
        if b.measurement_chain and a.measurement_chain:
            require(b.measurement_chain.configuration_sha256==a.measurement_chain.configuration_sha256,'different_measurement_chains')
        if b.speed.rpm is not None and a.speed.rpm is not None and b.speed.rpm_uncertainty_rpm is not None and a.speed.rpm_uncertainty_rpm is not None:
            tolerance=supported(max(supported(b.speed.rpm_uncertainty_rpm+a.speed.rpm_uncertainty_rpm),.01*b.speed.rpm))
            difference=abs(a.speed.rpm-b.speed.rpm)
            checks['speed']=dict(before_rpm=b.speed.rpm,after_rpm=a.speed.rpm,absolute_difference_rpm=difference,tolerance_rpm=tolerance)
            require(difference<=tolerance,'speed_difference_exceeds_declared_tolerance')
        require(b.load.quantity==a.load.quantity and b.load.reference==a.load.reference,'different_load_quantity_or_reference')
        require(p.load_tolerance is not None and p.load_tolerance.checked,'checked_load_tolerance_required')
        if p.load_tolerance:
            lp=p.load_tolerance
            if lp.source.kind=='synthetic_exact' and (b.metadata.source_type!='synthetic' or a.metadata.source_type!='synthetic'):
                invalid('Exact synthetic load tolerance requires synthetic captures','load_tolerance')
            require(lp.quantity==b.load.quantity==a.load.quantity and lp.reference==b.load.reference==a.load.reference,'load_tolerance_scope_mismatch')
            values=[]
            for side in (b,a):
                values.append(load_value(side.metadata.load_value,side.metadata.load_unit,side.load.quantity) if side.metadata.load_value is not None else None)
            tolerance=load_value(lp.absolute,lp.unit,lp.quantity)
            require(all(v is not None for v in values) and tolerance is not None,'unsupported_load_units_or_quantity')
            if all(v is not None for v in values) and tolerance is not None:
                delta=supported(values[1][0]-values[0][0])
                checks['load']=dict(before=values[0][0],after=values[1][0],unit=values[0][1],absolute_difference=abs(delta),tolerance=tolerance[0])
                require(abs(delta)<=tolerance[0],'load_difference_exceeds_declared_tolerance')
        if before_envelope is not None or after_envelope is not None:
            require(eb is not None and ea is not None,'two_successful_envelope_parents_required')
            if eb and ea:
                require(eb['fingerprint']==ea['fingerprint'] and eb['configuration']==ea['configuration'],'different_envelope_processing')
                require(eb['segment']==ea['segment'],'different_envelope_effective_segments')
                for label,side,env in [('before',b,eb),('after',a,ea)]:
                    require(side.acquisition is not None and side.acquisition.low_hz<=env['band'][0] and side.acquisition.high_hz>=env['band'][1],label+'_envelope_band_outside_acquisition_bandwidth')
        comparison_status='related_or_same_acquisition' if related else 'not_comparable' if reasons else 'comparable'
        summary=dict(comparison_status=comparison_status,reasons=reasons,related_reasons=related,shared_acquisition_refs=shared,
            configuration=dict(CONFIG),parameters=p.model_dump(mode='json'),before=b.model_dump(mode='json'),after=a.model_dump(mode='json'),
            checks=checks,metrics={},distinct_declared_acquisitions=2 if not related and not reasons else None,
            statistical_independence='not_established',repair_effectiveness=None,health_assessment=None,diagnostic_evidence_eligible=False)
        if comparison_status!='comparable':
            return NumericToolResult('compare_measurements',VERSION,'unavailable',summary,missing_requirements=tuple(reasons),
                limitations=LIMITATIONS,error_code='RELATED_ACQUISITION' if related else 'NOT_COMPARABLE')
        if bem and aem: bmetrics.update(bem);ametrics.update(aem)
        summary['metrics']={name:metric_difference(name,bmetrics[name],ametrics[name],b.metric_uncertainties.get(name),a.metric_uncertainties.get(name)) for name in bmetrics}
        return NumericToolResult('compare_measurements',VERSION,'ok',summary,limitations=LIMITATIONS)
    except (NumericRangeError,OverflowError,FloatingPointError):
        return NumericToolResult('compare_measurements',VERSION,'failed',limitations=LIMITATIONS,error_code='NUMERIC_RANGE_UNSUPPORTED')
