from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import io
import json
import math
from threading import Barrier, Event, Lock
from uuid import UUID,uuid4

from fastapi.testclient import TestClient
import numpy as np
import pytest
from sqlalchemy import text

from vibradiag.infrastructure.persistence.analysis_store import json_bytes
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.main import app
from vibradiag.schemas.analyses import AnalysisRequest
from vibradiag.services import persistent_analysis_service as service
from vibradiag.services.analysis_runtime import AnalysisRuntime, runtime


@pytest.fixture(autouse=True)
def isolated_files(database,monkeypatch,tmp_path):
    monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'analysis-data'))
    from vibradiag.api.v1.identity import _attempts
    _attempts.clear()


def session(database,name='owner',org='org'):
    client=TestClient(app)
    response=client.post('/api/v1/auth/login',json=dict(organization_id=str(database[org]),username=name,password=database['password']))
    assert response.status_code==200,response.text
    client.headers['x-csrf-token']=response.json()['csrf_token']
    return client


def input_record(client,*,unit='m/s2',asset=None):
    raw=('value\n'+'\n'.join(str(2*math.sin(2*math.pi*64*i/1024)) for i in range(1024))+'\n').encode()
    metadata={'sampling_rate_hz':1024,'acceleration_unit':unit,'source_type':'synthetic'}
    if asset:
        metadata['asset_id']=str(asset)
    uploaded=client.post('/api/v1/measurements',files={'file':('test.csv',raw,'text/csv')},data={'metadata':json.dumps(metadata)})
    assert uploaded.status_code==201,uploaded.text
    record=uploaded.json()
    confirmed=client.post(f"/api/v1/measurements/{record['id']}/confirmations",json={'revision_id':record['revision']['id']})
    assert confirmed.status_code==200,confirmed.text
    return record,{'measurement_revision_id':record['revision']['id'],'confirmation_id':confirmed.json()['id'],'config_profile':'basic-v1'}


def analyze(client,payload,key=None):
    return client.post('/api/v1/analyses',json=payload,headers={'Idempotency-Key':key or str(uuid4())})


def completed(client,payload,key=None):
    response=analyze(client,payload,key)
    assert response.status_code==201,response.text
    assert response.json()['status']=='completed',response.text
    return response.json()


def test_analysis_metrics_artifacts_series_and_idempotency(database):
    with session(database) as client:
        record,payload=input_record(client)
        key=str(uuid4());result=completed(client,payload,key)
        prefix='/api/v1/analyses/'+result['analysis_id']
        assert result['metrics']['rms']['value']==pytest.approx(math.sqrt(2),rel=1e-9)
        assert result['input_snapshot']['input_sha256']==record['sha256']
        assert len(result['artifacts'])==4 and 'spectrum' not in result['result_summary']
        assert client.get(prefix).json()['metrics']==result['metrics']
        repeated=analyze(client,payload,key)
        assert repeated.status_code==200 and repeated.json()['analysis_id']==result['analysis_id']
        assert client.get(prefix+'/series').json()['unit']=='m/s2'
        spectrum=client.get(prefix+'/series?kind=spectrum').json()
        assert len(spectrum['frequency_hz'])<=2000
        binary=client.get(prefix+'/artifacts/amplitude_spectrum')
        assert binary.status_code==200
        with np.load(io.BytesIO(binary.content),allow_pickle=False) as data:
            assert data['amplitude_m_s2'][64]==pytest.approx(2,rel=1e-8)
        changed=dict(payload,retry_of=str(uuid4()))
        assert analyze(client,changed,key).status_code==409


def test_current_revision_confirmation_and_old_result_history(database):
    with session(database) as client:
        record,payload=input_record(client)
        result=completed(client,payload)
        response=client.post(f"/api/v1/measurements/{record['id']}/revisions",json={
            'metadata':{'sampling_rate_hz':2048,'acceleration_unit':'m/s2'},'reason':'New parameter version',
            'row_version':1,'expected_current_revision_id':record['revision']['id']})
        assert response.status_code==201
        assert analyze(client,payload).status_code==409
        reread=client.get('/api/v1/analyses/'+result['analysis_id']).json()
        assert not reread['is_current_revision']
        assert reread['metrics']==result['metrics'] and reread['input_snapshot']==result['input_snapshot']
        assert analyze(client,dict(payload,measurement_revision_id=response.json()['revision']['id'])).status_code==409


def test_no_confirmation_or_foreign_confirmation_cannot_run(database):
    with session(database) as client:
        _,payload=input_record(client)
        assert analyze(client,dict(payload,confirmation_id=str(uuid4()))).status_code==409
        assert client.post('/api/v1/analyses',json=payload).status_code==422
        assert analyze(client,payload,' ').status_code==422


def test_read_series_and_artifacts_enforce_actual_scope(database):
    with session(database) as client:
        record,payload=input_record(client,asset=database['asset'])
        result=completed(client,payload);prefix='/api/v1/analyses/'+result['analysis_id']
        with transaction() as conn:
            conn.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:owner'),database)
        try:
            for suffix in ('','/series','/artifacts/result'):
                assert client.get(prefix+suffix).status_code==404
        finally:
            with transaction() as conn:
                conn.execute(text('UPDATE asset_grants SET can_read=true,can_write=true WHERE user_id=:owner'),database)
    for name,org in [('peer','org'),('admin','org'),('outsider','other_org')]:
        with session(database,name,org) as client:
            for suffix in ('','/series','/artifacts/result'):
                assert client.get(prefix+suffix).status_code==404


def test_artifact_and_input_corruption_stop_result_use(database):
    with session(database) as client:
        record,payload=input_record(client);result=completed(client,payload)
        prefix='/api/v1/analyses/'+result['analysis_id']
        with transaction() as conn:
            key=conn.execute(text("SELECT storage_key FROM analysis_artifacts WHERE analysis_id=:id AND kind='result'"),dict(id=UUID(result['analysis_id']))).scalar_one()
        path=FileStore().resolve(key);original=path.read_bytes();path.write_bytes(b'changed')
        for suffix in ('','/series','/artifacts/amplitude_spectrum'):
            response=client.get(prefix+suffix)
            assert response.status_code==409 and response.json()['error']['code']=='ARTIFACT_INTEGRITY'
        path.write_bytes(original)
        source=next((FileStore().root/'originals').rglob('*.csv'));source.unlink()
        assert client.get(prefix).json()['error']['code']=='FILE_MISSING'


def test_duplicate_while_running_returns_202_and_capacity_429(database,monkeypatch):
    entered=Event();release=Event();compute=service.compute_basic
    def slow(*args):
        entered.set()
        assert release.wait(timeout=15)
        return compute(*args)
    monkeypatch.setattr(service,'compute_basic',slow)
    with session(database) as client:
        _,payload=input_record(client);key=str(uuid4())
        cookies=dict(client.cookies);headers={'x-csrf-token':client.headers['x-csrf-token']}
        def run():
            with TestClient(app,cookies=cookies,headers=headers) as other:
                return analyze(other,payload,key)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(run)
            assert entered.wait(timeout=10)
            try:
                response=analyze(client,payload,key)
                assert response.status_code==202 and response.json()['status']=='running'
                service._capacity.acquire()
                try:
                    assert analyze(client,payload).status_code==429
                finally:
                    service._capacity.release()
            finally:
                release.set()
            response=future.result(timeout=15)
            assert response.status_code==201 and response.json()['status']=='completed'
            assert analyze(client,payload,key).json()['analysis_id']==response.json()['analysis_id']


def test_storage_failure_is_terminal_and_explicit_retry_new_id(database,monkeypatch):
    with session(database) as client:
        _,payload=input_record(client);key=str(uuid4())
        original=service.AnalysisStore.publish
        def fail(*args,**kwargs):
            raise OSError('Injected private path')
        monkeypatch.setattr(service.AnalysisStore,'publish',fail)
        failed=analyze(client,payload,key)
        assert failed.status_code==201 and failed.json()['status']=='failed'
        assert failed.json()['error_code']=='STORAGE_UNAVAILABLE' and 'Injected' not in failed.text
        assert analyze(client,payload,key).json()['analysis_id']==failed.json()['analysis_id']
        monkeypatch.setattr(service.AnalysisStore,'publish',original)
        retried=completed(client,dict(payload,retry_of=failed.json()['analysis_id']))
        assert retried['analysis_id']!=failed.json()['analysis_id']


def test_revoke_during_compute_never_completes(database,monkeypatch):
    with session(database) as client:
        _,payload=input_record(client);compute=service.compute_basic
        def revoke(*args):
            output=compute(*args)
            with transaction() as conn:
                conn.execute(text("UPDATE users SET status='disabled' WHERE id=:owner"),database)
            return output
        monkeypatch.setattr(service,'compute_basic',revoke)
        try:
            assert analyze(client,payload).status_code==401
            with transaction() as conn:
                row=conn.execute(text('SELECT status,error_code FROM analysis_runs WHERE measurement_revision_id=:id'),
                    dict(id=UUID(payload['measurement_revision_id']))).one()
            assert row.status=='failed' and row.error_code=='AUTH_REQUIRED'
        finally:
            with transaction() as conn:
                conn.execute(text("UPDATE users SET status='active' WHERE id=:owner"),database)


def test_single_process_lease_and_startup_recovery(database):
    with session(database) as client:
        with pytest.raises(RuntimeError,match='one API process'):
            AnalysisRuntime().start()
    identifier=uuid4();key=str(uuid4())
    payload=AnalysisRequest(measurement_revision_id=database['revision'],confirmation_id=database['confirmation'])
    with transaction() as conn:
        conn.execute(text("""INSERT INTO analysis_runs(id,organization_id,measurement_revision_id,confirmation_id,requested_by,
            status,algorithm_version,config_hash,config_snapshot) VALUES (:id,:org,:revision,:confirmation,:owner,'running','test',:hash,'{}')"""),
            dict(database,id=identifier,hash='a'*64))
        conn.execute(text("""INSERT INTO idempotency_records(organization_id,actor_id,operation,key_hash,request_hash,resource_id,state)
            VALUES (:org,:owner,'basic-analysis',:key,:request,:id,'running')"""),dict(database,id=identifier,
            key=sha256(key.encode()).hexdigest(),request=sha256(json_bytes(payload.model_dump(mode='json'))).hexdigest()))
    with session(database) as client:
        response=client.get('/api/v1/analyses/'+str(identifier))
        assert response.status_code==200 and response.json()['status']=='interrupted'
        assert response.json()['error_code']=='PROCESS_INTERRUPTED'
        repeated=analyze(client,payload.model_dump(mode='json'),key)
        assert repeated.status_code==200 and repeated.json()['status']=='interrupted'


def test_confirmation_revoked_during_compute_cannot_complete(database,monkeypatch):
    with session(database) as client:
        _,payload=input_record(client);compute=service.compute_basic
        def revoke(*args):
            output=compute(*args)
            with transaction() as conn:
                conn.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(payload['confirmation_id'])))
            return output
        monkeypatch.setattr(service,'compute_basic',revoke)
        response=analyze(client,payload)
        assert response.status_code==201 and response.json()['status']=='failed'
        assert response.json()['error_code']=='CONFIRMATION_REVOKED'


def test_simultaneous_same_key_creates_exactly_one_run(database,monkeypatch):
    counter=[0];guard=Lock();barrier=Barrier(2);compute=service.compute_basic
    def counted(*args):
        with guard:
            counter[0]+=1
        return compute(*args)
    monkeypatch.setattr(service,'compute_basic',counted)
    with session(database) as client:
        _,payload=input_record(client);key=str(uuid4());cookies=dict(client.cookies)
        headers={'x-csrf-token':client.headers['x-csrf-token']}
        def run(_):
            with TestClient(app,cookies=cookies,headers=headers) as other:
                barrier.wait(timeout=10)
                return analyze(other,payload,key)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(run,range(2)))
        assert sorted(r.status_code for r in responses) in ([200,201],[201,202])
        assert len({r.json()['analysis_id'] for r in responses})==1 and counter[0]==1


def test_parameter_change_during_compute_keeps_accepted_snapshot(database,monkeypatch):
    with session(database) as client:
        record,payload=input_record(client);compute=service.compute_basic
        def revise(*args):
            changed=client.post(f"/api/v1/measurements/{record['id']}/revisions",json={
                'metadata':{'sampling_rate_hz':2048,'acceleration_unit':'m/s2'},'reason':'While computing',
                'row_version':1,'expected_current_revision_id':record['revision']['id']})
            assert changed.status_code==201,changed.text
            return compute(*args)
        monkeypatch.setattr(service,'compute_basic',revise)
        result=completed(client,payload)
        assert result['measurement_revision_id']==payload['measurement_revision_id'] and not result['is_current_revision']
        assert result['result_summary']['sampling_rate_hz']==1024


def test_input_hash_failure_and_invalid_numeric_range_are_failed_resources(database):
    with session(database) as client:
        _,payload=input_record(client)
        source=next((FileStore().root/'originals').rglob('*.csv'));source.write_bytes(b'changed')
        result=analyze(client,payload)
        assert result.status_code==201 and result.json()['status']=='failed'
        assert result.json()['error_code']=='FILE_INTEGRITY'
        raw=b'value\n'+b'1e308\n'*1024
        upload=client.post('/api/v1/measurements',files={'file':('huge.csv',raw,'text/csv')},
            data={'metadata':json.dumps({'sampling_rate_hz':1024,'acceleration_unit':'g'})})
        assert upload.status_code==201,upload.text
        record=upload.json()
        confirmation=client.post(f"/api/v1/measurements/{record['id']}/confirmations",json={'revision_id':record['revision']['id']})
        assert confirmation.status_code==200
        result=analyze(client,dict(measurement_revision_id=record['revision']['id'],confirmation_id=confirmation.json()['id']))
        assert result.status_code==201 and result.json()['error_code']=='NUMERIC_RANGE_UNSUPPORTED'


def test_lease_loss_refuses_new_work_without_reconnecting(database):
    with session(database) as client:
        _,payload=input_record(client)
        with runtime.guard:
            runtime.healthy=False
        try:
            response=analyze(client,payload)
            assert response.status_code==503
        finally:
            with runtime.guard:
                runtime.healthy=True


def test_paginated_analysis_history_obeys_record_scope(database):
    with session(database) as client:
        record,payload=input_record(client)
        first=completed(client,payload);second=completed(client,payload)
        path=f"/api/v1/analyses?measurement_id={record['id']}&page_size=1"
        listed=client.get(path)
        assert listed.status_code==200 and listed.json()['items'][0]['analysis_id']==second['analysis_id']
        assert client.get(path+'&page=2').json()['items'][0]['analysis_id']==first['analysis_id']
        assert client.get(path+'&page=3').json()['items']==[]
    with session(database,'peer') as peer:
        assert peer.get(path).status_code==404
