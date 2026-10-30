"""Bounded numerical observations, never a fault class or authenticated EV record."""
from hashlib import sha256
import json
import math
import numpy as np
from scipy.signal import find_peaks
from vibradiag.analysis.bearing_frequencies import calculate_bearing_frequencies, VERSION as FREQUENCY_VERSION
from vibradiag.analysis.envelope import VERSION as ENVELOPE_VERSION
from vibradiag.domain.tool_results import NumericToolResult, ToolInputError, NumericRangeError
from vibradiag.schemas.matching import MatchingParameters
from vibradiag.schemas.tools import validate_parameters

VERSION = 'frequency-matching-v2'
CONFIG = dict(max_window_relative=.05, amplitude_ratio_min=4., prominence_ratio_min=3.,
              max_candidates=8, min_reference_bins=20, reference_floor_eps_multiplier=64,
              prominence_window_bins=41, max_raw_peaks=64, max_beat_pairs_per_target=8,
              ambiguity_candidates='threshold_passing_peaks')
LIMITATIONS = (
    'Window support is an observation, not a bearing fault diagnosis or probability',
    'No observed support does not establish health or exclude a fault',
    'Shared peaks and harmonics are correlated, not independent acquisitions',
    'Reference median and numerical floor are not physical sensor noise calibration',
    'Pure numerical references require server-side authorization, hash and lineage checks before persistence',
)


def _invalid(message, field='results'):
    raise ToolInputError('MATCHING_INPUT_INVALID', message, field)


def _finite(value, *, positive=False):
    if isinstance(value, (bool, str)) or not isinstance(value, (int,float,np.number)):
        _invalid('Expected finite numerical metadata')
    value=float(value)
    if not math.isfinite(value) or (positive and value<=0):
        _invalid('Expected finite positive metadata' if positive else 'Expected finite metadata')
    return value


def _spectrum(frequency, amplitude, fs, count):
    f=np.asarray(frequency); a=np.asarray(amplitude)
    if f.ndim!=1 or a.ndim!=1 or f.shape!=a.shape or f.dtype.kind not in 'fiu' or a.dtype.kind not in 'fiu':
        _invalid('Expected equally sized real one-dimensional spectral arrays','arrays')
    if len(f)!=count//2+1 or not 1024<=count<=1_000_000:
        _invalid('Spectrum size does not match the declared full segment','arrays')
    if not np.isfinite(f).all() or not np.isfinite(a).all() or np.any(a<0):
        _invalid('Spectrum must be finite with nonnegative amplitudes','arrays')
    delta=fs/count
    expected=np.arange(len(f),dtype=float)*delta
    if not np.isfinite(expected).all() or not np.allclose(f,expected,rtol=1e-12,atol=delta*1e-9):
        _invalid('Frequency axis must be the full unmodified FFT grid','frequency_hz')
    return f,a.astype(float,copy=False),delta


def _scaled_peaks(amplitude):
    peak=float(np.max(amplitude))
    if peak==0:
        return np.zeros_like(amplitude),np.array([],dtype=int),{},0.
    with np.errstate(under='ignore'):
        scaled=amplitude/peak
    if np.any((amplitude!=0)&(scaled==0)):
        raise NumericRangeError()
    bins, properties=find_peaks(scaled,prominence=(None,None),wlen=CONFIG['prominence_window_bins'])
    return scaled,bins,dict(zip(map(int,bins),map(float,properties['prominences']))),peak


def _targets(theory, delta, slip):
    frequencies=theory['frequencies']; rows=[]
    def add(name, harmonic, offset, spacing):
        base=frequencies[name]; center=harmonic*base['frequency_hz']
        uncertainty=harmonic*base['uncertainty_hz']
        if offset:
            center+=offset*spacing['center']
            uncertainty+=abs(offset)*spacing['uncertainty']
        width=2*delta+uncertainty+abs(center)*slip
        if not math.isfinite(center) or not math.isfinite(width):
            raise NumericRangeError()
        suffix=f'{offset:+d}{spacing["label"]}' if offset else ''
        rows.append(dict(target_id=f'{name}:{harmonic}{suffix}', family=name, harmonic=harmonic,
            sideband_order=offset, sideband_spacing=spacing['label'] if offset else None,
            aliases=['2BSF'] if name=='BSF' and harmonic==2 and not offset else [],
            auxiliary_only=name=='FTF', target_hz=center, half_width_hz=width,
            propagated_uncertainty_hz=uncertainty, status='not_evaluable', reasons=[], candidates=[],
            reference=None, overlap_group=None, confounders=[]))
    conditions=theory['parameters']['conditions']
    shaft=dict(center=theory['shaft_frequency_hz'], uncertainty=conditions['rpm_uncertainty_rpm']/60,label='fr')
    cage=dict(center=frequencies['FTF']['frequency_hz'],uncertainty=frequencies['FTF']['uncertainty_hz'],label='FTF')
    for family, maximum in [('BPFO',5),('BPFI',5),('BSF',4),('FTF',1)]:
        for harmonic in range(1,maximum+1):
            add(family,harmonic,0,None)
            if family in {'BPFO','BPFI'}:
                for offset in (-2,-1,1,2): add(family,harmonic,offset,shaft)
            elif family=='BSF' and harmonic in (1,2):
                for offset in (-2,-1,1,2): add(family,harmonic,offset,cage)
    return rows,shaft


def _overlap_groups(rows):
    # Connected intervals, including transitive overlap; deterministic identities.
    eligible=sorted([r for r in rows if not r['reasons']],key=lambda r:(r['target_hz']-r['half_width_hz'],r['target_id']))
    groups=[]; current=[]; right=-math.inf
    for row in eligible:
        low=row['target_hz']-row['half_width_hz']; high=row['target_hz']+row['half_width_hz']
        if current and low>right:
            groups.append(current); current=[]; right=-math.inf
        current.append(row); right=max(right,high)
    if current: groups.append(current)
    for group in groups:
        if len(group)>1:
            identity='overlap:'+sha256('|'.join(sorted(r['target_id'] for r in group)).encode()).hexdigest()[:16]
            for row in group: row['overlap_group']=identity


def _reference(f,scaled,row,exclusions,low,high,delta,global_peak):
    center=row['target_hz']; epsilon=row['half_width_hz']
    width=max(10*delta,4*epsilon)
    left=max(low,center-width); right=min(high,center+width)
    start=int(np.searchsorted(f,left,'left')); stop=int(np.searchsorted(f,right,'right'))
    local=f[start:stop]; mask=np.ones(len(local),dtype=bool)
    for excluded in exclusions:
        guard=max(2*delta,excluded['half_width_hz'])
        mask &= abs(local-excluded['target_hz'])>guard
    count=int(mask.sum())
    if count<CONFIG['min_reference_bins']:
        return None,'noise_reference_insufficient'
    values=scaled[start:stop][mask]
    # Sorting avoids overflow in median's even-length averaging for extreme values.
    ordered=np.sort(values); n=len(ordered)
    reference=float(ordered[n//2]) if n%2 else float(ordered[n//2-1]/2+ordered[n//2]/2)
    applied=reference==0
    if applied: reference=CONFIG['reference_floor_eps_multiplier']*np.finfo(float).eps
    physical=reference*global_peak
    if not math.isfinite(physical) or physical<=0:
        raise NumericRangeError()
    return dict(bin_count=count,range_hz=[float(left),float(right)],median_or_floor_m_s2=physical,
                scaled_value=reference,reference_floor_applied=applied),None


def match_frequency_evidence(envelope, frequencies, parameters, *, basic_spectrum=None):
    """basic_spectrum is internal full-grid {frequency_hz, amplitude_m_s2, sample_count}.

    T03b must verify all parent records/hashes and construct these identities;
    a UUID or this function's numerical status grants no authorization.
    """
    p=validate_parameters(MatchingParameters,parameters)
    if not isinstance(envelope,NumericToolResult) or not isinstance(frequencies,NumericToolResult):
        _invalid('Expected numerical parent outcomes')
    if envelope.tool_name!='analyze_envelope' or envelope.tool_version!=ENVELOPE_VERSION or frequencies.tool_name!='calculate_bearing_frequencies' or frequencies.tool_version!=FREQUENCY_VERSION:
        _invalid('Parent tool or version mismatch')
    sources=[p.envelope_source,p.frequency_source]+([p.basic_source] if p.basic_source else [])
    if len({(s.measurement_revision_id,s.context_id,s.input_sha256) for s in sources})!=1:
        _invalid('Parents must have identical input revision, context and original hash','sources')
    if len({s.execution_id for s in sources})!=len(sources):
        _invalid('Distinct tools must reference distinct executions','sources')
    if (basic_spectrum is None)!=(p.basic_source is None):
        _invalid('Basic spectrum and its source must be provided together','basic_source')
    missing=[]
    if envelope.status!='ok': missing.append('successful_envelope_parent')
    if frequencies.status!='ok': missing.append('successful_frequency_parent')
    if missing:
        return NumericToolResult('match_frequency_evidence',VERSION,'unavailable',missing_requirements=tuple(missing),limitations=LIMITATIONS,error_code='PARENT_NOT_READY')
    try:
        theory=frequencies.summary
        recalculated=calculate_bearing_frequencies(theory['parameters'])
        if recalculated.status!='ok' or json.dumps(recalculated.summary,sort_keys=True,allow_nan=False)!=json.dumps(theory,sort_keys=True,allow_nan=False):
            _invalid('Theory metadata does not match its parameter snapshot')
        target=theory['parameters']['conditions']['target']
        if target['measurement_revision_id']!=str(p.frequency_source.measurement_revision_id):
            _invalid('Theoretical target references another input revision','sources')
        if not theory['uncertainty_complete']: missing.append('complete_frequency_and_speed_uncertainty')
        if p.execution_mode=='diagnostic_evidence':
            if not envelope.summary.get('evidence_preconditions_satisfied'): missing.append('qualified_envelope_parent')
            if basic_spectrum is None: missing.append('same_input_basic_spectrum')
        if missing:
            return NumericToolResult('match_frequency_evidence',VERSION,'unavailable',missing_requirements=tuple(missing),limitations=LIMITATIONS,error_code='MATCHING_PRECONDITION_MISSING')
        summary=envelope.summary
        fs=_finite(summary['sampling_rate_hz'],positive=True)
        count=summary['effective_segment']['sample_count']
        original_count=summary['input_sample_count']
        if type(count)!=int or type(original_count)!=int or not 1024<=count<=original_count<=1_000_000:
            _invalid('Invalid original/effective segment lengths')
        segment=summary['effective_segment']
        if type(segment['start_index'])!=int or type(segment['stop_index_exclusive'])!=int or not 0<=segment['start_index']<segment['stop_index_exclusive']<=original_count or segment['stop_index_exclusive']-segment['start_index']!=count:
            _invalid('Effective segment identity is inconsistent')
        if summary['output_unit']!='m/s2': _invalid('Only explicit m/s2 amplitudes are supported','unit')
        f,a,delta=_spectrum(envelope.arrays['frequency_hz'],envelope.arrays['amplitude_m_s2'],fs,count)
        low,high=map(_finite,summary['configuration']['modulation_range_hz'])
        band_low,band_high=map(_finite,summary['configuration']['band_hz'])
        if not 0<low<high<fs/2 or not 0<band_low<band_high<fs/2 or high>(band_high-band_low)/2:
            _invalid('Invalid envelope observation or carrier band')
        if summary['configuration']['sampling_rate_hz']!=fs or not math.isclose(summary['bin_spacing_hz'],delta,rel_tol=1e-12):
            _invalid('Spectrum resolution metadata mismatch')
        scaled,peaks,prominences,global_peak=_scaled_peaks(a)
        rows,shaft=_targets(theory,delta,p.slip_allowance_relative)
        for row in rows:
            center=row['target_hz']; width=row['half_width_hz']
            if center<=0: row['reasons'].append('nonpositive_target')
            elif width>CONFIG['max_window_relative']*center: row['reasons'].append('resolution_limited')
            elif center-width<low or center+width>high: row['reasons'].append('window_outside_observation_range')
        _overlap_groups(rows)
        shaft_windows=[dict(target_hz=h*shaft['center'],half_width_hz=2*delta+h*shaft['uncertainty']+h*shaft['center']*p.slip_allowance_relative)
            for h in range(1,11)]
        # Even unevaluable/partially out-of-range targets can contaminate a
        # neighboring background. Do not silently treat their windows as noise.
        exclusions=[r for r in rows if r['target_hz']>0]+shaft_windows
        # Bounded raw carrier screening; clipping a peak list makes the check incomplete.
        raw=[]; raw_truncated=False
        if basic_spectrum is not None:
            if basic_spectrum['sample_count']!=original_count:
                _invalid('Basic spectrum must describe the full original acquisition','basic_spectrum')
            bf,ba,bd=_spectrum(basic_spectrum['frequency_hz'],basic_spectrum['amplitude_m_s2'],fs,basic_spectrum['sample_count'])
            bs,bins,bp,bmax=_scaled_peaks(ba)
            in_band=[int(i) for i in bins if band_low<=bf[i]<=band_high and bs[i]>=.01 and bp[int(i)]>=.005]
            in_band.sort(key=lambda i:(-bs[i],i)); raw_truncated=len(in_band)>CONFIG['max_raw_peaks']
            raw=[dict(bin=i,frequency_hz=float(bf[i]),relative_amplitude=float(bs[i])) for i in in_band[:CONFIG['max_raw_peaks']]]
        else: bd=None
        for row in rows:
            if row['reasons']: continue
            if global_peak==0:
                row['reasons'].append('no_dynamic_information'); continue
            reference,error=_reference(f,scaled,row,exclusions,low,high,delta,global_peak)
            if error:
                row['reasons'].append(error); continue
            row['reference']={k:v for k,v in reference.items() if k!='scaled_value'}
            center=row['target_hz']; width=row['half_width_hz']; ref=reference['scaled_value']
            candidates=[int(i) for i in peaks if low<=f[i]<=high and abs(f[i]-center)<=width]
            candidates.sort(key=lambda i:(-scaled[i],abs(float(f[i])-center),i))
            row['candidate_count']=len(candidates)
            for i in candidates[:CONFIG['max_candidates']]:
                amplitude_ratio=float(scaled[i]/ref); prominence_ratio=float(prominences[i]/ref)
                if not math.isfinite(amplitude_ratio) or not math.isfinite(prominence_ratio): raise NumericRangeError()
                row['candidates'].append(dict(peak_id=f'{p.envelope_source.execution_id}:bin:{i}',bin=i,
                    frequency_hz=float(f[i]),amplitude_m_s2=float(a[i]),frequency_error_hz=float(f[i]-center),
                    prominence_m_s2=float(prominences[i]*global_peak),amplitude_ratio=amplitude_ratio,prominence_ratio=prominence_ratio,
                    passes_thresholds=amplitude_ratio>=4 and prominence_ratio>=3))
            supporting=[c for c in row['candidates'] if c['passes_thresholds']]
            row['status']='observed_support' if supporting else 'no_observed_support'
            if len(supporting)>1:
                row['reasons'].append('multiple_peaks'); row['status']='ambiguous'
            if supporting and row['overlap_group']:
                row['reasons'].append('overlapping_target_windows'); row['status']='ambiguous'
            if supporting:
                for h,window in enumerate(shaft_windows,1):
                    if any(abs(c['frequency_hz']-window['target_hz'])<=window['half_width_hz'] for c in supporting):
                        row['confounders'].append(dict(kind='shaft_harmonic_overlap',harmonic=h,spectra_checked=['envelope']))
                if basic_spectrum is not None:
                    pairs=[]
                    for i,left in enumerate(raw):
                        for right in raw[i+1:]:
                            difference=abs(right['frequency_hz']-left['frequency_hz'])
                            if abs(difference-center)<=width+2*bd:
                                pairs.append(dict(carrier_bins=[left['bin'],right['bin']],difference_hz=difference))
                    if pairs:
                        row['confounders'].append(dict(kind='possible_carrier_beating',pairs=pairs[:8],pair_count=len(pairs)))
                if row['confounders']:
                    row['status']='ambiguous'; row['reasons'].append('alternative_explanations')
        # Raw shaft components also matter even when no significant envelope component exists.
        basic_shaft=[]
        if basic_spectrum is not None:
            for h,window in enumerate(shaft_windows,1):
                observed=[int(i) for i in bins if abs(float(bf[i])-window['target_hz'])<=window['half_width_hz'] and bs[i]>=.01 and bp[int(i)]>=.005]
                basic_shaft.append(dict(harmonic=h,candidate_bins=observed[:8],candidate_count=len(observed)))
        support_ids=sorted({c['peak_id'] for r in rows for c in r['candidates'] if c['passes_thresholds']})
        result=dict(parameters=p.model_dump(mode='json'),configuration=dict(CONFIG),
            frequency_unit='Hz',amplitude_unit='m/s2',bin_spacing_hz=delta,effective_segment=segment,
            rows=rows,unique_supporting_peak_ids=support_ids,root_acquisition_refs=[p.envelope_source.input_sha256],
            evidence_eligible=False,confounder_checks_complete=False,
            confounder_checks=dict(shaft_harmonics='screened' if basic_spectrum is not None else 'envelope_only',
                carrier_beating='bounded_screen' if basic_spectrum is not None else 'not_checked',
                raw_peak_screen_truncated=raw_truncated,raw_peak_relative_threshold=.01,raw_prominence_relative_threshold=.005,
                basic_shaft_observations=basic_shaft,filter_sensitivity='not_checked'),
            independent_acquisition_count=1,diagnosis=None)
        return NumericToolResult('match_frequency_evidence',VERSION,'ok',result,
            missing_requirements=('filter_sensitivity_check','server_registered_evidence')+(() if basic_spectrum is not None else ('same_input_basic_spectrum',)),
            limitations=LIMITATIONS)
    except (KeyError,TypeError,IndexError):
        _invalid('Missing or malformed parent metadata or arrays')
    except (NumericRangeError,OverflowError,FloatingPointError):
        return NumericToolResult('match_frequency_evidence',VERSION,'failed',limitations=LIMITATIONS,error_code='NUMERIC_RANGE_UNSUPPORTED')
