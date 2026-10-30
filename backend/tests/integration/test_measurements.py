from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from vibradiag.analysis.csv_input import MAX_FILE_BYTES
from vibradiag.infrastructure.persistence.database import engine, transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.main import app

RAW=b'\xef\xbb\xbfvalue\n'+b'1\n'*1024
META={'sampling_rate_hz':1024,'acceleration_unit':'g','source_type':'synthetic'}


@pytest.fixture(autouse=True)
def isolated_files(database,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'private-data'))
    from vibradiag.api.v1.identity import _attempts
    _attempts.clear()


def session(database,name='owner',org='org'):
    client=TestClient(app)
    response=client.post('/api/v1/auth/login',json=dict(organization_id=str(database[org]),username=name,password=database['password']))
    assert response.status_code==200,response.text
    client.headers['x-csrf-token']=response.json()['csrf_token']
    return client


def upload(client,raw=RAW,metadata=None,name='signal.csv'):
    return client.post('/api/v1/measurements',files={'file':(name,raw,'text/csv')},
                       data={'metadata':json.dumps(metadata or META)})


def imported(client,**kwargs):
    response=upload(client,**kwargs)
    assert response.status_code==201,response.text
    return response.json()


def test_upload_read_list_original_and_storage_identity(database):
    with session(database) as client:
        record=imported(client,name='../../bad-name.csv')
        identifier=record['id'];prefix=f'/api/v1/measurements/{identifier}'
        assert record['original_filename']=='bad-name.csv'
        assert record['sha256']==sha256(RAW).hexdigest()
        assert record['validation_summary']['sample_count']==1024
        assert record['preview']['unit']=='g' and record['input_confirmation'] is None
        assert len(record['preview']['values'])<=2000
        assert record['revision']['metadata']['acquired_at'] is None
        assert record['revision']['parameter_sources']['sampling_rate_hz']['origin']=='user_input'
        assert record['revision']['parameter_sources']['rpm']['origin']=='unknown'
        engine().dispose()  # New connections still read persisted state and original bytes.
        assert client.get(prefix).json()['revision']['id']==record['revision']['id']
        download=client.get(prefix+'/original')
        assert download.status_code==200 and download.content==RAW
        assert '..' not in download.headers['content-disposition']
        assert identifier in {r['id'] for r in client.get('/api/v1/measurements').json()['items']}
        with transaction() as conn:
            events=conn.execute(text('SELECT action FROM audit_events WHERE object_id=:id'),dict(id=UUID(identifier))).scalars().all()
        assert 'measurement.import' in events and 'measurement.download_original' in events


def test_confirmation_revision_and_old_confirmation_not_reused(database):
    with session(database) as client:
        record=imported(client);prefix=f"/api/v1/measurements/{record['id']}"
        body={'revision_id':record['revision']['id']}
        first=client.post(prefix+'/confirmations',json=body)
        assert first.status_code==200
        assert client.post(prefix+'/confirmations',json=body).json()['id']==first.json()['id']
        revised=client.post(prefix+'/revisions',json={'metadata':dict(META,sampling_rate_hz=2048),
            'reason':'Correct sampling rate','expected_current_revision_id':record['revision']['id'],'row_version':record['row_version']})
        assert revised.status_code==201,revised.text
        updated=revised.json()
        assert updated['row_version']==2 and updated['input_confirmation'] is None
        assert updated['sha256']==record['sha256']
        assert updated['revision']['parent_id']==record['revision']['id']
        assert updated['preview']['duration_s']==0.5
        assert client.post(prefix+'/confirmations',json=body).status_code==409
        versions=client.get(prefix+'/revisions').json()['items']
        assert [v['metadata']['sampling_rate_hz'] for v in versions]==[2048,1024]
        assert client.post(prefix+'/confirmations',json={'revision_id':updated['revision']['id']}).status_code==200


def test_concurrent_revisions_have_one_winner(database):
    with session(database) as client:
        record=imported(client)
        payload={'metadata':META,'reason':'Concurrent edit','expected_current_revision_id':record['revision']['id'],'row_version':1}
        cookies=dict(client.cookies);headers={'x-csrf-token':client.headers['x-csrf-token']}
        def revise(_):
            with TestClient(app,cookies=cookies,headers=headers) as other:
                return other.post(f"/api/v1/measurements/{record['id']}/revisions",json=payload).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(revise,range(2)))==[201,409]


def test_actual_routes_deny_peer_admin_and_cross_org(database):
    with session(database) as owner:
        record=imported(owner)
    for name,org in [('peer','org'),('admin','org'),('outsider','other_org')]:
        with session(database,name,org) as client:
            prefix=f"/api/v1/measurements/{record['id']}"
            for suffix in ('','/original','/revisions'):
                assert client.get(prefix+suffix).status_code==404
            assert record['id'] not in {r['id'] for r in client.get('/api/v1/measurements').json()['items']}
            status=client.post(prefix+'/confirmations',json={'revision_id':record['revision']['id']}).status_code
            assert status==(403 if name=='admin' else 404)
            if name=='admin':
                assert upload(client).status_code==403


def test_asset_permission_revocation_and_historical_scope(database):
    associated=dict(META,asset_id=str(database['asset']),measurement_point_id=str(database['point']))
    with session(database) as client:
        record=imported(client,metadata=associated);prefix=f"/api/v1/measurements/{record['id']}"
        # Remove association via a new explicit revision, keeping the old version.
        response=client.post(prefix+'/revisions',json={'metadata':META,'reason':'Unlink asset',
            'expected_current_revision_id':record['revision']['id'],'row_version':1})
        assert response.status_code==201
        with transaction() as conn:
            conn.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:owner'),database)
        try:
            assert client.get(prefix).status_code==200  # Current unassociated owned input.
            versions=client.get(prefix+'/revisions').json()['items']
            assert len(versions)==1 and versions[0]['revision_no']==2
            assert upload(client,metadata=associated).status_code==404
        finally:
            with transaction() as conn:
                conn.execute(text('UPDATE asset_grants SET can_read=true,can_write=true WHERE user_id=:owner'),database)


@pytest.mark.parametrize('raw,metadata,code',[(b'value\n0\n',META,'CSV_TOO_SHORT'),
    (b'value\nNaN\n'+b'0\n'*1024,META,'CSV_INVALID_VALUE'),
    (RAW,{'sampling_rate_hz':1024},'METADATA_INVALID'),
    (RAW,dict(META,sampling_rate_hz=True),'METADATA_INVALID')])
def test_invalid_input_has_no_ready_rows_or_staged_files(database,raw,metadata,code):
    with session(database) as client:
        with transaction() as conn:
            before=conn.execute(text('SELECT count(*) FROM measurements')).scalar_one()
        response=upload(client,raw=raw,metadata=metadata)
        assert response.status_code==422 and response.json()['error']['code']==code
        assert response.json()['request_id']==response.headers['x-request-id']
        with transaction() as conn:
            assert conn.execute(text('SELECT count(*) FROM measurements')).scalar_one()==before
        assert not list(FileStore().root.rglob('*.part'))


def test_bad_integrity_and_missing_file_never_confirm(database):
    with session(database) as client:
        record=imported(client);prefix=f"/api/v1/measurements/{record['id']}"
        original=next((FileStore().root/'originals').rglob('*.csv'))
        original.write_bytes(b'value\n'+b'2\n'*1024)
        for suffix in ('','/original'):
            response=client.get(prefix+suffix)
            assert response.status_code==409 and response.json()['error']['code']=='FILE_INTEGRITY'
        assert client.post(prefix+'/confirmations',json={'revision_id':record['revision']['id']}).status_code==409
        original.unlink()
        assert client.get(prefix).json()['error']['code']=='FILE_MISSING'


def test_csrf_auth_and_import_capacity(database):
    with TestClient(app) as anonymous:
        assert upload(anonymous).status_code==401
    with session(database) as client:
        token=client.headers.pop('x-csrf-token')
        assert upload(client).status_code==403
        client.headers['x-csrf-token']=token
        from vibradiag.api.v1.measurements import _imports
        _imports.acquire();_imports.acquire()
        try:
            response=upload(client)
            assert response.status_code==429 and response.headers['retry-after']=='2'
        finally:
            _imports.release();_imports.release()


def test_raw_and_chunked_multipart_limits(database):
    with session(database) as client:
        # Real per-file boundary rejects before parse/DB write (whole body remains bounded).
        response=upload(client,raw=b'x'*(MAX_FILE_BYTES+1))
        assert response.status_code==413
        assert not list(FileStore().root.rglob('*.part'))
        from vibradiag.api.upload_limits import BODY_LIMIT
        def body():
            yield b'--large\r\nContent-Disposition: form-data; name="file"; filename="x.csv"\r\n\r\n'
            for _ in range(BODY_LIMIT//65536+2):
                yield b'x'*65536
            yield b'\r\n--large--\r\n'
        response=client.post('/api/v1/measurements',content=body(),headers={'content-type':'multipart/form-data; boundary=large'})
        assert response.status_code==413,response.text


def test_storage_failure_rolls_back_and_commit_failure_leaves_only_orphan(database,monkeypatch):
    with session(database) as client:
        from vibradiag.services import measurement_service
        with transaction() as conn:
            before=conn.execute(text('SELECT count(*) FROM measurements')).scalar_one()
        def fail(*args,**kwargs):
            raise OSError('Injected private disk path should not be returned')
        original=FileStore.promote
        monkeypatch.setattr(FileStore,'promote',fail)
        response=upload(client)
        assert response.status_code==503 and 'Injected' not in response.text
        monkeypatch.setattr(FileStore,'promote',original)
        def fail_insert(*args,**kwargs):
            raise OSError('Injected after publication')
        monkeypatch.setattr(measurement_service,'insert_revision',fail_insert)
        assert upload(client).status_code==503
        with transaction() as conn:
            assert conn.execute(text('SELECT count(*) FROM measurements')).scalar_one()==before
        assert len(list((FileStore().root/'originals').rglob('*.csv')))==1
        assert not list(FileStore().root.rglob('*.part'))
        from vibradiag.services.file_reconciliation import reconcile_files
        with transaction() as conn:
            report=reconcile_files(conn)
        assert len(report['unreferenced_originals'])==1 and report['deleted_files']==0


def test_permissions_rechecked_after_csv_validation(database,monkeypatch):
    with session(database) as client:
        from vibradiag.services import measurement_service
        parse=measurement_service.parse_csv
        def revoke_then_parse(raw):
            parsed=parse(raw)
            with transaction() as conn:
                conn.execute(text("UPDATE users SET status='disabled' WHERE id=:owner"),database)
            return parsed
        monkeypatch.setattr(measurement_service,'parse_csv',revoke_then_parse)
        try:
            assert upload(client).status_code==401
            assert not list((FileStore().root/'originals').rglob('*.csv'))
            assert not list(FileStore().root.rglob('*.part'))
        finally:
            with transaction() as conn:
                conn.execute(text("UPDATE users SET status='active' WHERE id=:owner"),database)


def test_explicit_record_grant_and_revoke_on_actual_routes(database):
    with session(database) as owner:
        record=imported(owner)
    params=dict(database,measurement=UUID(record['id']))
    with transaction() as conn:
        conn.execute(text('INSERT INTO measurement_grants VALUES (:org,:measurement,:peer,true,true,:owner)'),params)
    with session(database,'peer') as peer:
        prefix=f"/api/v1/measurements/{record['id']}"
        assert peer.get(prefix).status_code==200
        assert peer.get(prefix+'/original').content==RAW
        assert peer.post(prefix+'/confirmations',json={'revision_id':record['revision']['id']}).status_code==200
        with transaction() as conn:
            conn.execute(text('DELETE FROM measurement_grants WHERE measurement_id=:measurement AND user_id=:peer'),params)
        for suffix in ('','/original','/revisions'):
            assert peer.get(prefix+suffix).status_code==404
