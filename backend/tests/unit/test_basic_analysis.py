from decimal import Decimal, localcontext
import math
import numpy as np
import pytest

from vibradiag.analysis.basic import compute_basic
from vibradiag.domain.measurement_errors import MeasurementError


def value(result,name):
    return result.summary['metrics'][name]['value']


@pytest.mark.parametrize('dc,unit,factor',[(0,'m/s2',1),(3,'m/s2',1),(0,'g',9.80665)])
def test_bin_centered_sine_metrics_and_amplitude(dc,unit,factor):
    n=np.arange(1024);x=dc+2*np.sin(2*np.pi*64*n/1024)
    result=compute_basic(x,1024,unit)
    expected={'mean':dc*factor,'rms':math.sqrt(dc*dc+2)*factor,'ac_rms':math.sqrt(2)*factor,
              'absolute_peak':(dc+2)*factor,'peak_to_peak':4*factor,
              'crest_factor':(dc+2)/math.sqrt(dc*dc+2),'pearson_kurtosis':1.5}
    for name,target in expected.items():
        assert value(result,name)==pytest.approx(target,abs=1e-10,rel=1e-9)
    assert result.frequency[np.argmax(result.amplitude)]==pytest.approx(64,abs=1e-9)
    assert result.amplitude[64]==pytest.approx(2*factor,abs=1e-10,rel=1e-8)
    assert result.summary['duration_s']==1 and result.summary['last_sample_time_s']==1023/1024


@pytest.mark.parametrize('constant',[0,3,-3])
def test_zero_and_constant_do_not_claim_health(constant):
    result=compute_basic(np.full(1024,constant),1024,'m/s2')
    assert value(result,'rms')==abs(constant) and value(result,'ac_rms')==0
    assert value(result,'absolute_peak')==abs(constant)
    assert value(result,'pearson_kurtosis') is None
    assert result.summary['metrics']['pearson_kurtosis']['reason']=='ZERO_VARIANCE'
    assert value(result,'crest_factor')==(1 if constant else None)
    assert np.all(result.amplitude==0)


@pytest.mark.parametrize('count,bin_index',[(1024,512),(1025,512),(1025,64)])
def test_even_nyquist_and_last_odd_bin_are_scaled_correctly(count,bin_index):
    n=np.arange(count)
    result=compute_basic(2*np.cos(2*np.pi*bin_index*n/count),count,'m/s2')
    # Near odd-length Nyquist, periodic Hann mixes its conjugate into the last bin.
    expected=1 if count==1025 and bin_index==512 else 2
    assert result.amplitude[bin_index]==pytest.approx(expected,abs=1e-10,rel=1e-8)
    assert result.frequency[bin_index]==bin_index


@pytest.mark.parametrize('scale',[1e-150,1e150,1e-300,1e300])
def test_scaled_moments_match_independent_decimal_reference(scale):
    x=np.tile(np.array([-2.,-1.,1.,2.])*scale,256)
    result=compute_basic(x,1024,'m/s2')
    with localcontext() as ctx:
        ctx.prec=80
        values=[Decimal.from_float(float(v)) for v in x[:4]]
        mean=sum(values)/4
        expected_rms=(sum(v*v for v in values)/4).sqrt()
        variance=sum((v-mean)**2 for v in values)/4
        expected_kurtosis=(sum((v-mean)**4 for v in values)/4)/(variance*variance)
    assert value(result,'rms')==pytest.approx(float(expected_rms),rel=1e-9,abs=0)
    assert value(result,'pearson_kurtosis')==pytest.approx(float(expected_kurtosis),rel=1e-8)


def test_off_bin_leakage_and_repeatability():
    x=2*np.sin(2*np.pi*64.25*np.arange(1024)/1024)
    first=compute_basic(x,1024,'m/s2');second=compute_basic(x,1024,'m/s2')
    assert abs(first.frequency[np.argmax(first.amplitude)]-64.25)<=1
    assert first.amplitude.max()<2
    assert first.summary['metrics']==second.summary['metrics']
    np.testing.assert_allclose(first.amplitude,second.amplitude,rtol=1e-8,atol=1e-10)


def test_unrepresentable_conversion_and_derived_metric():
    with pytest.raises(MeasurementError) as error:
        compute_basic(np.full(1024,1e308),1024,'g')
    assert error.value.code=='NUMERIC_RANGE_UNSUPPORTED'
    result=compute_basic(np.tile([1e308,-1e308],512),1024,'m/s2')
    assert value(result,'rms')==pytest.approx(1e308)
    assert value(result,'peak_to_peak') is None
    assert result.summary['metrics']['peak_to_peak']['reason']=='NUMERIC_RANGE_UNSUPPORTED'


def test_impulse_is_not_filtered_and_matches_closed_form_moments():
    count=1024;amplitude=4
    signal=np.zeros(count);signal[512]=amplitude
    result=compute_basic(signal,1024,'m/s2')
    expected={'mean':amplitude/count,'rms':amplitude/math.sqrt(count),
        'ac_rms':amplitude*math.sqrt(count-1)/count,'absolute_peak':amplitude,
        'peak_to_peak':amplitude,'crest_factor':math.sqrt(count),
        'pearson_kurtosis':(count*count-3*count+3)/(count-1)}
    for name,target in expected.items():
        assert value(result,name)==pytest.approx(target,abs=1e-10,rel=1e-9)
    assert max(result.waveform['values'])==amplitude and signal[512]==amplitude
