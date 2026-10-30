"""Conditional, offline shape research. Never an ingestion or evidence-qualification adapter."""
from dataclasses import dataclass, field
import math

import numpy as np
from vibradiag.analysis.basic import compute_basic
from vibradiag.analysis.envelope import analyze_envelope
from vibradiag.evaluation.datasets import samples_sha256, vector, load_channel, verified_signal

VERSION = 'stored-axis-linear-shape-v1'


@dataclass(frozen=True)
class Adaptation:
    status: str
    summary: dict
    values: np.ndarray | None = field(default=None, repr=False)
    relative_time_s: np.ndarray | None = field(default=None, repr=False)


def check_protocol(protocol):
    expected = {'version':'stored-axis-shape-research-v1','sampling_rate_hz':64000.,
        'interpolation':'piecewise_linear_on_stored_coordinates',
        'min_step_nominal':.1,'max_step_nominal':2.,'max_count_duration_relative_error':.02,
        'max_time_ulp_nominal':1e-6,'min_points':1024,'max_points':1000000,
        'carrier_band_hz':[2000.,4000.],'modulation_range_hz':[10.,500.],
        'prediction':None,'diagnostic_evidence_eligible':False}
    if any(protocol.get(key) != value for key,value in expected.items()):
        raise ValueError('unsupported or changed research protocol')


def adapt_stored_axis(values,axis,protocol,*,assumption_acknowledged=False):
    check_protocol(protocol)
    summary = {'algorithm_version':VERSION,'time_axis_semantics_verified':False,
        'physical_unit_established':False,'diagnostic_evidence_eligible':False,'prediction':None}
    def reject(reason):
        return Adaptation('unavailable',summary|{'reason':reason})
    if assumption_acknowledged is not True:
        return reject('RESEARCH_TIME_ASSUMPTION_NOT_ACKNOWLEDGED')
    try:
        x=vector(values);t=vector(axis)
    except (ValueError,TypeError,OverflowError):
        return reject('NONFINITE_OR_NONVECTOR_INPUT')
    if len(x)!=len(t):return reject('TIME_AXIS_LENGTH_MISMATCH')
    fs=protocol['sampling_rate_hz']
    with np.errstate(over='ignore',invalid='ignore'):
        dt=np.diff(t)
    if not np.isfinite(dt).all():return reject('TIME_RANGE_UNSUPPORTED')
    if not np.all(dt>0):return reject('TIME_AXIS_NOT_STRICTLY_INCREASING')
    with np.errstate(over='ignore',invalid='ignore'):
        normalized_step=dt*fs
        duration=float(t[-1]-t[0]);span_points=duration*fs
    if not math.isfinite(span_points) or span_points<=0:
        return reject('TIME_RANGE_UNSUPPORTED')
    time_ulp=float(np.spacing(float(np.max(np.abs(t))))*fs)
    if not math.isfinite(time_ulp) or time_ulp>protocol['max_time_ulp_nominal']:
        return reject('TIME_COORDINATE_RESOLUTION_INSUFFICIENT')
    count_error=abs((len(x)-1)-span_points)/span_points
    summary.update({'input_count':len(x),'original_samples_sha256':samples_sha256(x),
        'original_axis_sha256':samples_sha256(t),'first_stored_coordinate':float(t[0]),
        'duration_s_under_assumption':duration,'min_step_nominal':float(normalized_step.min()),
        'max_step_nominal':float(normalized_step.max()),'count_duration_relative_error':count_error,
        'time_ulp_nominal':time_ulp})
    if normalized_step.max()>protocol['max_step_nominal']:
        return reject('GAP_EXCEEDS_RESEARCH_LIMIT')
    if normalized_step.min()<protocol['min_step_nominal']:
        return reject('INTERVAL_BELOW_RESEARCH_LIMIT')
    if count_error>protocol['max_count_duration_relative_error']:
        return reject('CADENCE_DIFFERS_FROM_RESEARCH_SCOPE')
    count=math.floor(span_points)+1
    if not protocol['min_points']<=count<=protocol['max_points']:
        return reject('OUTPUT_POINT_LIMIT')
    relative=np.arange(count,dtype=np.float64)/fs
    relative=relative[relative<=duration]
    if len(relative)<protocol['min_points']:return reject('OUTPUT_POINT_LIMIT')
    peak=float(np.max(np.abs(x)))
    proxy=x/peak if peak else np.zeros_like(x)
    if np.any((x!=0)&(proxy==0)):return reject('NORMALIZATION_UNDERFLOW')
    shifted=t-t[0]
    if not np.all(np.diff(shifted)>0):return reject('SHIFTED_TIME_AXIS_LOSES_RESOLUTION')
    output=np.interp(relative,shifted,proxy)
    if not np.isfinite(output).all():return reject('INTERPOLATION_RANGE_UNSUPPORTED')
    if np.any((output==0)&(np.interp(relative,shifted,np.abs(proxy))!=0)):
        # Crossings through zero are legitimate; only a positive-neighbor underflow is suspect.
        indices=np.searchsorted(shifted,relative,side='right').clip(1,len(proxy)-1)
        same_sign=(np.signbit(proxy[indices-1])==np.signbit(proxy[indices]))&(proxy[indices-1]!=0)&(proxy[indices]!=0)
        if np.any((output==0)&same_sign):return reject('INTERPOLATION_UNDERFLOW')
    output.setflags(write=False);relative.setflags(write=False)
    summary.update({'reason':None,'output_count':len(output),'grid_start_relative_s':float(relative[0]),
        'grid_last_relative_s':float(relative[-1]),'uncovered_tail_s':duration-float(relative[-1]),
        'normalization_divisor_in_original_numeric_scale':peak,'output_proxy_sha256':samples_sha256(output),
        'output_axis_sha256':samples_sha256(relative),'proxy_unit':'1',
        'operation':'explicit linear interpolation; no extrapolation; no anti-alias qualification'})
    return Adaptation('ok',summary,output,relative)


def shape_features(values,protocol):
    """Reuse numerical kernels on a dimensionless proxy; suppress every physical output."""
    check_protocol(protocol)
    x=vector(values)
    basic=compute_basic(x,protocol['sampling_rate_hz'],'m/s2')
    params={'sampling_rate_hz':protocol['sampling_rate_hz'],'acceleration_unit':'m/s2',
        'band_low_hz':protocol['carrier_band_hz'][0],'band_high_hz':protocol['carrier_band_hz'][1],
        'modulation_low_hz':protocol['modulation_range_hz'][0],'modulation_high_hz':protocol['modulation_range_hz'][1],
        'selection_reason':'Frozen research shape protocol; numeric proxy is not SI acceleration'}
    result=analyze_envelope(x,params)
    summary={'status':result.status,'reason':result.error_code,'proxy_unit':'1','physical_metrics':None,
        'crest_factor':basic.summary['metrics']['crest_factor'],
        'pearson_kurtosis':basic.summary['metrics']['pearson_kurtosis'],
        'numeric_configuration_sha256':result.summary['numeric_configuration_sha256'],
        'prediction':None,'diagnostic_evidence_eligible':False}
    if result.status=='ok':
        f=result.arrays['frequency_hz'];a=result.arrays['amplitude_m_s2'];inside=(f>=10)&(f<=500)
        selected=a[inside];total=float(selected.sum());maximum=float(selected.max())
        summary.update({'bin_spacing_hz':result.summary['bin_spacing_hz'],
            'effective_count':result.summary['effective_sample_count'],
            'dominant_modulation_hz_under_assumption':float(f[inside][int(np.argmax(selected))]) if maximum else None,
            'dominant_bin_fraction':maximum/total if total else None})
    return summary,result


def inspect_research_record(record,data_root,protocol):
    # This gate precedes all path resolution and signal reads.
    if record.split not in {'development','validation'}:
        raise ValueError('sealed-test signals cannot enter research adaptation')
    if record.dataset!='paderborn' or record.channel!='vibration_1':
        raise ValueError('research adapter only supports the frozen Paderborn vibration channel')
    values=verified_signal(record,data_root)
    _,metadata=load_channel(data_root/record.relative_path,record.dataset,record.channel)
    base={'record_id':record.record_id,'split':record.split,'entity_group':record.entity_group,
        'original_file_sha256':record.file_sha256,'label_for_inventory_only':record.label,
        'physical_input_ready':False,'diagnostic_evidence_eligible':False,'prediction':None,
        'matching_status':'unavailable','matching_reasons':['stored_unit_and_time_semantics_unverified',
            'sourced_speed_and_bearing_uncertainty_required','diagnostic_profile_not_qualified']}
    if record.sampling_rate_hz!=protocol['sampling_rate_hz'] or metadata['time_axis'] is None:
        return base|{'adaptation_status':'unavailable','adaptation':{'reason':'SOURCE_RATE_OR_TIME_AXIS_UNSUPPORTED'}}
    adapted=adapt_stored_axis(values,metadata['time_axis'],protocol,assumption_acknowledged=True)
    report=base|{'adaptation_status':adapted.status,'adaptation':adapted.summary}
    if adapted.status!='ok':return report
    features,_=shape_features(adapted.values,protocol)
    peak=float(np.max(np.abs(values)));proxy=values/peak if peak else np.zeros_like(values)
    counterfactual,_=shape_features(proxy,protocol)
    sensitivity={'status':'unavailable','reason':'successful_nonzero_envelope_required'}
    a=features.get('dominant_modulation_hz_under_assumption');b=counterfactual.get('dominant_modulation_hz_under_assumption')
    if features['status']==counterfactual['status']=='ok' and a is not None and b is not None:
        threshold=2*max(features['bin_spacing_hz'],counterfactual['bin_spacing_hz'])
        sensitivity={'status':'observed','dominant_frequency_difference_hz':a-b,
            'two_bin_threshold_hz':threshold,'timebase_sensitive':abs(a-b)>threshold,
            'interpretation':'Conditional sensitivity, not diagnostic confidence or source-time confirmation'}
    return report|{'stored_axis_hypothesis':features,'counterfactual_nominal_index_axis':counterfactual,
                   'timebase_sensitivity':sensitivity}
