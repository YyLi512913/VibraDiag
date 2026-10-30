"""Synthetic pure-function receipt; IDs are fixtures, never database evidence."""
from hashlib import sha256
import json
import platform
from time import perf_counter
from uuid import UUID
from xml.etree import ElementTree
import numpy as np
import scipy
from vibradiag.analysis.envelope import analyze_envelope
from vibradiag.analysis.basic import compute_basic
from vibradiag.analysis.bearing_frequencies import calculate_bearing_frequencies
from vibradiag.analysis.matching import match_frequency_evidence
from vibradiag.core.paths import PROJECT_ROOT

EXACT=dict(kind='synthetic_exact',reference='Analytic coefficients and AM/carrier fixture, not a real bearing model',version='1')


def verify(count, beating):
    t=np.arange(count)/12000
    x=2*np.cos(2*np.pi*3000*t)+.2*np.cos(2*np.pi*3095*t) if beating else 2*(1+.25*np.cos(2*np.pi*95*t))*np.cos(2*np.pi*3000*t)
    digest=sha256(x.tobytes()).hexdigest()
    revision=str(UUID(int=1)); context=str(UUID(int=2))
    conditions=dict(target=dict(measurement_revision_id=revision,component_id=str(UUID(int=3)),bearing_revision_id=str(UUID(int=4))),
        rpm=1800.,rpm_basis='synthetic_exact',rpm_source=EXACT,rpm_checked=True,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=EXACT,
        speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed')
    theory=calculate_bearing_frequencies(dict(parameterization='coefficients',conditions=conditions,coefficients=dict(
        values=dict(FTF=13/30,BPFO=3.17,BPFI=5.83,BSF=2.17),unit='shaft_order',bsf_definition='spin',
        rotation_assumption='inner_ring_rotates_outer_fixed',applicability='Mathematical test only',source=EXACT,checked=True,
        uncertainty=dict(FTF=0.,BPFO=0.,BPFI=0.,BSF=0.,source=EXACT))))
    envelope=analyze_envelope(x,dict(sampling_rate_hz=12000,acceleration_unit='m/s2',band_low_hz=2000,band_high_hz=4000,
        modulation_low_hz=10,modulation_high_hz=500,selection_reason='Fixed AM/two-carrier developer check'))
    basic=compute_basic(x,12000.,'m/s2')
    source=dict(measurement_revision_id=revision,context_id=context,input_sha256=digest)
    parameters=dict(envelope_source=dict(source,execution_id=str(UUID(int=5))),frequency_source=dict(source,execution_id=str(UUID(int=6))),
        basic_source=dict(source,execution_id=str(UUID(int=7))))
    started=perf_counter()
    result=match_frequency_evidence(envelope,theory,parameters,basic_spectrum=dict(frequency_hz=basic.frequency,
        amplitude_m_s2=basic.amplitude,sample_count=count))
    elapsed=perf_counter()-started
    assert result.status=='ok' and not result.summary['evidence_eligible'] and not result.summary['confounder_checks_complete']
    target=next(r for r in result.summary['rows'] if r['target_id']=='BPFO:1')
    assert any(abs(c['frequency_hz']-95)<=12000/(count-322) for c in target['candidates'])
    if beating:
        assert target['status']=='ambiguous' and any(c['kind']=='possible_carrier_beating' for c in target['confounders'])
    assert digest==sha256(x.tobytes()).hexdigest()
    return dict(original_sample_count=count,synthetic_signal_float64_sha256=digest,case='two_carrier' if beating else 'AM',
        matching_only_seconds=elapsed,result_status=result.status,target=target,row_count=len(result.summary['rows']),
        evidence_eligible=False,confounder_checks_complete=False,independent_acquisition_count=1)


def main():
    cases=[verify(48322,False),verify(48322,True),verify(1000000,False)]
    suite=ElementTree.parse(PROJECT_ROOT/'experiments/m2-t03a-tests.xml').getroot().find('testsuite')
    assert int(suite.attrib['failures'])==int(suite.attrib['errors'])==int(suite.attrib['skipped'])==0
    paths=['backend/src/vibradiag/analysis/matching.py','backend/src/vibradiag/schemas/matching.py',
        'backend/tests/unit/test_matching.py','backend/scripts/verify_m2_matching.py',
        'backend/src/vibradiag/analysis/envelope.py','backend/src/vibradiag/analysis/bearing_frequencies.py',
        'backend/src/vibradiag/analysis/basic.py','backend/requirements.lock.txt','experiments/m2-t03a-tests.xml']
    report=dict(scope='M2-T03a pure numerical matching; no HTTP, persistence or registered evidence',
        environment=dict(python=platform.python_version(),numpy=np.__version__,scipy=scipy.__version__),
        fixture_ids_are_real_records=False,factory_data_used=False,qualification_published=False,cases=cases,
        full_backend_tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},
        limitations=['Developer thresholds, not industrial fault criteria','Single matching-only timing; not an API/memory/P95 benchmark',
            'Carrier screen is bounded and filter sensitivity is untested; no evidence eligibility'],
        source_sha256={p:sha256((PROJECT_ROOT/p).read_bytes()).hexdigest() for p in paths})
    (PROJECT_ROOT/'experiments/m2-t03a-matching.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps(dict(cases=[dict(case=c['case'],samples=c['original_sample_count'],status=c['target']['status'],
        seconds=c['matching_only_seconds']) for c in cases],full_backend_tests=report['full_backend_tests'])))


if __name__=='__main__':
    main()
