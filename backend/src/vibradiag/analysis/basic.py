"""Deterministic basic analysis, separate from the experimental Agent adapter."""
from dataclasses import dataclass
import math
import numpy as np
from scipy.signal.windows import hann

from vibradiag.analysis.csv_input import ParsedSignal, make_preview, validate_time_axis
from vibradiag.domain.measurement_errors import MeasurementError

ALGORITHM_VERSION='basic-scaled-v1'
CONFIG={'profile':'basic-v1','segment':'full_file','window':'periodic_hann',
        'demean_for_spectrum':True,'amplitude':'single_sided_peak','g_to_m_s2':9.80665,
        'kurtosis':'pearson_population','algorithm_version':ALGORITHM_VERSION}


@dataclass(frozen=True)
class BasicResult:
    summary: dict
    waveform: dict
    frequency: np.ndarray
    amplitude: np.ndarray


def unsupported():
    raise MeasurementError('NUMERIC_RANGE_UNSUPPORTED','Input or derived values exceed supported float64 range')


def metric(normalized,scale=1.,unit='m/s2',reason=None):
    value=None if reason else float(normalized)*scale
    if value is not None and (not math.isfinite(value) or value==0 and normalized!=0):
        value=None;reason='NUMERIC_RANGE_UNSUPPORTED'
    return {'value':value,'unit':unit,'status':'unavailable' if reason else 'available','reason':reason}


def compute_basic(values,sampling_rate,unit):
    x=np.asarray(values,dtype=np.float64)
    if x.ndim!=1 or not 1024<=len(x)<=1_000_000 or not np.isfinite(x).all() or unit not in {'g','m/s2'}:
        raise MeasurementError('INPUT_INVALID','Validated one-dimensional acceleration input required')
    if not math.isfinite(sampling_rate) or sampling_rate<=0:
        raise MeasurementError('INPUT_INVALID','Finite positive sampling rate required')
    n=len(x);duration=validate_time_axis(n,sampling_rate)
    raw_peak=float(np.max(np.abs(x)));factor=9.80665 if unit=='g' else 1.
    scale=raw_peak*factor
    if not math.isfinite(scale):
        unsupported()
    normalized=x/raw_peak if raw_peak else np.zeros_like(x)
    if np.any((x!=0)&(normalized==0)):
        unsupported()
    mean=float(np.mean(normalized));ac=normalized-mean
    rms=math.sqrt(float(np.mean(normalized*normalized)))
    ac_peak=float(np.max(np.abs(ac)))
    centered=ac/ac_peak if ac_peak else np.zeros_like(ac)
    variance=float(np.mean(centered*centered))
    ac_rms=ac_peak*math.sqrt(variance)
    metrics={
        'mean':metric(mean,scale),'rms':metric(rms,scale),'ac_rms':metric(ac_rms,scale),
        'absolute_peak':metric(1. if raw_peak else 0.,scale),
        'peak_to_peak':metric(float(normalized.max()-normalized.min()),scale),
        'crest_factor':metric(1./rms if rms else 0.,unit='1',reason=None if rms else 'ZERO_RMS'),
        'pearson_kurtosis':metric(float(np.mean(centered**4))/variance**2 if variance else 0.,
                                  unit='1',reason=None if variance else 'ZERO_VARIANCE')}
    window=hann(n,sym=False)
    amplitude=np.abs(np.fft.rfft(ac*window))/float(window.sum())
    amplitude[1:-1 if n%2==0 else None]*=2
    with np.errstate(over='ignore',under='ignore',invalid='ignore'):
        amplitude=amplitude*scale
        converted=x*factor
    frequency=np.arange(n//2+1,dtype=np.float64)*(sampling_rate/n)
    if not np.isfinite(amplitude).all() or not np.isfinite(converted).all() or not np.isfinite(frequency).all():
        unsupported()
    warnings=['Basic analysis only; no bearing fault classification or severity assessment']
    if not ac_peak:
        warnings.append('Constant signal: no dynamic information; kurtosis unavailable')
    if not raw_peak:
        warnings.append('Zero signal: crest factor unavailable')
    if any(item['reason']=='NUMERIC_RANGE_UNSUPPORTED' for item in metrics.values()):
        warnings.append('One or more derived metrics cannot be represented in float64')
    summary={'sample_count':n,'sampling_rate_hz':sampling_rate,'input_unit':unit,'output_unit':'m/s2',
             'duration_s':duration,'last_sample_time_s':(n-1)/sampling_rate,'bin_spacing_hz':sampling_rate/n,
             'metrics':metrics,'warnings':warnings,'processing':dict(CONFIG)}
    waveform=dict(make_preview(ParsedSignal(converted),sampling_rate),unit='m/s2')
    return BasicResult(summary,waveform,frequency,amplitude)
