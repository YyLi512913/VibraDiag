from hashlib import sha256
import json
from uuid import uuid4

import numpy as np
import pytest

from vibradiag.analysis.envelope import analyze_envelope, configuration_sha256, _amplitude_spectrum
from vibradiag.domain.tool_results import ToolInputError
from vibradiag.schemas.tools import EnvelopeParameters


BASE = {'sampling_rate_hz':12000,'acceleration_unit':'m/s2','band_low_hz':2000,'band_high_hz':4000,
        'modulation_low_hz':10,'modulation_high_hz':500,'selection_reason':'Synthetic AM reference; no field calibration'}
EXACT = {'kind':'synthetic_exact','reference':'analytic-fixture','version':'v1'}
GUARD = 161  # Frozen design fixture, independently recorded before implementation.


def am_signal(effective_count=48000,depth=.25,scale=1.,carrier=3000):
    n=effective_count+2*GUARD;t=(np.arange(n)-GUARD)/12000
    fm=480*12000/effective_count
    truth=2*scale*(1+depth*np.cos(2*np.pi*fm*t))
    x=truth*np.cos(2*np.pi*carrier*t)+scale*.8*np.cos(2*np.pi*10*t)
    return x,truth[GUARD:n-GUARD],fm


def test_fixed_amplitude_and_boundary_contract():
    x,truth,fm=am_signal();before=x.copy();digest=sha256(x.tobytes()).hexdigest()
    result=analyze_envelope(x,BASE)
    assert result.status=='ok' and not result.summary['evidence_preconditions_satisfied']
    assert result.summary['padlen']==27 and result.summary['guard_samples_each_side']==GUARD
    segment=result.summary['effective_segment']
    assert segment=={'start_index':161,'stop_index_exclusive':48161,'sample_count':48000,
                     'start_time_s':161/12000,'last_sample_time_s':48160/12000,'duration_s':4}
    assert result.arrays['frequency_hz'][480]==fm
    assert np.argmax(result.arrays['amplitude_m_s2'])==480
    assert result.arrays['amplitude_m_s2'][480]==pytest.approx(.5,abs=1e-5,rel=.01)
    assert np.max(np.abs(result.arrays['envelope_m_s2']-truth))<=.02
    assert result.summary['response_at_band_edges_forward_backward_gain']==pytest.approx([.5,.5],abs=1e-10)
    assert result.summary['single_pass_order']==8 and result.summary['sos_sections']==4
    preview=result.summary['envelope_preview']
    assert len(preview['values'])<=2000 and min(preview['time_s'])>=161/12000
    assert all(index>=161 and index<48161 for index in preview['original_indices'])
    np.testing.assert_array_equal(x,before)
    assert sha256(x.tobytes()).hexdigest()==digest
    json.dumps(result.metadata(),allow_nan=False)
    assert not result.arrays['envelope_m_s2'].flags.writeable


def test_no_modulation_control_does_not_claim_health():
    x,_,_=am_signal(depth=0)
    result=analyze_envelope(x,BASE)
    frequencies=result.arrays['frequency_hz'];amplitude=result.arrays['amplitude_m_s2']
    assert amplitude[(frequencies>=10)&(frequencies<=500)].max()<=2e-4
    assert 'fault' not in result.summary and not result.summary['evidence_preconditions_satisfied']
    assert 'bearing fault classifications' in ' '.join(result.limitations)


def test_odd_effective_length_and_unit_equivalence():
    x,truth,fm=am_signal(48001)
    first=analyze_envelope(x,BASE)
    second=analyze_envelope(x/9.80665,dict(BASE,acceleration_unit='g'))
    assert first.status==second.status=='ok'
    assert len(first.arrays['frequency_hz'])==48001//2+1
    assert first.arrays['frequency_hz'][480]==pytest.approx(fm,abs=1e-9)
    assert first.arrays['amplitude_m_s2'][480]==pytest.approx(.5,abs=1e-5,rel=.01)
    assert np.max(np.abs(first.arrays['envelope_m_s2']-truth))<=.02
    np.testing.assert_allclose(first.arrays['amplitude_m_s2'],second.arrays['amplitude_m_s2'],atol=1e-10,rtol=1e-8)
    np.testing.assert_allclose(first.arrays['envelope_m_s2'],second.arrays['envelope_m_s2'],atol=1e-10,rtol=1e-8)


@pytest.mark.parametrize('count,k,expected',[(1024,64,2),(1024,512,2),(1025,64,2),(1025,512,1)])
def test_envelope_spectrum_dc_even_nyquist_and_last_odd_bin(count,k,expected):
    # Analytic nonnegative envelope, independent of the bandpass/Hilbert kernel.
    e=3+2*np.cos(2*np.pi*k*np.arange(count)/count)
    frequency,amplitude=_amplitude_spectrum(e,count)
    assert frequency[k]==k and amplitude[k]==pytest.approx(expected,abs=1e-10,rel=1e-8)
    # Odd last bin contains the neighboring conjugate's Hann lobe; it is still doubled.


@pytest.mark.parametrize('override',[{'sampling_rate_hz':True},{'sampling_rate_hz':float('inf')},
    {'band_low_hz':0},{'band_low_hz':4500},{'band_high_hz':6000},{'band_high_hz':float('nan')},
    {'modulation_low_hz':0},{'modulation_high_hz':1001},{'prototype_order':2},{'selection_reason':'   '},
    {'acceleration_unit':'mm/s'},{'execution_mode':'auto'}])
def test_invalid_parameters_fail_before_computation(override):
    with pytest.raises(ToolInputError):
        analyze_envelope(np.ones(20000),BASE|override)


@pytest.mark.parametrize('values',[np.ones((1024,1)),np.ones(1023),np.ones(1000001),
    np.full(1024,np.inf),np.array([True]*1024),np.array(['1']*1024),np.ones(1024,dtype=complex)])
def test_unvalidated_signal_is_rejected(values):
    with pytest.raises(ToolInputError):
        analyze_envelope(values,BASE)


def test_lengths_cycles_and_narrow_band_budget_are_unavailable():
    short=analyze_envelope(np.ones(1024),BASE)
    assert short.status=='unavailable' and short.error_code=='ENVELOPE_LENGTH_INSUFFICIENT' and not short.arrays
    cycles=analyze_envelope(np.ones(4000),BASE)
    assert cycles.error_code=='MODULATION_OBSERVATION_TOO_SHORT' and not cycles.arrays
    narrow=analyze_envelope(np.ones(20000),dict(BASE,band_low_hz=2500,band_high_hz=2500.1,modulation_low_hz=.001,modulation_high_hz=.01))
    assert narrow.status=='unavailable' and narrow.error_code=='ENVELOPE_RESOURCE_LIMIT' and not narrow.arrays
    assert narrow.summary['guard_samples_each_side']>65536


def test_boundary_impulses_are_not_deleted_or_hidden_as_original_data():
    x,_,_=am_signal();x[0]=20;x[-1]=-20;before=x.copy()
    result=analyze_envelope(x,BASE)
    assert result.status=='ok'
    np.testing.assert_array_equal(x,before)
    assert len(result.arrays['filtered_m_s2'])==len(x)
    assert result.summary['effective_segment']['start_index']==GUARD
    assert result.summary['effective_segment']['stop_index_exclusive']==len(x)-GUARD
    assert 'boundary-error' in ' '.join(result.limitations)


@pytest.mark.parametrize('scale',[1e-100,1e100])
def test_scale_stability(scale):
    x,_,_=am_signal(scale=scale)
    result=analyze_envelope(x,BASE)
    assert result.status=='ok'
    assert result.arrays['amplitude_m_s2'][480]/scale==pytest.approx(.5,abs=1e-5,rel=.01)
    json.dumps(result.metadata(),allow_nan=False)


@pytest.mark.parametrize('constant',[0,3,-3])
def test_constant_or_zero_does_not_become_a_health_assessment(constant):
    result=analyze_envelope(np.full(20000,constant),BASE)
    assert result.status=='ok'
    assert np.all(result.arrays['envelope_m_s2']==0) and np.all(result.arrays['amplitude_m_s2']==0)
    assert any('no dynamic information' in text for text in result.limitations)


def test_numeric_overflow_and_dynamic_range_are_explicit_failures():
    converted=analyze_envelope(np.full(20000,1e308),dict(BASE,acceleration_unit='g'))
    assert converted.status=='failed' and converted.error_code=='NUMERIC_RANGE_UNSUPPORTED'
    x=np.ones(20000)*1e-300;x[10000]=1e308
    mixed=analyze_envelope(x,BASE)
    assert mixed.status=='failed' and not mixed.arrays
    json.dumps(mixed.metadata(),allow_nan=False)


def evidence_parameters():
    return dict(BASE,execution_mode='diagnostic_evidence',
        acquisition={'low_hz':100,'high_hz':5000,'source':EXACT,'checked':True},
        qualification={'profile_id':str(uuid4()),'numeric_configuration_sha256':configuration_sha256(BASE),
                       'manifest_sha256':'a'*64,'source':EXACT})


def test_evidence_mode_requires_scoped_bandwidth_and_qualification():
    x,_,_=am_signal()
    missing=analyze_envelope(x,dict(BASE,execution_mode='diagnostic_evidence'))
    assert missing.status=='unavailable' and not missing.arrays
    assert set(missing.missing_requirements)=={'verified_acquisition_bandwidth','qualified_numeric_profile'}
    p=evidence_parameters()
    good=analyze_envelope(x,p)
    assert good.status=='ok' and good.summary['evidence_preconditions_satisfied']
    assert any('not field calibration' in text for text in good.limitations)
    p['band_low_hz']=2100
    changed=analyze_envelope(x,p)
    assert changed.status=='unavailable' and 'qualification_matches_exact_numeric_configuration' in changed.missing_requirements


def test_bandwidth_cannot_be_inferred_from_sample_rate():
    x,_,_=am_signal();p=evidence_parameters()
    p['acquisition']['high_hz']=3000
    result=analyze_envelope(x,p)
    assert result.status=='unavailable' and 'band_inside_effective_acquisition_bandwidth' in result.missing_requirements
    p['execution_mode']='exploratory'
    exploratory=analyze_envelope(x,p)
    assert exploratory.status=='ok' and not exploratory.summary['evidence_preconditions_satisfied']


def test_frozen_model_copy_cannot_bypass_parameter_validation():
    invalid=EnvelopeParameters.model_validate(BASE).model_copy(update={'band_low_hz':-1})
    with pytest.raises(ToolInputError):
        analyze_envelope(np.ones(20000),invalid)


def test_beating_is_a_counterexample_to_single_peak_fault_diagnosis():
    n=48000+2*GUARD;t=(np.arange(n)-GUARD)/12000
    x=2*np.cos(2*np.pi*3000*t)+.2*np.cos(2*np.pi*3120*t)
    result=analyze_envelope(x,BASE)
    assert result.status=='ok' and result.arrays['amplitude_m_s2'][480]>.1
    assert 'fault_class' not in result.summary and not result.summary['evidence_preconditions_satisfied']


def test_repeated_calls_have_equal_numerical_products():
    x,_,_=am_signal()
    first=analyze_envelope(x,BASE);second=analyze_envelope(x,BASE)
    assert first.metadata()==second.metadata()
    np.testing.assert_array_equal(first.arrays['amplitude_m_s2'],second.arrays['amplitude_m_s2'])


def test_neighboring_exploratory_band_has_its_own_processing_identity():
    x,_,fm=am_signal()
    base=analyze_envelope(x,BASE)
    neighbor=analyze_envelope(x,dict(BASE,band_low_hz=1900,band_high_hz=4100))
    assert base.status==neighbor.status=='ok'
    assert base.summary['numeric_configuration_sha256']!=neighbor.summary['numeric_configuration_sha256']
    assert neighbor.summary['configuration']['band_hz']==[1900,4100]
    assert not neighbor.summary['evidence_preconditions_satisfied']
    frequency=neighbor.arrays['frequency_hz'][np.argmax(neighbor.arrays['amplitude_m_s2'])]
    assert abs(frequency-fm)<=2*neighbor.summary['bin_spacing_hz']
