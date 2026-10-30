"""Synthetic truth checks for the frozen research interpolation candidate; no public signals."""
from hashlib import sha256
import json
import platform

import numpy as np
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.evaluation.time_adaptation import adapt_stored_axis,shape_features

PROTOCOL_PATH=PROJECT_ROOT/'datasets/public-data-v2/research-adaptation-protocol.json'


def timing_pattern(count,fs,pattern):
    k=np.arange(count-1,dtype=float)
    steps=1+.015*np.sin(k*.713)
    if pattern=='short_intervals':
        steps[::100]=.25
    elif pattern!='bounded_jitter':raise ValueError('unknown synthetic timing pattern')
    return np.r_[0.,np.cumsum(steps)]/fs


def relative_rms_error(actual,truth):
    return float(np.sqrt(np.mean((actual-truth)**2))/np.sqrt(np.mean(truth**2)))


def main():
    protocol=json.loads(PROTOCOL_PATH.read_text(encoding='utf8'))
    fs=protocol['sampling_rate_hz'];requirements=protocol['synthetic_acceptance']
    count=int(fs*requirements['duration_s'])+1;cases=[]
    for pattern in requirements['timing_patterns']:
        axis=timing_pattern(count,fs,pattern)
        for carrier in requirements['carriers_hz']:
            for modulation in requirements['modulation_hz']:
                def signal(t):return (1+requirements['modulation_depth']*np.cos(2*np.pi*modulation*t))*np.cos(2*np.pi*carrier*t)
                values=signal(axis);before=values.copy();adapted=adapt_stored_axis(values,axis,protocol,assumption_acknowledged=True)
                assert adapted.status=='ok'
                divisor=np.max(np.abs(values));truth=signal(adapted.relative_time_s)/divisor
                wave_error=relative_rms_error(adapted.values,truth)
                features,result=shape_features(adapted.values,protocol)
                truth_features,truth_result=shape_features(truth,protocol)
                assert result.status==truth_result.status=='ok'
                env_error=relative_rms_error(result.arrays['envelope_m_s2'],truth_result.arrays['envelope_m_s2'])
                frequency_error=abs(features['dominant_modulation_hz_under_assumption']-modulation)
                bins=frequency_error/features['bin_spacing_hz']
                passed=(wave_error<=requirements['max_waveform_relative_rms_error']
                    and env_error<=requirements['max_envelope_relative_rms_error']
                    and bins<=requirements['max_dominant_frequency_error_bins']
                    and np.array_equal(values,before))
                cases.append({'timing_pattern':pattern,'carrier_hz':carrier,'modulation_hz':modulation,
                    'waveform_relative_rms_error':wave_error,'envelope_relative_rms_error':env_error,
                    'dominant_frequency_error_bins':bins,'passed':passed,
                    'input_samples_sha256':adapted.summary['original_samples_sha256'],
                    'input_axis_sha256':adapted.summary['original_axis_sha256'],
                    'output_proxy_sha256':adapted.summary['output_proxy_sha256']})
    x=np.ones(4096);axis=np.arange(4096)/fs;axis[2000:]+=2/fs
    denied=adapt_stored_axis(x,axis,protocol,assumption_acknowledged=True)
    gap_rejected=denied.status=='unavailable' and denied.summary['reason']=='GAP_EXCEEDS_RESEARCH_LIMIT'
    passed=all(row['passed'] for row in cases) and gap_rejected
    sources=['backend/src/vibradiag/evaluation/time_adaptation.py','backend/src/vibradiag/analysis/envelope.py',
        'backend/src/vibradiag/analysis/basic.py','backend/src/vibradiag/evaluation/datasets.py',
        'backend/scripts/verify_time_adaptation.py','backend/requirements.lock.txt']
    report={'passed':passed,'protocol_sha256':sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
        'source_sha256':{name:sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() for name in sources},
        'python':platform.python_version(),'cases':cases,'large_gap_rejected':gap_rejected,
        'public_signals_read':0,'diagnostic_profile_qualified':False,
        'limitation':'Known smooth synthetic AM truth only; no full-band or unknown-physics qualification'}
    target=PROJECT_ROOT/'experiments/m2-t05b2-synthetic.json'
    target.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':passed,'synthetic_cases':len(cases),
        'max_waveform_relative_rms_error':max(row['waveform_relative_rms_error'] for row in cases),
        'max_envelope_relative_rms_error':max(row['envelope_relative_rms_error'] for row in cases),
        'large_gap_rejected':gap_rejected}))
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
