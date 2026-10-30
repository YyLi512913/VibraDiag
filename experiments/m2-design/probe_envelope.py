"""Design probe only. Not a shipped tool, preset qualification or fault evaluation."""
from hashlib import sha256
import json
import math
from pathlib import Path
import platform

import numpy as np
import scipy
from scipy.signal import butter,hilbert,sosfiltfilt

fs=12000
sos=butter(4,[2000,4000],btype='bandpass',output='sos',fs=fs)
padlen=3*(2*len(sos)+1-min(int((sos[:,2]==0).sum()),int((sos[:,5]==0).sum())))
radius=max(abs(root) for row in sos for root in np.roots(row[3:]))
guard=max(3*padlen,math.ceil(2*math.log(1e-6)/math.log(radius)),math.ceil(fs/2000))
cases=[]
for effective_count in (48000,48001):
    count=effective_count+2*guard
    time=(np.arange(count)-guard)/fs
    modulation_hz=480*fs/effective_count
    for depth in (.25,0.):
        raw=2*(1+depth*np.cos(2*np.pi*modulation_hz*time))*np.cos(2*np.pi*3000*time)+.8*np.cos(2*np.pi*10*time)
        filtered=sosfiltfilt(sos,raw-raw.mean(),padtype='odd',padlen=padlen)
        padded=np.pad(filtered,(guard,guard),mode='reflect')
        envelope=np.abs(hilbert(padded,N=len(padded)))[guard:guard+count][guard:count-guard]
        truth=2*(1+depth*np.cos(2*np.pi*modulation_hz*time[guard:count-guard]))
        window=.5-.5*np.cos(2*np.pi*np.arange(effective_count)/effective_count)
        spectrum=np.abs(np.fft.rfft((envelope-envelope.mean())*window))/window.sum()
        spectrum[1:-1 if effective_count%2==0 else None]*=2
        frequencies=np.arange(len(spectrum))*fs/effective_count
        expected=2*depth
        amplitude=float(spectrum[480])
        in_range=(frequencies>=10)&(frequencies<=500)
        cases.append({'effective_count':effective_count,'modulation_depth':depth,'modulation_hz':modulation_hz,
            'expected_peak_amplitude_m_s2':expected,'measured_bin_amplitude_m_s2':amplitude,
            'amplitude_absolute_error':abs(amplitude-expected),
            'interior_envelope_max_error_m_s2':float(np.max(np.abs(envelope-truth))),
            'max_10_to_500_hz_amplitude_m_s2':float(spectrum[in_range].max()),
            'float64_samples_sha256':sha256(raw.astype('<f8',copy=False).tobytes()).hexdigest()})
result={'purpose':'M2-T01 design probe; not formal acceptance','python':platform.python_version(),
    'numpy':np.__version__,'scipy':scipy.__version__,'config':{'fs':fs,'band_hz':[2000,4000],
        'prototype_order':4,'sos_sections':len(sos),'single_pass_order':8,'forward_backward_response':'abs(H)**2',
        'padtype':'odd','padlen':padlen,'guard_samples_each_side':guard,'max_pole_radius':float(radius),
        'hilbert_extension':'reflect each side by guard; no zero padding'},'cases':cases,
    'bearing_formula_fixture_hz':{'rpm':1800,'rolling_elements':8,'d_mm':10,'D_mm':40,'contact_angle_deg':0,
                                'BPFO':90,'BPFI':150,'BSF':56.25,'two_BSF':112.5,'FTF':11.25},
    'profile_qualified':False,'factory_data_used':False}
Path(__file__).with_name('probe-results.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps(result,ensure_ascii=False))
