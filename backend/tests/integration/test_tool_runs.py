"""Actual API + dedicated PostgreSQL + isolated filesystem, no model calls."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import io
import json
from threading import Event
from uuid import UUID, uuid4
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from vibradiag.main import app
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.infrastructure.persistence.tool_store import ToolStore
from vibradiag.services import tool_run_service as service
from vibradiag.services.tool_profile_service import create_profile
from vibradiag.services.diagnostic_context_service import create_component, create_bearing_revision, create_context
from vibradiag.services.identity_service import authenticate

SOURCE = dict(kind='synthetic_exact',reference='Analytic AM and Z8 d10 D40 fixture; not field data',version='1')


def login(data, name='peer', org='org'):
    client = TestClient(app)
    response = client.post('/api/v1/auth/login',json=dict(organization_id=str(data[org]),username=name,password=data['password']))
    assert response.status_code == 200, response.text
    client.headers['x-csrf-token'] = response.json()['csrf_token']
    return client


@pytest.fixture
def case(database, monkeypatch, tmp_path):
    monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'data'))
    from vibradiag.api.v1.identity import _attempts
    _attempts.clear()
    with transaction() as conn:
        conn.execute(text('''INSERT INTO asset_grants VALUES (:org,:peer,:asset,true,true)
            ON CONFLICT (user_id,asset_id) DO UPDATE SET can_read=true,can_write=true'''),database)
    with login(database) as client:
        t=np.arange(48322)/12000
        signal=2*(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
        raw=('value\n'+'\n'.join(format(x,'.17g') for x in signal)+'\n').encode()
        metadata=dict(sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(database['asset']),
            measurement_point_id=str(database['point']),rpm=1800,source_type='synthetic')
        response=client.post('/api/v1/measurements',files={'file':('am.csv',raw,'text/csv')},data={'metadata':json.dumps(metadata)})
        assert response.status_code==201,response.text
        measurement=response.json()
        revision=measurement['revision']['id']
        confirmation=client.post(f"/api/v1/measurements/{measurement['id']}/confirmations",json={'revision_id':revision}).json()['id']
        with transaction() as conn:
            principal=authenticate(conn,client.cookies.get('vibradiag_session'))
        component=create_component(dict(asset_id=database['asset'],component_code=str(uuid4()),name='Synthetic target'),principal)
        bearing=create_bearing_revision(dict(component_id=component,expected_current_revision_id=None,parameterization='geometry',
            geometry=dict(rolling_elements=8,rolling_element_diameter=10.,pitch_diameter=40.,length_unit='mm',contact_angle_deg=0.,
                          bearing_design='single_row_ball',source=SOURCE),reason='Analytic fixture'),principal)
        context=create_context(dict(measurement_revision_id=revision,component_id=component,bearing_revision_id=bearing,
            conditions=dict(rpm_basis='synthetic_exact',rpm_source=SOURCE,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=SOURCE,
                speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),
            acquisition=dict(low_hz=100.,high_hz=5000.,source=SOURCE),source_reference=SOURCE,reason='Fixture attestation'),principal)
        profile=create_profile(dict(asset_id=database['asset'],sampling_rate_hz=12000,band_low_hz=2000,band_high_hz=4000,
            modulation_low_hz=10,modulation_high_hz=500,selection_reason='Fixed AM numerical test'),principal)
        yield dict(database,client=client,principal=principal,measurement=measurement,revision=revision,confirmation=confirmation,
            context=context,profile=profile,raw=raw,component=component,bearing=bearing)


def payload(case, tool='analyze_envelope'):
    result=dict(tool_name=tool,measurement_revision_id=case['revision'],confirmation_id=case['confirmation'],context_id=str(case['context']))
    if tool=='analyze_envelope':
        result.update(profile_id=str(case['profile']),mode='exploratory')
    return result


def run(case, body=None, key=None):
    return case['client'].post('/api/v1/tool-runs',json=body or payload(case),headers={'Idempotency-Key':key or str(uuid4())})


def completed(case, body=None):
    response=run(case,body)
    assert response.status_code==201,response.text
    result=response.json()
    assert result['status']=='completed',result
    return result


def test_envelope_api_snapshot_series_artifacts_and_list(case):
    result=completed(case); identifier=result['tool_execution_id']; prefix='/api/v1/tool-runs/'+identifier
    assert result['result_status']=='ok'
    assert {a['kind'] for a in result['artifacts']}=={'result','arrays','manifest'}
    assert result['input_snapshot']['input_sha256']==sha256(case['raw']).hexdigest()
    assert result['result']['summary']['effective_segment']['sample_count']==48000
    assert result['result']['summary']['evidence_preconditions_satisfied'] is False
    assert result['result']['summary']['parameters']['qualification'] is None
    assert case['client'].get(prefix).json()==result
    preview=case['client'].get(prefix+'/series').json()
    assert len(preview['values'])<=2000 and preview['original_indices'][0]==161
    spectrum=case['client'].get(prefix+'/series?kind=spectrum').json()
    assert len(spectrum['frequency_hz'])<=2000 and spectrum['display_only']
    response=case['client'].get(prefix+'/artifacts/arrays')
    with np.load(io.BytesIO(response.content),allow_pickle=False) as arrays:
        index=np.argmin(abs(arrays['frequency_hz']-120))
        assert arrays['amplitude_m_s2'][index]==pytest.approx(.5,abs=.00501)
    assert case['client'].get(prefix+'/artifacts/../../original').status_code in {404,422}
    items=case['client'].get('/api/v1/tool-runs',params={'measurement_id':case['measurement']['id']}).json()['items']
    assert [r['tool_execution_id'] for r in items]==[identifier]


def test_frequency_api_has_no_fake_spectrum(case):
    result=completed(case,payload(case,'calculate_bearing_frequencies'))
    assert result['result']['summary']['frequencies']['BPFO']['frequency_hz']==90
    assert {a['kind'] for a in result['artifacts']}=={'result','manifest'}
    prefix='/api/v1/tool-runs/'+result['tool_execution_id']
    assert case['client'].get(prefix+'/series').status_code==409
    assert case['client'].get(prefix+'/artifacts/arrays').status_code==409


def test_diagnostic_mode_unavailable_without_qualification(case):
    body=payload(case); body['mode']='diagnostic_evidence'
    result=completed(case,body)
    assert result['result_status']=='unavailable'
    assert 'qualified_numeric_profile' in result['result']['missing_requirements']
    assert {a['kind'] for a in result['artifacts']}=={'result','manifest'}


def test_envelope_does_not_require_context_or_rpm(case):
    body=payload(case); body.pop('context_id')
    assert completed(case,body)['result_status']=='ok'


def test_same_key_response_loss_replay_and_conflict(case):
    key=str(uuid4()); first=run(case,key=key)
    assert first.status_code==201
    replay=run(case,key=key)
    assert replay.status_code==200 and replay.json()['tool_execution_id']==first.json()['tool_execution_id']
    changed=payload(case); changed['mode']='diagnostic_evidence'
    assert run(case,changed,key).status_code==409
    with transaction() as conn:
        assert conn.execute(text('SELECT count(*) FROM tool_runs WHERE measurement_revision_id=:revision'),dict(revision=UUID(case['revision']))).scalar_one()==1


@pytest.mark.parametrize('extra',[{'rpm':900},{'qualification':{}},{'checked':True},{'band_low_hz':100},{'storage_key':'x'}])
def test_public_request_forbids_overrides(case,extra):
    response=run(case,dict(payload(case),**extra))
    assert response.status_code==422
    assert response.json()['error']['code']=='REQUEST_INVALID'
    assert response.json()['request_id']==response.headers['x-request-id']


def test_auth_csrf_and_role_scope(case):
    result=completed(case); prefix='/api/v1/tool-runs/'+result['tool_execution_id']
    with TestClient(app) as anonymous:
        assert anonymous.get(prefix).status_code==401
    for name,org in [('owner','org'),('admin','org'),('outsider','other_org')]:
        with login(case,name,org) as other:
            assert other.get(prefix).status_code==404
            assert other.get(prefix+'/artifacts/result').status_code==404
    csrf=case['client'].headers.pop('x-csrf-token')
    try:
        assert run(case).status_code==403
    finally:
        case['client'].headers['x-csrf-token']=csrf


def test_completed_artifact_corruption_blocks_read_download_and_replay(case):
    key=str(uuid4()); response=run(case,key=key); assert response.status_code==201,response.text
    identifier=response.json()['tool_execution_id']
    path=next((FileStore().root/'tools').rglob('arrays.npz')); path.write_bytes(b'corrupt')
    for suffix in ('','/series','/artifacts/result'):
        result=case['client'].get('/api/v1/tool-runs/'+identifier+suffix)
        assert result.status_code==409 and result.json()['error']['code']=='ARTIFACT_INTEGRITY'
    assert run(case,key=key).status_code==409


def test_profile_immutable_and_cannot_self_qualify(case):
    with pytest.raises(DBAPIError),transaction() as conn:
        conn.execute(text("UPDATE tool_profiles SET qualification_status='qualified' WHERE id=:id"),dict(id=case['profile']))
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        create_profile(dict(asset_id=case['asset'],sampling_rate_hz=12000,band_low_hz=2000,band_high_hz=4000,
            modulation_low_hz=10,modulation_high_hz=500,selection_reason='test',qualification_status='qualified'),case['principal'])


def test_completed_run_and_artifact_immutable_and_incomplete_completion_rejected(case):
    result=completed(case); identifier=UUID(result['tool_execution_id'])
    for query in ["UPDATE tool_runs SET result_metadata='{}' WHERE id=:id",'DELETE FROM tool_runs WHERE id=:id',
                  "UPDATE tool_artifacts SET sha256=repeat('a',64) WHERE tool_run_id=:id"]:
        with pytest.raises(DBAPIError),transaction() as conn:
            conn.execute(text(query),dict(id=identifier))
    with pytest.raises(DBAPIError),transaction() as conn:
        conn.execute(text('''INSERT INTO tool_runs
            (id,organization_id,measurement_revision_id,confirmation_id,context_id,profile_id,requested_by,
             tool_name,tool_version,status,input_fingerprint,input_snapshot,result_status,result_metadata,finished_at)
            SELECT :id,organization_id,measurement_revision_id,confirmation_id,context_id,profile_id,requested_by,
             tool_name,tool_version,'completed',input_fingerprint,input_snapshot,'ok',result_metadata,now()
             FROM tool_runs WHERE id=:old'''),dict(id=uuid4(),old=identifier))


def test_shared_m1_and_m2_capacity_429(case):
    from vibradiag.services.persistent_analysis_service import _capacity
    _capacity.acquire(); _capacity.acquire()
    try:
        response=run(case)
        assert response.status_code==429 and response.json()['error']['code']=='ANALYSIS_CAPACITY'
    finally:
        _capacity.release(); _capacity.release()


def test_concurrent_same_key_running_replay_and_single_resource(case,monkeypatch):
    original=service.analyze_envelope; entered=Event(); release=Event(); key=str(uuid4())
    def blocked(*args):
        entered.set(); assert release.wait(15)
        return original(*args)
    monkeypatch.setattr(service,'analyze_envelope',blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(run,case,None,key)
        try:
            assert entered.wait(10)
            replay=run(case,key=key)
            assert replay.status_code==202 and replay.json()['status']=='running'
        finally:
            release.set()
        first=future.result()
    assert first.status_code==201,first.text
    assert first.json()['tool_execution_id']==replay.json()['tool_execution_id']


def test_revocation_during_compute_prevents_completion_and_leaves_reconcilable_orphan(case,monkeypatch):
    original=service.analyze_envelope
    def revoked(*args):
        result=original(*args)
        with transaction() as conn:
            conn.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(case['confirmation'])))
        return result
    monkeypatch.setattr(service,'analyze_envelope',revoked)
    result=run(case)
    assert result.status_code==201 and result.json()['status']=='failed'
    assert result.json()['error_code']=='CONFIRMATION_REVOKED' and not result.json()['artifacts']
    from vibradiag.services.file_reconciliation import reconcile_files
    with transaction() as conn:
        report=reconcile_files(conn)
    assert len(report['unreferenced_tool_files'])==3 and report['deleted_files']==0


def test_storage_failure_replay_and_explicit_retry(case,monkeypatch):
    original=ToolStore.publish
    def fail(*args,**kwargs):
        raise OSError('Private path must not be returned')
    monkeypatch.setattr(ToolStore,'publish',fail)
    key=str(uuid4()); first=run(case,key=key)
    assert first.status_code==201 and first.json()['status']=='failed'
    assert first.json()['error_code']=='STORAGE_UNAVAILABLE' and 'Private path' not in first.text
    monkeypatch.setattr(ToolStore,'publish',original)
    replay=run(case,key=key)
    assert replay.status_code==200 and replay.json()['tool_execution_id']==first.json()['tool_execution_id']
    body=payload(case); body['retry_of']=first.json()['tool_execution_id']
    retried=completed(case,body)
    assert retried['tool_execution_id']!=first.json()['tool_execution_id']


def test_startup_recovery_fences_old_completion_and_same_key_does_not_rerun(case,monkeypatch):
    original=service.analyze_envelope
    def interrupted(*args):
        from vibradiag.services.analysis_runtime import recover_interrupted
        assert recover_interrupted()>=1
        return original(*args)
    monkeypatch.setattr(service,'analyze_envelope',interrupted)
    key=str(uuid4()); response=run(case,key=key)
    assert response.status_code==201,response.text
    assert response.json()['status']=='interrupted' and not response.json()['artifacts']
    assert run(case,key=key).json()['tool_execution_id']==response.json()['tool_execution_id']


def test_wrong_revision_context_and_confirmation_rejected_before_acceptance(case):
    body=payload(case); body['measurement_revision_id']=str(case['revision'])
    body['confirmation_id']=str(uuid4())
    assert run(case,body).status_code==409
    body=payload(case); body['context_id']=str(uuid4())
    assert run(case,body).status_code==404


def test_permission_revoked_before_replay(case):
    key=str(uuid4()); first=run(case,key=key); assert first.status_code==201
    with transaction() as conn:
        conn.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer AND asset_id=:asset'),case)
    assert run(case,key=key).status_code==404
    assert case['client'].get('/api/v1/tool-runs/'+first.json()['tool_execution_id']).status_code==404


def test_scope_revoked_during_compute_never_publishes_completed_result(case,monkeypatch):
    original=service.analyze_envelope
    def revoke(*args):
        result=original(*args)
        with transaction() as conn:
            conn.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer AND asset_id=:asset'),case)
        return result
    monkeypatch.setattr(service,'analyze_envelope',revoke)
    response=run(case)
    assert response.status_code==404
    with transaction() as conn:
        row=conn.execute(text('SELECT status,error_code FROM tool_runs WHERE measurement_revision_id=:id'),dict(id=UUID(case['revision']))).one()
    assert row.status=='failed' and row.error_code=='RESOURCE_NOT_FOUND'


def test_commit_failure_after_publication_keeps_orphan_and_failed_idempotency(case,monkeypatch):
    original=service.audit
    def fail_completion(connection, org, actor, action, *args):
        if action=='tool.completed':
            raise OSError('Injected completion transaction rollback')
        return original(connection,org,actor,action,*args)
    monkeypatch.setattr(service,'audit',fail_completion)
    key=str(uuid4()); response=run(case,key=key)
    assert response.status_code==201 and response.json()['status']=='failed'
    assert not response.json()['artifacts']
    assert len(list((FileStore().root/'tools').rglob('*.*')))==3
    assert run(case,key=key).status_code==200


def test_running_snapshot_cannot_be_mutated_in_database(case,monkeypatch):
    original=service.analyze_envelope
    def check(*args):
        with pytest.raises(DBAPIError),transaction() as conn:
            conn.execute(text("UPDATE tool_runs SET input_snapshot='{}' WHERE measurement_revision_id=:id"),dict(id=UUID(case['revision'])))
        return original(*args)
    monkeypatch.setattr(service,'analyze_envelope',check)
    assert completed(case)['result_status']=='ok'


def test_stale_profile_sampling_rate_and_other_input_context_rejected(case):
    bad=create_profile(dict(asset_id=case['asset'],sampling_rate_hz=24000,band_low_hz=2000,band_high_hz=4000,
        modulation_low_hz=10,modulation_high_hz=500,selection_reason='Different acquisition rate'),case['principal'])
    body=payload(case); body['profile_id']=str(bad)
    response=run(case,body)
    assert response.status_code==409 and response.json()['error']['code']=='PROFILE_SAMPLING_MISMATCH'
    # A valid, owned and confirmed input cannot borrow a context from another revision.
    response=case['client'].post('/api/v1/measurements',files={'file':('other.csv',case['raw'],'text/csv')},
        data={'metadata':json.dumps(dict(sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(case['asset']),source_type='synthetic'))})
    other=response.json()
    confirmation=case['client'].post(f"/api/v1/measurements/{other['id']}/confirmations",json={'revision_id':other['revision']['id']}).json()['id']
    body=payload(case); body.update(measurement_revision_id=other['revision']['id'],confirmation_id=confirmation)
    response=run(case,body)
    assert response.status_code==409 and response.json()['error']['code']=='CONTEXT_INPUT_MISMATCH'


def test_historical_result_keeps_old_input_snapshot_after_revision(case):
    result=completed(case)
    response=case['client'].post(f"/api/v1/measurements/{case['measurement']['id']}/revisions",json=dict(
        metadata=dict(sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(case['asset']),rpm=900,source_type='synthetic'),
        reason='Correct speed for subsequent work',expected_current_revision_id=case['revision'],row_version=1))
    assert response.status_code==201,response.text
    read=case['client'].get('/api/v1/tool-runs/'+result['tool_execution_id']).json()
    assert not read['is_current_revision'] and read['input_snapshot']['revision']['rpm']==1800
    response=run(case)
    assert response.status_code==409 and response.json()['error']['code']=='REVISION_CONFLICT'
