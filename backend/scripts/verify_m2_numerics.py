"""Reproduce standalone M2 numerical checks; no API, database, published presets or LLM."""
from hashlib import sha256
import json
from pathlib import Path
import platform
import time

import numpy as np
import scipy

from vibradiag.analysis.bearing_frequencies import calculate_bearing_frequencies
from vibradiag.analysis.envelope import analyze_envelope
from vibradiag.core.paths import PROJECT_ROOT

BASE={'sampling_rate_hz':12000,'acceleration_unit':'m/s2','band_low_hz':2000,'band_high_hz':4000,
      'modulation_low_hz':10,'modulation_high_hz':500,'selection_reason':'Synthetic numerical verification, not field calibration'}
SOURCE={'kind':'synthetic_exact','reference':'M2 mathematical fixture','version':'v1'}


def sample_hash(array):
    return sha256(array.astype('<f8',copy=False).tobytes()).hexdigest()


def main():
    envelope_cases=[]
    for count in (48000,48001):
        for depth in (.25,0.):
            guard=161;n=count+2*guard;t=(np.arange(n)-guard)/12000;fm=480*12000/count
            truth=2*(1+depth*np.cos(2*np.pi*fm*t))
            raw=truth*np.cos(2*np.pi*3000*t)+.8*np.cos(2*np.pi*10*t)
            before=sample_hash(raw)
            start=time.perf_counter();result=analyze_envelope(raw,BASE);elapsed=time.perf_counter()-start
            assert result.status=='ok' and result.summary['guard_samples_each_side']==guard
            amplitude=float(result.arrays['amplitude_m_s2'][480])
            error=float(np.max(np.abs(result.arrays['envelope_m_s2']-truth[guard:n-guard])))
            frequencies=result.arrays['frequency_hz'];spectrum=result.arrays['amplitude_m_s2']
            maximum=float(spectrum[(frequencies>=10)&(frequencies<=500)].max())
            assert abs(amplitude-2*depth)<=1e-5+.01*(2*depth) and error<=.02
            if depth==0:assert maximum<=2e-4
            assert before==sample_hash(raw)
            envelope_cases.append({'effective_count':count,'modulation_depth':depth,'expected_peak_amplitude_m_s2':2*depth,
                'actual_peak_amplitude_m_s2':amplitude,'max_envelope_error_m_s2':error,'max_10_to_500_hz_amplitude_m_s2':maximum,
                'float64_samples_sha256':before,'seconds':elapsed,'configuration_sha256':result.summary['numeric_configuration_sha256'],
                'evidence_preconditions_satisfied':result.summary['evidence_preconditions_satisfied']})
    rng=np.random.default_rng(20261029);t=np.arange(1_000_000)/12000
    raw=2*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)+.01*rng.standard_normal(len(t))
    checksum=sample_hash(raw);start=time.perf_counter();large=analyze_envelope(raw,BASE);elapsed=time.perf_counter()-start
    assert large.status=='ok' and checksum==sample_hash(raw)
    assert all(np.isfinite(array).all() for array in large.arrays.values())
    frequency_parameters={'parameterization':'geometry','geometry':{
        'rolling_elements':8,'rolling_element_diameter':10.,'pitch_diameter':40.,'length_unit':'mm','contact_angle_deg':0.,
        'bearing_design':'single_row_ball','source':SOURCE,'checked':True,
        'uncertainty':{'rolling_element_diameter_absolute':0.,'pitch_diameter_absolute':0.,'contact_angle_deg_absolute':0.,'source':SOURCE}},
        'conditions':{'target':{'measurement_revision_id':'00000000-0000-0000-0000-000000000001',
                              'component_id':'00000000-0000-0000-0000-000000000002','bearing_revision_id':'00000000-0000-0000-0000-000000000003'},
            'rpm':1800.,'rpm_basis':'synthetic_exact','rpm_source':SOURCE,'rpm_checked':True,'rpm_uncertainty_rpm':0.,
            'rpm_uncertainty_source':SOURCE,'speed_condition':'constant_steady_checked','rotation':'inner_ring_rotates_outer_fixed'}}
    frequencies=calculate_bearing_frequencies(frequency_parameters)
    expected={'FTF':11.25,'BPFO':90.,'BPFI':150.,'BSF':56.25,'2BSF':112.5}
    assert frequencies.status=='ok'
    for name,value in expected.items():assert abs(frequencies.summary['frequencies'][name]['frequency_hz']-value)<=1e-10+1e-10*abs(value)
    sources=['backend/src/vibradiag/analysis/envelope.py','backend/src/vibradiag/analysis/bearing_frequencies.py',
             'backend/src/vibradiag/domain/tool_results.py','backend/src/vibradiag/schemas/tools.py',
             'backend/tests/unit/test_envelope.py','backend/tests/unit/test_bearing_frequencies.py',
             'backend/scripts/verify_m2_numerics.py','backend/requirements.lock.txt']
    report={'passed':True,'purpose':'M2-T02a standalone numerics, not full M2 acceptance',
        'environment':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,'os':platform.platform()},
        'source_sha256':{name:sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() for name in sources},
        'envelope_cases':envelope_cases,
        'million_point_smoke':{'status':large.status,'input_count':len(raw),'effective_count':large.summary['effective_sample_count'],
            'seconds':elapsed,'arrays_bytes':sum(array.nbytes for array in large.arrays.values()),'all_arrays_finite':True,
            'float64_samples_sha256':checksum,'timing_scope':'pure function; not HTTP/persistence, not a memory peak measurement'},
        'frequency_fixture':{name:row['frequency_hz'] for name,row in frequencies.summary['frequencies'].items()},
        'domain_references_are_synthetic_not_database_records':True,'published_profiles':0,'factory_data_used':False,
        'database_schema_changed':False,'api_or_ui_integrated':False,'model_called':False}
    destination=PROJECT_ROOT/'experiments/m2-t02a-numerics.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':True,'envelope_cases':len(envelope_cases),'million_point_seconds':elapsed,
                      'million_point_effective_count':large.summary['effective_sample_count'],'frequency_fixture':report['frequency_fixture'],
                      'published_profiles':0,'api_or_ui_integrated':False}))


if __name__=='__main__':
    main()
