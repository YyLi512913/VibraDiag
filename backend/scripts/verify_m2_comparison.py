"""Deterministic comparison fixtures; no real records, database or model calls."""
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import platform
from time import perf_counter
from uuid import UUID
from xml.etree import ElementTree
import numpy as np
import scipy
from vibradiag.analysis.basic import compute_basic
from vibradiag.analysis.envelope import analyze_envelope
from vibradiag.analysis.comparison import compare_measurements
from vibradiag.core.paths import PROJECT_ROOT

SOURCE=dict(kind='synthetic_exact',reference='Analytic comparison fixture; no actual bearing/DAQ calibration',version='t04a-v1')


def verify(case,count,envelopes=False):
    t=np.arange(count)/12000
    before=2*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
    after=before.copy() if case=='copied_content' else before/2
    if case=='zero_baseline': before=np.zeros(count)
    signals=[before,after]; captures=[]; basics=[]; parents=[]
    digests=[sha256(x.tobytes()).hexdigest() for x in signals]
    for i,(name,signal,time) in enumerate(zip(('before','after'),signals,('2026-01-01T00:00:00Z','2026-01-01T00:10:00Z'))):
        revision=str(UUID(int=40+i))
        capture=dict(organization_id=str(UUID(int=1)),measurement_id=str(UUID(int=20+i)),measurement_revision_id=revision,
            file_object_id=str(UUID(int=10+i)),input_sha256=digests[i],basic_execution_id=str(UUID(int=50+i)),
            metadata=dict(sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(UUID(int=2)),measurement_point_id=str(UUID(int=3)),
                direction='horizontal_radial',rpm=1800.,load_value=100.,load_unit='Nm',acquired_at=time,source_type='synthetic'),
            provenance=dict(acquisition_id=case+':'+name,root_acquisition_refs=[case+':'+name],source=SOURCE,checked=True),
            timing=dict(time_reference='start',source=SOURCE,checked=True),measurement_chain=dict(configuration_sha256='d'*64,source=SOURCE,checked=True),
            acquisition=dict(low_hz=100.,high_hz=5000.,source=SOURCE,checked=True),
            load=dict(quantity='torque',reference='synthetic-shaft-torque',condition='constant_steady_checked',source=SOURCE,checked=True),
            speed=dict(target=dict(measurement_revision_id=revision,component_id=str(UUID(int=30)),bearing_revision_id=str(UUID(int=31))),
                rpm=1800.,rpm_basis='synthetic_exact',rpm_source=SOURCE,rpm_checked=True,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=SOURCE,
                speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'))
        captures.append(capture); basics.append(compute_basic(signal,12000.,'m/s2').summary)
        if envelopes:
            capture['envelope_execution_id']=str(UUID(int=60+i))
            parents.append(analyze_envelope(signal,dict(sampling_rate_hz=12000,acceleration_unit='m/s2',band_low_hz=2000,band_high_hz=4000,
                modulation_low_hz=10,modulation_high_hz=500,selection_reason='Fixed mathematical comparison')))
    if case=='unknown_load':
        captures[1]['metadata'].pop('load_value');captures[1]['metadata'].pop('load_unit')
    parameters=dict(order_checked=True,order_source=SOURCE,load_tolerance=dict(absolute=0.,unit='Nm',quantity='torque',
        reference='synthetic-shaft-torque',source=SOURCE,checked=True))
    started=perf_counter()
    result=compare_measurements(captures[0],captures[1],basics[0],basics[1],parameters,
        **(dict(before_envelope=parents[0],after_envelope=parents[1]) if envelopes else {}))
    elapsed=perf_counter()-started
    if case=='copied_content':
        assert result.status=='unavailable' and result.summary['comparison_status']=='related_or_same_acquisition' and not result.summary['metrics']
    elif case=='unknown_load':
        assert result.status=='unavailable' and result.summary['comparison_status']=='not_comparable' and not result.summary['metrics']
    else:
        assert result.status=='ok' and result.summary['comparison_status']=='comparable'
        row=result.summary['metrics']['rms']
        if case=='zero_baseline':
            assert row['relative_change_percent'] is None and row['percent_reason']=='ZERO_BASELINE'
        else:
            with localcontext() as context:
                context.prec=80
                expected=(Decimal.from_float(row['after'])-Decimal.from_float(row['before']))/Decimal.from_float(row['before'])*100
            assert abs(Decimal.from_float(row['relative_change_percent'])-expected)<Decimal('1e-10')
            assert abs(row['relative_change_percent']+50)<1e-10
            if envelopes: assert abs(result.summary['metrics']['envelope_rms']['relative_change_percent']+50)<1e-8
    assert digests==[sha256(x.tobytes()).hexdigest() for x in signals]
    assert result.summary['repair_effectiveness'] is None and not result.summary['diagnostic_evidence_eligible']
    return dict(case=case,original_sample_count=count,envelope_comparison=envelopes,signal_float64_sha256=digests,
        comparison_only_seconds=elapsed,tool_status=result.status,comparison_status=result.summary['comparison_status'],
        metrics=result.summary['metrics'],reasons=result.summary['reasons'],related_reasons=result.summary['related_reasons'],
        repair_effectiveness=None,diagnostic_evidence_eligible=False)


def main():
    cases=[verify('half_amplitude',48322,True),verify('zero_baseline',4096),verify('copied_content',4096),
           verify('unknown_load',4096),verify('million_half_amplitude',1000000,True)]
    suite=ElementTree.parse(PROJECT_ROOT/'experiments/m2-t04a-tests.xml').getroot().find('testsuite')
    assert int(suite.attrib['failures'])==int(suite.attrib['errors'])==int(suite.attrib['skipped'])==0
    paths=['backend/src/vibradiag/analysis/comparison.py','backend/src/vibradiag/schemas/comparison.py',
        'backend/tests/unit/test_comparison.py','backend/scripts/verify_m2_comparison.py',
        'backend/src/vibradiag/analysis/basic.py','backend/src/vibradiag/analysis/envelope.py',
        'backend/src/vibradiag/schemas/measurements.py','backend/src/vibradiag/schemas/tools.py',
        'backend/src/vibradiag/domain/tool_results.py','backend/requirements.lock.txt','experiments/m2-t04a-tests.xml']
    report=dict(scope='M2-T04a pure comparison core; no HTTP/persistence or field effectiveness evaluation',
        environment=dict(python=platform.python_version(),numpy=np.__version__,scipy=scipy.__version__),cases=cases,
        fixture_ids_are_real_records=False,factory_data_used=False,qualification_published=False,
        full_backend_tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},
        limitations=['Signal hashes describe synthetic float64 fixture bytes, not field CSV originals',
            'Only the comparison function is timed; parent calculations, HTTP, database and memory are excluded',
            'Supplied declarations do not prove calibration, independence or repair effectiveness'],
        source_sha256={p:sha256((PROJECT_ROOT/p).read_bytes()).hexdigest() for p in paths})
    (PROJECT_ROOT/'experiments/m2-t04a-comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps(dict(cases=[dict(case=c['case'],samples=c['original_sample_count'],status=c['comparison_status'],
        seconds=c['comparison_only_seconds']) for c in cases],full_backend_tests=report['full_backend_tests'])))


if __name__=='__main__':
    main()
