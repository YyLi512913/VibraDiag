"""Frozen development-only shape baseline; no fault classifier or evidence promotion."""
import math
import numpy as np
from vibradiag.analysis.basic import compute_basic
from vibradiag.analysis.envelope import analyze_envelope

PROTOCOL = {
    'version': 'public-shape-development-v1',
    'selection': 'full original drive-end/vibration_1 channel; no resampling or window splitting',
    'carrier_band_hz': [2000., 4000.],
    'modulation_range_hz': [10., 500.],
    'band_reason': 'Fixed before this public-data run from existing T02a synthetic baseline; not label optimized',
    'scale': 'divide by original absolute peak; report dimensionless shape only',
    'time_axis_relative_tolerance': .001,
    'prediction': None,
    'diagnostic_evidence_eligible': False,
}


def shape_baseline(record, values, metadata):
    if record.split != 'development':
        raise ValueError('this development runner cannot inspect validation or sealed test signals')
    peak = float(np.max(np.abs(values)))
    normalized = values / peak if peak else np.zeros_like(values)
    if np.any((values != 0) & (normalized == 0)):
        raise ValueError('unit-peak normalization underflows float64')
    # Numeric proxy only: the API's physical metrics and units are never exported here.
    basic = compute_basic(normalized, record.sampling_rate_hz or 1., 'm/s2')
    metrics = basic.summary['metrics']
    report = {'record_id': record.record_id, 'label_for_inventory_only': record.label,
              'input_count': len(values), 'crest_factor': metrics['crest_factor'],
              'pearson_kurtosis': metrics['pearson_kurtosis'],
              'physical_metrics': None, 'physical_metrics_reason': 'SHAPE_ONLY_PROTOCOL',
              'unit_verified': record.unit is not None,
              'prediction': None, 'diagnostic_evidence_eligible': False}
    fs = record.sampling_rate_hz
    if fs is None:
        report['envelope'] = {'status': 'unavailable', 'reason': 'SAMPLING_RATE_NOT_VERIFIED'}
        return report
    axis = metadata['time_axis']
    if axis is not None:
        duration = float(axis[-1]-axis[0])
        expected = (len(values)-1)/fs
        duration_error = abs(duration-expected)/expected
        dt = np.diff(axis)
        max_step_error = float(np.max(np.abs(dt-1/fs))*fs)
        report['time_axis_check'] = {'record_duration_s': duration,
            'nominal_duration_s': expected, 'duration_relative_error': duration_error,
            'max_step_relative_error': max_step_error,
            'tolerance': PROTOCOL['time_axis_relative_tolerance']}
        if max(duration_error, max_step_error) > PROTOCOL['time_axis_relative_tolerance']:
            report['envelope'] = {'status': 'unavailable', 'reason': 'TIME_AXIS_DIFFERS_FROM_NOMINAL_RATE'}
            return report
    parameters = {'sampling_rate_hz': fs, 'acceleration_unit': 'm/s2',
        'band_low_hz': 2000., 'band_high_hz': 4000., 'modulation_low_hz': 10., 'modulation_high_hz': 500.,
        'selection_reason': PROTOCOL['band_reason']}
    result = analyze_envelope(normalized, parameters)
    row = {'status': result.status, 'configuration_sha256': result.summary['numeric_configuration_sha256'],
           'reason': result.error_code, 'numeric_input': 'unit-peak proxy; not SI acceleration',
           'evidence_preconditions_satisfied': False}
    if result.status == 'ok':
        frequencies = result.arrays['frequency_hz']; amplitude = result.arrays['amplitude_m_s2']
        inside = (frequencies >= 10) & (frequencies <= 500)
        selected = amplitude[inside]
        maximum = float(np.max(selected)); index = int(np.argmax(selected))
        row.update({'dominant_modulation_hz': float(frequencies[inside][index]),
                    'dominant_bin_fraction': maximum / float(selected.sum()) if selected.sum() else None,
                    'bin_spacing_hz': result.summary['bin_spacing_hz'],
                    'effective_count': result.summary['effective_sample_count']})
        if not math.isfinite(row['dominant_modulation_hz']):
            raise ValueError('nonfinite baseline output')
    report['envelope'] = row
    return report
