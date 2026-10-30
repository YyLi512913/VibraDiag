import importlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from vibradiag.evaluation.datasets import DatasetRecord
from vibradiag.evaluation.holdout import read_frozen_bundle
from vibradiag.evaluation.time_adaptation import adapt_stored_axis,shape_features,inspect_research_record

ROOT=Path(__file__).resolve().parents[3]


@pytest.fixture
def protocol():return json.loads((ROOT/'datasets/public-data-v2/research-adaptation-protocol.json').read_text())


def adapt(values,axis,protocol):
    return adapt_stored_axis(values,axis,protocol,assumption_acknowledged=True)


def test_assumption_is_explicit_and_never_verifies_physics(protocol):
    t=np.arange(4096)/64000.;x=np.sin(t)
    denied=adapt_stored_axis(x,t,protocol)
    assert denied.summary['reason']=='RESEARCH_TIME_ASSUMPTION_NOT_ACKNOWLEDGED'
    allowed=adapt(x,t,protocol)
    assert allowed.status=='ok'
    assert not allowed.summary['time_axis_semantics_verified']
    assert not allowed.summary['physical_unit_established']
    assert not allowed.summary['diagnostic_evidence_eligible']


def test_linear_truth_endpoints_hashes_and_originals_preserved(protocol):
    fs=64000.;k=np.arange(12000);t=np.r_[0.,np.cumsum(1+.01*np.sin(k[:-1]))]/fs
    x=2*t-1;before=x.copy();axis_before=t.copy();result=adapt(x,t,protocol)
    assert result.status=='ok'
    assert result.relative_time_s[0]==0 and result.relative_time_s[-1]<=t[-1]-t[0]
    assert np.allclose(result.values,(2*result.relative_time_s-1)/np.max(np.abs(x)),atol=1e-14)
    assert np.array_equal(x,before) and np.array_equal(t,axis_before)
    assert result.summary['original_samples_sha256'] and result.summary['original_axis_sha256']
    assert result.summary['output_proxy_sha256']
    assert not result.values.flags.writeable and not result.relative_time_s.flags.writeable
    assert adapt(x,t,protocol).summary==result.summary


def test_origin_shift_does_not_shift_shape(protocol):
    t=np.arange(4096)/64000.;x=np.cos(t*200)
    a=adapt(x,t,protocol);b=adapt(x,t+10.,protocol)
    assert a.status==b.status=='ok'
    count=min(len(a.values),len(b.values))
    assert np.allclose(a.values[:count],b.values[:count],atol=1e-11)


@pytest.mark.parametrize('kind,reason',[
    ('gap','GAP_EXCEEDS_RESEARCH_LIMIT'),('tiny_interval','INTERVAL_BELOW_RESEARCH_LIMIT'),
    ('duplicate','TIME_AXIS_NOT_STRICTLY_INCREASING'),('reverse','TIME_AXIS_NOT_STRICTLY_INCREASING'),
    ('nan','NONFINITE_OR_NONVECTOR_INPUT'),('length','TIME_AXIS_LENGTH_MISMATCH'),
    ('offset','TIME_COORDINATE_RESOLUTION_INSUFFICIENT'),('cadence','CADENCE_DIFFERS_FROM_RESEARCH_SCOPE')])
def test_refusals_are_specific_and_have_no_derived_signal(protocol,kind,reason):
    t=np.arange(4096)/64000.;x=np.ones(4096)
    if kind=='gap':t[1000:]+=2/64000
    if kind=='tiny_interval':t[1000]=t[999]+.01/64000
    if kind=='duplicate':t[1000]=t[999]
    if kind=='reverse':t=t[::-1]
    if kind=='nan':t[1000]=np.nan
    if kind=='length':t=t[:-1]
    if kind=='offset':t+=1e9
    if kind=='cadence':t*=1.05
    result=adapt(x,t,protocol)
    assert result.status=='unavailable' and result.summary['reason']==reason
    assert result.values is None and result.relative_time_s is None


def test_output_limit_checked_before_large_grid_allocation(protocol):
    # Small cadence change can put a maximum-size input just beyond the output cap.
    t=np.arange(1000000)*1.001/64000.;x=np.ones(len(t))
    with patch('vibradiag.evaluation.time_adaptation.np.arange',side_effect=AssertionError('must not allocate grid')):
        result=adapt(x,t,protocol)
    assert result.summary['reason']=='OUTPUT_POINT_LIMIT'


def test_unknown_dynamic_range_does_not_silently_underflow(protocol):
    t=np.arange(4096)/64000.;x=np.ones(4096);x[0]=1e308;x[1]=1e-308
    assert adapt(x,t,protocol).summary['reason']=='NORMALIZATION_UNDERFLOW'


def test_zero_proxy_does_not_become_health_or_peak_claim(protocol):
    t=np.arange(80000)/64000.;result=adapt(np.zeros(len(t)),t,protocol)
    assert result.status=='ok'
    summary,_=shape_features(result.values,protocol)
    assert summary['dominant_modulation_hz_under_assumption'] is None
    assert summary['dominant_bin_fraction'] is None
    assert summary['physical_metrics'] is None and summary['prediction'] is None


def test_shape_features_are_scale_invariant_and_show_known_modulation(protocol):
    t=np.arange(128000)/64000.;signal=(1+.3*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
    summaries=[]
    for scale in (1.,9.80665):
        result=adapt(signal*scale,t,protocol);summary,_=shape_features(result.values,protocol);summaries.append(summary)
        assert abs(summary['dominant_modulation_hz_under_assumption']-120)<1.
        assert summary['physical_metrics'] is None and not summary['diagnostic_evidence_eligible']
    assert summaries[0]['dominant_bin_fraction']==pytest.approx(summaries[1]['dominant_bin_fraction'])


def test_sealed_record_is_denied_before_io(protocol,tmp_path):
    record=read_frozen_bundle(ROOT/'datasets/public-data-v2',ROOT)[3].records[0]
    record=DatasetRecord.model_validate(record.model_dump()|{'split':'sealed_test'})
    with patch('vibradiag.evaluation.time_adaptation.verified_signal',side_effect=AssertionError('must not read')):
        with pytest.raises(ValueError,match='sealed-test'):inspect_research_record(record,tmp_path,protocol)


def test_batch_preflight_denies_sealed_record_before_any_other_signal(monkeypatch,protocol,tmp_path):
    monkeypatch.syspath_prepend(str(ROOT/'backend/scripts'));script=importlib.import_module('benchmark_time_adaptation')
    record=read_frozen_bundle(ROOT/'datasets/public-data-v2',ROOT)[3].records[0]
    sealed=DatasetRecord.model_validate(record.model_dump()|{'split':'sealed_test'})
    with patch.object(script,'inspect_research_record',side_effect=AssertionError('must not read batch')):
        with pytest.raises(ValueError,match='no signals have been read'):script.evaluate_records([record,sealed],tmp_path,protocol)


def test_protocol_cannot_loosen_gap_gate_without_new_version(protocol):
    protocol['max_step_nominal']=100.
    with pytest.raises(ValueError,match='changed research protocol'):
        adapt(np.ones(4096),np.arange(4096)/64000.,protocol)
