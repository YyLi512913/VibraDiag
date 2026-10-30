from hashlib import sha256
import json
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text

from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services.legacy_import_service import import_directory,local_actor
from vibradiag.domain.measurement_errors import MeasurementError


def legacy_file(root,metadata=None):
    identifier=uuid4().hex;raw=b'value\n'+b'1\n'*1024
    record={'analysis_id':identifier,'sha256':sha256(raw).hexdigest(),'original_filename':'prototype.csv',
        'sampling_rate_hz':1024,'input_unit':'g','features':{'rms':999999},'diagnosis':'unverified old output'}
    record.update(metadata or {})
    (root/(identifier+'.csv')).write_bytes(raw)
    (root/(identifier+'.json')).write_text(json.dumps(record),encoding='utf8')
    return identifier


def test_dry_run_apply_replay_and_unconfirmed_provenance(database,monkeypatch,tmp_path):
    source=tmp_path/'legacy';source.mkdir();identifier=legacy_file(source)
    monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'formal'))
    with local_actor(database['peer']) as actor:
        with transaction() as connection:
            before=connection.execute(text('SELECT count(*) FROM measurements')).scalar_one()
        dry=import_directory(source,actor,'batch-1')
        assert dry['records'][0]['status']=='eligible'
        with transaction() as connection:
            assert connection.execute(text('SELECT count(*) FROM measurements')).scalar_one()==before
        imported=import_directory(source,actor,'batch-1',apply=True)
        row=imported['records'][0]
        assert row['status']=='imported_unconfirmed'
        repeated=import_directory(source,actor,'batch-1',apply=True)
        assert repeated['records'][0]['measurement_id']==row['measurement_id']
        assert repeated['records'][0]['status']=='already_imported'
        assert import_directory(source,actor,'batch-2',apply=True)['records'][0]['status']=='already_imported'
        with transaction() as connection:
            revision=connection.execute(text('SELECT * FROM measurement_revisions WHERE measurement_id=:id'),dict(id=UUID(row['measurement_id']))).mappings().one()
            assert revision['source_type']=='unknown'
            assert revision['parameter_sources']['sampling_rate_hz']['origin']=='legacy_unverified'
            assert connection.execute(text('SELECT count(*) FROM input_confirmations WHERE revision_id=:id'),dict(id=revision['id'])).scalar_one()==0
            assert connection.execute(text('SELECT count(*) FROM analysis_runs WHERE measurement_revision_id=:id'),dict(id=revision['id'])).scalar_one()==0
        conflict=import_directory(source,actor,'batch-3',apply=True,overrides={identifier:{'sampling_rate_hz':2048}})
        assert conflict['records'][0]['code']=='LEGACY_IMPORT_CONFLICT'
    assert len(list(source.iterdir()))==2


def test_missing_metadata_requires_explicit_manifest_and_hash_mismatch_rejected(database,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'formal'))
    source=tmp_path/'legacy';source.mkdir()
    identifier=legacy_file(source,{'input_unit':None})
    with local_actor(database['peer']) as actor:
        report=import_directory(source,actor,'missing')
        assert report['records'][0]['code']=='LEGACY_METADATA_INVALID'
        report=import_directory(source,actor,'fixed',apply=True,overrides={identifier:{'acceleration_unit':'g'}})
        assert report['records'][0]['status']=='imported_unconfirmed'
        (source/(identifier+'.csv')).write_bytes(b'value\n'+b'2\n'*1024)
        assert import_directory(source,actor,'tampered')['records'][0]['code']=='LEGACY_HASH_MISMATCH'


def test_admin_has_no_implicit_engineer_import_role(database):
    with pytest.raises(MeasurementError,match='engineer'):
        with local_actor(database['admin']):
            pass
