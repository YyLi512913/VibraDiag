from hashlib import sha256
import importlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import numpy as np
from pydantic import ValidationError
import pytest
from scipy.io import savemat

from vibradiag.evaluation.datasets import DatasetManifest,DatasetRecord,samples_sha256,audit_manifest
from vibradiag.evaluation.holdout import (HoldoutAsset,SplitPlan,planned_members,read_frozen_bundle,verify_bindings)
from vibradiag.evaluation.readiness import check_validation_record

ROOT=Path(__file__).resolve().parents[3]
CONFIG=ROOT/'datasets/public-data-v2'


def bundle():return read_frozen_bundle(CONFIG,ROOT)


def test_frozen_entities_are_disjoint_and_test_remains_archive_only():
    plan,catalog,development,validation,protocol=bundle()
    assert len(validation.records)==24
    assert {x.bearing_id for x in plan.validation}=={'K002','KA03','KI03'}
    assert {x.bearing_id for x in plan.sealed_test}=={'K003','KA04','KI04'}
    assert all(not asset.members for asset in catalog.assets if asset.split=='sealed_test')
    audit=audit_manifest(DatasetManifest(schema_version='public-records-v1',purpose='test',split_policy='frozen',
        records=development.records+validation.records))
    assert audit['split_counts']=={'development':28,'validation':24,'sealed_test':0}
    assert audit['missing_four_class_labels']['validation']==['rolling_element']
    assert not audit['four_class_evaluation_ready']


@pytest.mark.parametrize('bearing',['K003','KA04','KI04','K001'])
def test_only_frozen_validation_entities_have_extractable_members(bearing):
    plan,*_=bundle()
    with pytest.raises(ValueError,match='sealed or unassigned'):planned_members(plan,bearing)


def test_same_bearing_cannot_move_across_partitions():
    data=bundle()[0].model_dump();data['sealed_test'][0]['bearing_id']='K002'
    with pytest.raises(ValidationError,match='multiple assignments'):SplitPlan.model_validate(data)


def test_test_asset_cannot_acquire_member_list():
    _,catalog,*_=bundle()
    data=next(asset.model_dump() for asset in catalog.assets if asset.split=='sealed_test')
    data['members']=[next(asset.members[0].model_dump() for asset in catalog.assets if asset.split=='validation')]
    with pytest.raises(ValidationError,match='must not contain'):HoldoutAsset.model_validate(data)


def test_asset_source_cannot_be_redirected_or_rebound():
    _,catalog,*_=bundle();data=catalog.assets[0].model_dump();data['url']='https://example.com/data.rar'
    with pytest.raises(ValidationError,match='official archive'):HoldoutAsset.model_validate(data)


@pytest.mark.parametrize('field,value', [('entity_group','different-bearing'),('acquisition_root','different-capture'),
    ('label','unknown'),('record_id','different-id'),('source_url','https://example.com/record')])
def test_validation_snapshot_must_match_frozen_entity_and_acquisition(field,value):
    plan,catalog,development,validation,_=bundle()
    rows=list(validation.records);data=rows[0].model_dump();data[field]=value;rows[0]=DatasetRecord.model_validate(data)
    changed=DatasetManifest(schema_version='public-records-v1',purpose='test',split_policy='frozen',records=tuple(rows))
    with pytest.raises(ValueError,match='identities disagree'):verify_bindings(plan,catalog,development,changed)


def copy_config(tmp_path):
    config=tmp_path/'datasets/public-data-v2';config.parent.mkdir()
    shutil.copytree(CONFIG,config)
    shutil.copytree(ROOT/'datasets/public-data-v1',config.parent/'public-data-v1')
    return config


@pytest.mark.parametrize('filename',['split-plan.json','validation-protocol.json'])
def test_post_freeze_edits_are_rejected(tmp_path,filename):
    config=copy_config(tmp_path);path=config/filename;path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='fingerprint changed'):read_frozen_bundle(config,tmp_path)


def test_sealed_archive_duplicate_of_development_is_detected_without_parsing(tmp_path):
    config=copy_config(tmp_path);path=config/'catalog.json';data=json.loads(path.read_text())
    old=json.loads((config.parent/'public-data-v1/catalog.json').read_text())['assets'][0]['sha256']
    data['assets'][-1]['sha256']=old;path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='archive bytes repeat'):read_frozen_bundle(config,tmp_path)


def test_sealed_reader_denial_precedes_every_file_read(tmp_path):
    record=bundle()[3].records[0]
    record=DatasetRecord.model_validate(record.model_dump()|{'split':'sealed_test'})
    with patch('vibradiag.evaluation.readiness.verified_signal',side_effect=AssertionError('must not read')):
        with pytest.raises(ValueError,match='cannot open'):check_validation_record(record,tmp_path,bundle()[4])


def test_sealed_extractor_denial_precedes_subprocess_and_path_access(monkeypatch,tmp_path):
    monkeypatch.syspath_prepend(str(ROOT/'backend/scripts'))
    script=importlib.import_module('prepare_holdout_data')
    asset=next(asset for asset in bundle()[1].assets if asset.split=='sealed_test')
    with patch.object(script.subprocess,'run',side_effect=AssertionError('must not extract')):
        with patch.object(script,'inside',side_effect=AssertionError('must not access member path')):
            with pytest.raises(ValueError,match='never be extracted'):script.extract_validation(tmp_path,asset,'unused')


def test_readiness_preserves_missing_unit_and_timebase_blocks(tmp_path):
    n=2048;values=np.sin(np.arange(n));axis=np.arange(n)/63900.
    path=tmp_path/'record.mat'
    savemat(path,{'record':{'Y':{'Name':'vibration_1','Data':values,'Unit':'','XIndex':1,'DownSampling':1},
                          'X':{'Data':axis}}})
    original=path.read_bytes();base=bundle()[3].records[0]
    record=DatasetRecord.model_validate(base.model_dump()|{'relative_path':'record.mat',
        'file_sha256':sha256(original).hexdigest(),'samples_sha256':samples_sha256(values),'sample_count':n})
    result=check_validation_record(record,tmp_path,bundle()[4])
    assert set(result['blocking_reasons'])=={'STORED_ACCELERATION_UNIT_NOT_ESTABLISHED','ORIGINAL_TIME_AXIS_NOT_UNIFORM_AT_NOMINAL_RATE'}
    assert not result['input_ready'] and not result['features_computed']
    assert not result['resampled'] and not result['unit_overridden'] and result['prediction'] is None
    assert path.read_bytes()==original
