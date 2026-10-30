import math
import numpy as np
import pytest
from vibradiag.analysis.signal import analyze_csv

def csv(values):
    return ('value\n'+'\n'.join(map(str,values))).encode()

@pytest.mark.parametrize('n',[4095,4096])
def test_calibrated_sine_odd_even(n):
    values=np.sin(2*np.pi*128*np.arange(n)/n)
    result=analyze_csv(csv(values),n,'m/s2')
    assert result['features']['rms']==pytest.approx(1/math.sqrt(2),abs=1e-12)
    assert result['features']['pearson_kurtosis']==pytest.approx(1.5,abs=1e-12)
    assert result['peaks'][0]['hz']==pytest.approx(128)
    assert result['peaks'][0]['amplitude']==pytest.approx(1,abs=1e-12)

def test_dc_and_unit_equivalence():
    x=2+np.sin(2*np.pi*128*np.arange(4096)/4096)
    a=analyze_csv(csv(x),4096,'m/s2')
    b=analyze_csv(csv(x/9.80665),4096,'g')
    assert a['features']['rms']==pytest.approx(math.sqrt(4.5),abs=1e-12)
    assert a['features']['ac_rms']==pytest.approx(1/math.sqrt(2),abs=1e-12)
    assert b['features']==pytest.approx(a['features'],abs=1e-12)

def test_zero_and_large_impulse():
    zero=analyze_csv(csv([0]*1024),1024,'m/s2')
    assert zero['features']['crest_factor'] is None
    assert zero['features']['pearson_kurtosis'] is None
    x=np.zeros(1024);x[400]=100
    impulse=analyze_csv(csv(x),1024,'m/s2')
    assert impulse['features']['peak']==100
    assert max(point[1] for point in impulse['waveform_preview'])==100

def test_nyquist_not_doubled():
    result=analyze_csv(csv((-1.)**np.arange(4096)),4096,'m/s2')
    assert result['spectrum']['amplitude_m_s2'][-1]==pytest.approx(1)

@pytest.mark.parametrize('raw,rate,unit',[(b'value\nNaN',1024,'g'),(b'value\n1',1024,'g'),
    (csv([1]*1024),0,'g'),(csv([1]*1024),1024,'unknown'),(b'value\n\n1',1024,'g')])
def test_bad_input_rejected(raw,rate,unit):
    with pytest.raises(ValueError):
        analyze_csv(raw,rate,unit)
