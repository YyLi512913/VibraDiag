"""Offline SOS/Hilbert numerical kernel. No file IO, database, HTTP or model calls."""
from hashlib import sha256
import json
import math
import warnings
import numpy as np
import scipy
from scipy.signal import butter, freqz_sos, hilbert, sosfiltfilt
from scipy.signal.windows import hann

from vibradiag.analysis.csv_input import ParsedSignal, make_preview
from vibradiag.domain.tool_results import NumericRangeError, NumericToolResult, ToolInputError
from vibradiag.schemas.tools import EnvelopeParameters, validate_parameters

VERSION = 'envelope-sos-hilbert-v1'
MAX_GUARD_SAMPLES = 65536
MAX_HILBERT_POINTS = 1200000
LIMITATIONS = ('Envelope observations are not bearing fault classifications',
               'Guard length is a heuristic, not a mathematical Hilbert boundary-error bound',
               'Window correction does not compensate bandpass attenuation',
               'Service adapter must verify provenance, qualification records and current permissions')


def numeric_configuration(parameters):
    p = validate_parameters(EnvelopeParameters, parameters)
    return {'algorithm_version': VERSION, 'scipy_version': scipy.__version__, 'numpy_version': np.__version__,
            'sampling_rate_hz': p.sampling_rate_hz, 'band_hz': [p.band_low_hz, p.band_high_hz],
            'modulation_range_hz': [p.modulation_low_hz, p.modulation_high_hz],
            'prototype_order': 4, 'padtype': 'odd', 'guard_rule': 'max(3P,ceil(2log(1e-6)/log(r)),ceil(fs/low))',
            'max_guard_samples': MAX_GUARD_SAMPLES, 'max_hilbert_points': MAX_HILBERT_POINTS,
            'min_effective_points': 1024, 'min_modulation_cycles': 10,
            'hilbert_extension': 'reflect_G_each_side', 'window': 'periodic_hann',
            'spectrum': 'single_sided_peak', 'g_to_m_s2': 9.80665}


def configuration_sha256(parameters):
    return sha256(json.dumps(numeric_configuration(parameters),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _evidence_gaps(p, fingerprint):
    gaps = []
    if p.acquisition is None:
        gaps.append('verified_acquisition_bandwidth')
    else:
        if not p.acquisition.checked or p.acquisition.source is None:
            gaps.append('acquisition_bandwidth_check_and_source')
        if p.acquisition.low_hz > p.band_low_hz or p.acquisition.high_hz < p.band_high_hz:
            gaps.append('band_inside_effective_acquisition_bandwidth')
        if p.acquisition.high_hz >= p.sampling_rate_hz/2:
            gaps.append('acquisition_bandwidth_below_nyquist')
    if p.qualification is None:
        gaps.append('qualified_numeric_profile')
    elif p.qualification.numeric_configuration_sha256 != fingerprint:
        gaps.append('qualification_matches_exact_numeric_configuration')
    return gaps


def _signal(values):
    try:
        original = np.asarray(values)
        if original.ndim != 1 or original.dtype.kind not in 'fiu' or not 1024 <= original.size <= 1000000:
            raise ValueError()
        with np.errstate(over='raise',invalid='raise'):
            x = original.astype(np.float64,copy=True)
        if not np.isfinite(x).all(): raise ValueError()
        return x
    except (ValueError, TypeError, FloatingPointError, OverflowError):
        raise ToolInputError('SIGNAL_INVALID','A finite one-dimensional numeric signal of 1024–1000000 points is required','values') from None


def _physical(values, scale):
    with np.errstate(over='ignore',under='ignore',invalid='ignore'):
        result = values*scale
    if not np.isfinite(result).all() or np.any((values != 0) & (result == 0)):
        raise NumericRangeError()
    return result


def _amplitude_spectrum(envelope, fs):
    """Validated envelope -> periodic-Hann, coherent-gain single-sided peak spectrum."""
    count = len(envelope)
    window = hann(count,sym=False)
    amplitude = np.abs(np.fft.rfft((envelope-envelope.mean())*window))/window.sum()
    amplitude[1:-1 if count%2==0 else None] *= 2
    return np.arange(count//2+1,dtype=np.float64)*(fs/count), amplitude


def analyze_envelope(values, parameters) -> NumericToolResult:
    p = validate_parameters(EnvelopeParameters,parameters)
    x = _signal(values); n = len(x); fs = p.sampling_rate_hz
    fingerprint = configuration_sha256(p)
    summary = {'input_sample_count': n, 'sampling_rate_hz': fs, 'input_unit': p.acceleration_unit,
               'output_unit': 'm/s2', 'execution_mode': p.execution_mode,
               'numeric_configuration_sha256': fingerprint, 'configuration': numeric_configuration(p),
               'parameters': p.model_dump(mode='json'), 'evidence_preconditions_satisfied': False}
    evidence_gaps = _evidence_gaps(p,fingerprint)
    limitations = list(LIMITATIONS)
    if p.execution_mode == 'exploratory':
        limitations.append('Exploratory calculation; not approved as diagnostic evidence')
    elif evidence_gaps:
        return NumericToolResult('analyze_envelope',VERSION,'unavailable',summary,
            missing_requirements=tuple(evidence_gaps),limitations=tuple(limitations),error_code='EVIDENCE_PRECONDITION_MISSING')
    try:
        if not math.isfinite(n/fs) or n/fs <= 0 or not math.isfinite(1/fs) or 1/fs <= 0:
            raise NumericRangeError()
        with warnings.catch_warnings():
            warnings.simplefilter('error',RuntimeWarning)
            sos = butter(4,[p.band_low_hz,p.band_high_hz],btype='bandpass',output='sos',fs=fs)
        if not np.isfinite(sos).all(): raise NumericRangeError()
        padlen = 3*(2*len(sos)+1-min(int((sos[:,2]==0).sum()),int((sos[:,5]==0).sum())))
        radius = float(max(abs(root) for row in sos for root in np.roots(row[3:])))
        if not 0 < radius < 1:
            return NumericToolResult('analyze_envelope',VERSION,'failed',summary,
                limitations=tuple(limitations),error_code='FILTER_DESIGN_UNSTABLE')
        guard = max(3*padlen,math.ceil(2*math.log(1e-6)/math.log(radius)),math.ceil(fs/p.band_low_hz))
        effective = n-2*guard
        summary.update({'padlen': padlen,'max_pole_radius': radius,'guard_samples_each_side': guard,
                        'hilbert_points': n+2*guard, 'effective_sample_count': max(0,effective)})
        if guard > MAX_GUARD_SAMPLES or n+2*guard > MAX_HILBERT_POINTS:
            return NumericToolResult('analyze_envelope',VERSION,'unavailable',summary,
                limitations=tuple(limitations),error_code='ENVELOPE_RESOURCE_LIMIT')
        if n < padlen+2 or effective < 1024:
            return NumericToolResult('analyze_envelope',VERSION,'unavailable',summary,
                limitations=tuple(limitations),error_code='ENVELOPE_LENGTH_INSUFFICIENT')
        if (effective/fs)*p.modulation_low_hz < 10:
            return NumericToolResult('analyze_envelope',VERSION,'unavailable',summary,
                limitations=tuple(limitations),error_code='MODULATION_OBSERVATION_TOO_SHORT')
        peak = float(np.max(np.abs(x)))
        scale = peak*(9.80665 if p.acceleration_unit=='g' else 1.)
        if not math.isfinite(scale): raise NumericRangeError()
        with np.errstate(under='ignore'):
            normalized = x/peak if peak else np.zeros_like(x)
        if np.any((x!=0)&(normalized==0)): raise NumericRangeError()
        if x.min()!=x.max() and normalized.min()==normalized.max(): raise NumericRangeError()
        with np.errstate(over='raise',invalid='raise',divide='raise',under='ignore'):
            filtered = sosfiltfilt(sos,normalized-normalized.mean(),padtype='odd',padlen=padlen)
            padded = np.pad(filtered,(guard,guard),mode='reflect')
            full_envelope = np.abs(hilbert(padded,N=len(padded)))[guard:guard+n]
            retained = full_envelope[guard:n-guard]
            frequencies,amplitude = _amplitude_spectrum(retained,fs)
            response_frequency,response = freqz_sos(sos,worN=np.linspace(0.,fs/2,2049),fs=fs)
            _,edge_response = freqz_sos(sos,worN=np.array([p.band_low_hz,p.band_high_hz]),fs=fs)
        physical_filtered = _physical(filtered,scale)
        physical_envelope = _physical(retained,scale)
        physical_amplitude = _physical(amplitude,scale)
        if not np.isfinite(frequencies).all() or not np.isfinite(response).all() or not np.isfinite(edge_response).all(): raise NumericRangeError()
        preview = make_preview(ParsedSignal(physical_envelope),fs)
        preview['original_indices'] = [guard+index for index in preview['indices']]
        preview['time_s'] = [index/fs for index in preview['original_indices']]
        preview['unit'] = 'm/s2'
        del preview['last_sample_time_s']  # Full segment times are defined explicitly below.
        segment = {'start_index': guard,'stop_index_exclusive': n-guard,'sample_count': effective,
                   'start_time_s': guard/fs,'last_sample_time_s': (n-guard-1)/fs,'duration_s': effective/fs}
        summary.update({'original_record_duration_s': n/fs,'effective_segment': segment,'bin_spacing_hz': fs/effective,
                        'dynamic_information_available': bool(x.min()!=x.max()),
                        'prototype_order':4,'single_pass_order':8,'sos_sections':len(sos),'sos':sos.tolist(),
                        'forward_backward_response':'abs(H)**2','response_grid_is_filter_design_evaluation':True,
                        'response_at_band_edges_forward_backward_gain':(np.abs(edge_response)**2).tolist(),
                        'envelope_preview':preview,'evidence_preconditions_satisfied':p.execution_mode=='diagnostic_evidence'})
        if peak==0: limitations.append('Zero input: no dynamic information; not a health assessment')
        elif x.min()==x.max(): limitations.append('Constant input: no dynamic information; not a health assessment')
        if p.acquisition and p.acquisition.source and p.acquisition.source.kind=='synthetic_exact':
            limitations.append('Synthetic acquisition statement is not field calibration')
        arrays = {'filtered_m_s2':physical_filtered,'envelope_m_s2':physical_envelope,
                  'frequency_hz':frequencies,'amplitude_m_s2':physical_amplitude,
                  'response_frequency_hz':response_frequency,'response_gain_single_pass':np.abs(response),
                  'response_gain_forward_backward':np.abs(response)**2}
        for array in arrays.values(): array.setflags(write=False)
        return NumericToolResult('analyze_envelope',VERSION,'ok',summary,arrays,
                                 missing_requirements=tuple(evidence_gaps),limitations=tuple(limitations))
    except (NumericRangeError, FloatingPointError, OverflowError, ZeroDivisionError, RuntimeWarning):
        return NumericToolResult('analyze_envelope',VERSION,'failed',summary,
                                 limitations=tuple(limitations),error_code='NUMERIC_RANGE_UNSUPPORTED')
    except (ValueError,np.linalg.LinAlgError):
        return NumericToolResult('analyze_envelope',VERSION,'failed',summary,
                                 limitations=tuple(limitations),error_code='FILTER_DESIGN_FAILED')
