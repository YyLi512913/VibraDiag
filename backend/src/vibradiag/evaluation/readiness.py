"""Source/timebase readiness checks; never repair metadata or publish a diagnosis."""
import numpy as np
from vibradiag.evaluation.datasets import load_channel, verified_signal


def check_validation_record(record, data_root, protocol):
    if record.split != 'validation':
        raise ValueError('validation reader cannot open development or sealed-test signals')
    # The split check precedes every filesystem or MAT operation.
    values = verified_signal(record,data_root)
    _,metadata = load_channel(data_root/record.relative_path,record.dataset,record.channel)
    reasons = []
    if record.unit is None: reasons.append('STORED_ACCELERATION_UNIT_NOT_ESTABLISHED')
    fs = record.sampling_rate_hz
    if fs is None: reasons.append('SAMPLING_RATE_NOT_ESTABLISHED')
    elif fs != protocol['sampling_rate_hz']:
        reasons.append('SAMPLING_RATE_DIFFERS_FROM_PROTOCOL')
    axis = metadata['time_axis']
    timing = None
    if axis is None:
        reasons.append('ORIGINAL_TIME_AXIS_NOT_AVAILABLE')
    elif fs is not None:
        dt = np.diff(axis)*fs
        expected = (len(values)-1)/fs
        duration = float(axis[-1]-axis[0])
        timing = {'sample_count':len(values),'record_duration_s':duration,
            'min_step_nominal':float(dt.min()),'max_step_nominal':float(dt.max()),
            'max_step_relative_error':float(np.max(np.abs(dt-1))),
            'duration_relative_error':abs(duration-expected)/expected,
            'gaps_over_two_nominal_intervals':int((dt>2).sum()),
            'tolerance':protocol['time_axis_relative_tolerance']}
        if max(timing['max_step_relative_error'],timing['duration_relative_error']) > timing['tolerance']:
            reasons.append('ORIGINAL_TIME_AXIS_NOT_UNIFORM_AT_NOMINAL_RATE')
    return {'record_id':record.record_id,'entity_group':record.entity_group,'label':record.label,
        'input_ready':not reasons,'blocking_reasons':reasons,'time_axis_check':timing,
        'resampled':False,'unit_overridden':False,'features_computed':False,'prediction':None,
        'diagnostic_evidence_eligible':False,
        'remaining_diagnostic_requirements':['sourced_rpm_and_geometry_uncertainty',
            'numeric_profile_qualification','verified_input_context_and_evidence_registration']}
