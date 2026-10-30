from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from scipy.io import savemat

from vibradiag.evaluation.baseline import shape_baseline
from vibradiag.evaluation.datasets import (DatasetManifest, DatasetRecord, audit_manifest,
    load_channel, samples_sha256, vector, verified_signal)


def record(**changes):
    data = dict(record_id='cwru-105-DE', dataset='cwru', relative_path='cwru/105.mat',
        file_sha256='a'*64, samples_sha256='b'*64, sample_count=48000, channel='X105_DE_time',
        sampling_rate_hz=12000., sampling_rate_source='official 12k fault table', unit=None, unit_source=None,
        label='inner_race', label_source='official fault table', entity_group='bearing-1', acquisition_root='capture-1',
        split='development', previously_used_for_development=False, source_url='https://engineering.case.edu/sites/default/files/105.mat',
        license_status='not_specified_on_reviewed_pages', limitations=['Research only'])
    data.update(changes)
    return DatasetRecord.model_validate(data)


def manifest(*records):
    return DatasetManifest(schema_version='public-records-v1', purpose='test', split_policy='grouped', records=records)


@pytest.mark.parametrize('field', ['entity_group', 'acquisition_root', 'file_sha256', 'samples_sha256'])
def test_aliases_cannot_leak_across_splits(field):
    before = record()
    data = before.model_dump(); data.update(record_id='different-name', relative_path='other.mat', split='validation',
        entity_group='other-bearing', acquisition_root='other-capture', file_sha256='c'*64, samples_sha256='d'*64)
    data[field] = getattr(before, field)
    after = DatasetRecord.model_validate(data)
    with pytest.raises(ValueError, match='cross-split source leakage'):
        audit_manifest(manifest(before, after))


def test_previously_used_data_cannot_be_sealed():
    with pytest.raises(ValidationError, match='held-out'):
        record(split='sealed_test', previously_used_for_development=True)


@pytest.mark.parametrize('path', ['../source.mat', 'C:/source.mat', '/source.mat', 'cwru\\source.mat'])
def test_paths_must_be_contained(path):
    with pytest.raises(ValidationError): record(relative_path=path)


def test_duplicate_id_and_empty_heldout_report():
    with pytest.raises(ValueError, match='duplicate record_id'): audit_manifest(manifest(record(), record()))
    report = audit_manifest(manifest(record()))
    assert report['four_class_evaluation_ready'] is False
    assert report['split_counts']['sealed_test'] == 0
    assert report['missing_four_class_labels']['validation']


def test_content_hash_uses_canonical_vector_and_signed_zero():
    x = np.arange(1024, dtype=float); x[0] = -0.
    assert samples_sha256(x.reshape(-1, 1)) == samples_sha256(x.reshape(1, -1))
    x[0] = 0.
    assert samples_sha256(x) == samples_sha256(np.arange(1024))
    with pytest.raises(ValueError, match='flattened'): vector(np.ones((1024, 2)))
    with pytest.raises(ValueError): vector(np.full(1024, np.nan))
    with pytest.raises(ValueError): vector(np.ones(1024, dtype=complex))


def test_original_and_channel_hashes_are_verified(tmp_path):
    path = tmp_path/'record.mat'; values = np.arange(1024, dtype=float)
    savemat(path, {'X105_DE_time': values})
    r = record(relative_path='record.mat', file_sha256=sha256(path.read_bytes()).hexdigest(),
               samples_sha256=samples_sha256(values), sample_count=1024)
    assert np.array_equal(verified_signal(r, tmp_path), values)
    wrong = record(**(r.model_dump() | {'samples_sha256': '0'*64}))
    with pytest.raises(ValueError, match='channel content'): verified_signal(wrong, tmp_path)
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError, match='file hash'): verified_signal(r, tmp_path)


def test_paderborn_selects_name_not_current_position_and_checks_unit(tmp_path):
    x = np.arange(1024, dtype=float)
    path = tmp_path/'record.mat'
    savemat(path, {'record': {'Y': [dict(Name='phase_current_1',Data=x*2,Unit='A',XIndex=1),
                                  dict(Name='vibration_1',Data=x,Unit='m/s^2',XIndex=1)],
                             'X': [dict(Data=x/64000)]}})
    actual, metadata = load_channel(path,'paderborn','vibration_1')
    assert np.array_equal(actual, x) and metadata['native_unit'] == 'm/s^2'
    r = record(dataset='paderborn', relative_path='record.mat', channel='vibration_1', sample_count=1024,
        file_sha256=sha256(path.read_bytes()).hexdigest(), samples_sha256=samples_sha256(x),
        unit='g', unit_source='incorrect statement')
    with pytest.raises(ValueError, match='unit differs'): verified_signal(r,tmp_path)
    with pytest.raises(ValueError): load_channel(path,'paderborn','phase_current_1')


@pytest.mark.parametrize('split',['validation','sealed_test'])
def test_development_runner_refuses_heldout(split):
    with pytest.raises(ValueError, match='development runner'):
        shape_baseline(record(split=split), np.ones(48000), {'time_axis':None})


def test_unknown_unit_shape_baseline_has_no_physical_or_diagnostic_claim():
    t = np.arange(48000)/12000
    x = (1+.3*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
    results = [shape_baseline(record(), x*scale, {'time_axis':None}) for scale in (1.,9.80665)]
    for row in results:
        assert row['physical_metrics'] is None and not row['unit_verified']
        assert row['prediction'] is None and not row['diagnostic_evidence_eligible']
        assert row['envelope']['status'] == 'ok'
        assert abs(row['envelope']['dominant_modulation_hz']-120) < 1
    assert results[0]['crest_factor']['value'] == pytest.approx(results[1]['crest_factor']['value'])
    assert results[0]['envelope']['dominant_bin_fraction'] == pytest.approx(results[1]['envelope']['dominant_bin_fraction'])


def test_missing_rate_and_export_time_mismatch_are_not_silently_processed():
    x = np.sin(np.arange(48000))
    row = shape_baseline(record(sampling_rate_hz=None,sampling_rate_source=None), x, {'time_axis':None})
    assert row['envelope']['reason'] == 'SAMPLING_RATE_NOT_VERIFIED'
    row = shape_baseline(record(),x,{'time_axis':np.arange(48000)/11900})
    assert row['envelope']['reason'] == 'TIME_AXIS_DIFFERS_FROM_NOMINAL_RATE'
    assert row['time_axis_check']['duration_relative_error'] > .001


def test_shape_normalization_does_not_silently_lose_nonzero_values():
    x = np.ones(48000); x[0] = 1e308; x[1] = 1e-308
    with pytest.raises(ValueError, match='underflows'):
        shape_baseline(record(),x,{'time_axis':None})


def test_checked_in_inventory_never_claims_heldout_readiness():
    root = Path(__file__).resolve().parents[3]
    config = root/'datasets/public-data-v1'
    inventory = DatasetManifest.model_validate_json((config/'manifest.json').read_text(encoding='utf8'))
    report = audit_manifest(inventory)
    assert report['record_count'] == 28 and report['entity_group_count'] == 7
    assert report['split_counts'] == {'development':28,'validation':0,'sealed_test':0}
    catalog = json.loads((config/'catalog.json').read_text(encoding='utf8'))
    assert not catalog['commercial_or_product_distribution_approved']
    assert catalog['sources']['paderborn']['license_status'] == 'CC-BY-NC-4.0'
    assert all(r.unit is None for r in inventory.records)
