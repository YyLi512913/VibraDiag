"""Deterministic M2 release baselines, independent of a language model or factory claims."""
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5
import numpy as np

from vibradiag.analysis.basic import compute_basic
from vibradiag.analysis.envelope import analyze_envelope
from vibradiag.analysis.bearing_frequencies import calculate_bearing_frequencies
from vibradiag.analysis.matching import match_frequency_evidence

SOURCE={'kind':'synthetic_exact','reference':'M2 acceptance analytic fixture; no real bearing or calibration', 'version':'1'}
BANDS=((2000.,4000.),(1800.,4200.),(2200.,3800.))


def identifier(name):return str(uuid5(NAMESPACE_URL,'vibradiag-m2-acceptance/'+name))


def synthetic_observation(case,band=(2000.,4000.)):
    count=48322;fs=12000.;t=np.arange(count)/fs
    carrier=2*np.cos(2*np.pi*3000*t)
    if case=='AM':x=2*(1+.25*np.cos(2*np.pi*95*t))*np.cos(2*np.pi*3000*t)
    elif case=='unmodulated':x=carrier
    elif case=='beating':x=carrier+.2*np.cos(2*np.pi*3095*t)
    elif case=='out_of_band':x=carrier+.5*np.cos(2*np.pi*5000*t)
    elif case=='weak_in_noise':
        x=2*(1+1e-5*np.cos(2*np.pi*95*t))*np.cos(2*np.pi*3000*t)+.1*np.random.default_rng(20261030).standard_normal(count)
    elif case=='boundary_impulse':
        x=2*(1+.25*np.cos(2*np.pi*95*t))*np.cos(2*np.pi*3000*t);x=x.copy();x[0]+=100.;x[-1]-=100.
    else:raise ValueError('unknown fixed synthetic case')
    original=sha256(x.astype('<f8').tobytes()).hexdigest()
    revision=identifier(case+'/revision');context=identifier(case+'/context')
    target={'measurement_revision_id':revision,'component_id':identifier(case+'/component'),'bearing_revision_id':identifier(case+'/bearing')}
    theory=calculate_bearing_frequencies({'parameterization':'coefficients',
        'coefficients':{'values':{'FTF':13/30,'BPFO':3.17,'BPFI':5.83,'BSF':2.17},'unit':'shaft_order','bsf_definition':'spin',
            'rotation_assumption':'inner_ring_rotates_outer_fixed','applicability':'Exact mathematical fixture only','source':SOURCE,'checked':True,
            'uncertainty':{'FTF':0.,'BPFO':0.,'BPFI':0.,'BSF':0.,'source':SOURCE}},
        'conditions':{'target':target,'rpm':1800.,'rpm_basis':'synthetic_exact','rpm_source':SOURCE,'rpm_checked':True,
            'rpm_uncertainty_rpm':0.,'rpm_uncertainty_source':SOURCE,'speed_condition':'constant_steady_checked',
            'rotation':'inner_ring_rotates_outer_fixed'}})
    envelope=analyze_envelope(x,{'sampling_rate_hz':fs,'acceleration_unit':'m/s2','band_low_hz':band[0],'band_high_hz':band[1],
        'modulation_low_hz':10.,'modulation_high_hz':500.,'selection_reason':'Fixed M2 synthetic release/sensitivity baseline; no label tuning'})
    basic=compute_basic(x,fs,'m/s2')
    parent={'measurement_revision_id':revision,'context_id':context,'input_sha256':original}
    params={name:parent|{'execution_id':identifier(case+'/'+name)} for name in ('envelope_source','frequency_source','basic_source')}
    result=match_frequency_evidence(envelope,theory,params,basic_spectrum={'frequency_hz':basic.frequency,
        'amplitude_m_s2':basic.amplitude,'sample_count':count})
    if result.status!='ok':raise AssertionError('fixed synthetic pipeline did not complete')
    row=next(row for row in result.summary['rows'] if row['target_id']=='BPFO:1')
    f=envelope.arrays['frequency_hz'];a=envelope.arrays['amplitude_m_s2'];inside=(f>=10)&(f<=500)
    selected=a[inside];peak=int(np.argmax(selected))
    summary={'case':case,'band_hz':list(band),'numeric_configuration_sha256':envelope.summary['numeric_configuration_sha256'],
        'original_sha256':original,'original_unchanged':original==sha256(x.astype('<f8').tobytes()).hexdigest(),
        'target_id':row['target_id'],'target_status':row['status'],'target_reasons':row['reasons'],
        'confounders':[item['kind'] for item in row['confounders']],
        'candidate_count':row['candidate_count'],'row_count':len(result.summary['rows']),
        'observation_status_counts':{status:sum(r['status']==status for r in result.summary['rows']) for status in
            ('observed_support','no_observed_support','ambiguous','not_evaluable')},
        'guard_samples_each_side':envelope.summary['guard_samples_each_side'],
        'effective_segment':envelope.summary['effective_segment'],
        'maximum_modulation_amplitude_m_s2':float(selected[peak]),'maximum_modulation_frequency_hz':float(f[inside][peak]),
        'original_absolute_peak_m_s2':basic.summary['metrics']['absolute_peak']['value'],
        'diagnosis':result.summary['diagnosis'],'evidence_eligible':result.summary['evidence_eligible'],
        'independent_acquisition_count':result.summary['independent_acquisition_count'],
        'filter_sensitivity_in_registered_result':result.summary['confounder_checks']['filter_sensitivity']}
    return summary


def public_applicability(record):
    """Inventory decision only: do not invent units/rpm/context to force a tool run."""
    gaps=[]
    if record.unit is None:gaps.append('stored_acceleration_unit_not_established')
    if record.sampling_rate_hz is None:gaps.append('sampling_rate_not_established')
    if record.dataset=='paderborn':gaps.append('time_axis_semantics_not_established')
    gaps.extend(('verified_current_speed_and_target_context','sourced_parameter_uncertainty','qualified_diagnostic_profile'))
    return {'record_id':record.record_id,'dataset':record.dataset,'split':record.split,'label':record.label,
        'entity_group':record.entity_group,'decision':'unavailable','missing_requirements':gaps,
        'tool_executed':False,'decision_scope':'strict matching applicability audit, not a persisted execution',
        'observed_support':False,'diagnosis':None,'physical_metrics':None}
