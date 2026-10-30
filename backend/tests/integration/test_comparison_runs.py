import json
from uuid import UUID, uuid4
import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_tool_runs import case, login, SOURCE
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.infrastructure.persistence.tool_store import ToolStore
from vibradiag.services.diagnostic_context_service import create_context
from vibradiag.services.comparison_service import create_declaration
from vibradiag.services import tool_run_service, comparison_service


@pytest.fixture
def comparison(case):
    client=case['client']; before=case['measurement']
    common=dict(sampling_rate_hz=12000,acceleration_unit='m/s2',asset_id=str(case['asset']),measurement_point_id=str(case['point']),
        direction='horizontal_radial',rpm=1800.,load_value=100.,load_unit='Nm',source_type='synthetic')
    response=client.post(f"/api/v1/measurements/{before['id']}/revisions",json=dict(metadata=dict(common,acquired_at='2026-01-01T00:00:00Z'),
        reason='Add exact synthetic comparison metadata',expected_current_revision_id=before['current_revision_id'],row_version=1))
    assert response.status_code==201,response.text
    before=response.json()
    t=np.arange(48322)/12000
    x=(1+.25*np.cos(2*np.pi*120*t))*np.cos(2*np.pi*3000*t)
    raw=('value\n'+'\n'.join(format(v,'.17g') for v in x)+'\n').encode()
    response=client.post('/api/v1/measurements',files={'file':('after.csv',raw,'text/csv')},data={'metadata':json.dumps(dict(common,acquired_at='2026-01-01T00:01:00Z'))})
    assert response.status_code==201,response.text
    after=response.json(); confirmations={}; basics={}; contexts={}
    for name,record in [('before',before),('after',after)]:
        response=client.post(f"/api/v1/measurements/{record['id']}/confirmations",json={'revision_id':record['current_revision_id']})
        assert response.status_code==200,response.text
        confirmations[name]=response.json()['id']
        contexts[name]=create_context(dict(measurement_revision_id=record['current_revision_id'],component_id=case['component'],bearing_revision_id=case['bearing'],
            conditions=dict(rpm_basis='synthetic_exact',rpm_source=SOURCE,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=SOURCE,
                speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),
            acquisition=dict(low_hz=100.,high_hz=5000.,source=SOURCE),source_reference=SOURCE,reason='Synthetic comparison'),case['principal'])
        response=client.post('/api/v1/analyses',json=dict(measurement_revision_id=record['current_revision_id'],confirmation_id=confirmations[name]),headers={'Idempotency-Key':str(uuid4())})
        assert response.status_code==201 and response.json()['status']=='completed',response.text
        basics[name]=response.json()['analysis_id']
    chain=dict(configuration=dict(sensor_id='synthetic-generator',channel_id='synthetic-channel',daq_id='numpy',
        mounting_reference='no-physical-mount',calibration_reference='exact-mathematics-not-field-calibration',acquisition_configuration='synthetic-fs12000-v1'),source=SOURCE)
    sides={name:dict(provenance=dict(acquisition_id='fixture-'+name,root_acquisition_refs=['fixture-'+name],source=SOURCE),
        timing=dict(time_reference='start',source=SOURCE),load=dict(quantity='torque',reference='synthetic-shaft-torque',
            condition='constant_steady_checked',source=SOURCE),measurement_chain=chain) for name in ('before','after')}
    declaration=dict(before_context_id=str(contexts['before']),after_context_id=str(contexts['after']),before_basic_id=basics['before'],after_basic_id=basics['after'],
        **sides,order_source=SOURCE,load_tolerance=dict(absolute=0.,unit='Nm',quantity='torque',reference='synthetic-shaft-torque',source=SOURCE),reason='Exact synthetic comparison fixture')
    identifier=create_declaration(declaration,case['principal'])
    return dict(case,before_record=before,after_record=after,confirmations=confirmations,basics=basics,contexts=contexts,
        declaration=declaration,declaration_id=identifier,comparison_payload=dict(tool_name='compare_measurements',
            measurement_revision_id=after['current_revision_id'],confirmation_id=confirmations['after'],before_confirmation_id=confirmations['before'],declaration_id=str(identifier)))


def execute(data,key=None):
    return data['client'].post('/api/v1/tool-runs',json=data['comparison_payload'],headers={'Idempotency-Key':key or str(uuid4())})


def completed(data):
    response=execute(data); assert response.status_code==201,response.text
    assert response.json()['status']=='completed',response.json()
    return response.json()


def test_comparable_api_two_inputs_and_no_repair_or_evidence_claim(comparison):
    result=completed(comparison)
    assert result['result_status']=='ok' and result['result']['summary']['comparison_status']=='comparable'
    assert result['result']['summary']['metrics']['rms']['relative_change_percent']==pytest.approx(-50.,abs=1e-8)
    assert result['before_measurement_id']==comparison['before_record']['id']
    assert result['is_current_before_revision'] and result['is_current_revision'] and result['input_confirmation_active']
    assert result['result']['summary']['repair_effectiveness'] is None
    assert not result['fact_refs'] and not result['evidence_refs']
    assert {a['kind'] for a in result['artifacts']}=={'result','manifest'}
    assert comparison['client'].get('/api/v1/tool-runs/'+result['tool_execution_id']).status_code==200


def test_same_key_replays_same_two_input_resource(comparison):
    key=str(uuid4()); first=execute(comparison,key); assert first.status_code==201,first.text
    replay=execute(comparison,key)
    assert replay.status_code==200 and replay.json()['tool_execution_id']==first.json()['tool_execution_id']
    changed=dict(comparison['comparison_payload'],declaration_id=str(uuid4()))
    assert comparison['client'].post('/api/v1/tool-runs',json=changed,headers={'Idempotency-Key':key}).status_code==409


@pytest.mark.parametrize('extra',[{'checked':True},{'rpm':900},{'load_tolerance':{}},{'before_revision_id':str(uuid4())},{'order_checked':True}])
def test_public_request_cannot_override_comparison_sources(comparison,extra):
    body=dict(comparison['comparison_payload'],**extra)
    assert comparison['client'].post('/api/v1/tool-runs',json=body,headers={'Idempotency-Key':str(uuid4())}).status_code==422


def test_confirmation_must_belong_to_both_inputs_and_current_actor(comparison):
    comparison['comparison_payload']['before_confirmation_id']=comparison['confirmations']['after']
    assert execute(comparison).status_code==409


def test_missing_conditions_are_persisted_unavailable_with_no_changes(comparison):
    declaration=dict(comparison['declaration']);declaration['load_tolerance']=None
    comparison['comparison_payload']['declaration_id']=str(create_declaration(declaration,comparison['principal']))
    result=completed(comparison)
    assert result['result_status']=='unavailable' and result['result']['summary']['comparison_status']=='not_comparable'
    assert result['result']['summary']['metrics']=={}


def test_shared_declared_root_cannot_become_independent_before_after(comparison):
    declaration=json.loads(json.dumps(comparison['declaration']))
    declaration['after']['provenance']['root_acquisition_refs']=declaration['before']['provenance']['root_acquisition_refs']
    comparison['comparison_payload']['declaration_id']=str(create_declaration(declaration,comparison['principal']))
    result=completed(comparison)
    assert result['result']['summary']['comparison_status']=='related_or_same_acquisition'
    assert result['result']['summary']['metrics']=={}


def test_declaration_requires_engineer_and_forbids_self_checked_hashes(comparison):
    from pydantic import ValidationError
    with login(comparison,'owner') as other:
        from vibradiag.services.identity_service import authenticate
        with transaction() as conn: principal=authenticate(conn,other.cookies.get('vibradiag_session'))
        with pytest.raises(Exception) as error: create_declaration(comparison['declaration'],principal)
        assert getattr(error.value,'status',None)==403
    for key in ('checked','configuration_sha256'):
        declaration=json.loads(json.dumps(comparison['declaration']))
        declaration['before']['measurement_chain'][key]=True if key=='checked' else 'a'*64
        with pytest.raises(ValidationError): create_declaration(declaration,comparison['principal'])


def test_declaration_and_execution_bindings_are_immutable(comparison):
    result=completed(comparison)
    for sql,identifier in [('UPDATE comparison_declarations SET declaration=\'{}\' WHERE id=:id',comparison['declaration_id']),
        ('DELETE FROM comparison_declarations WHERE id=:id',comparison['declaration_id']),
        ('UPDATE tool_runs SET before_revision_id=NULL WHERE id=:id',UUID(result['tool_execution_id']))]:
        with pytest.raises(DBAPIError),transaction() as conn: conn.execute(text(sql),dict(id=identifier))


def test_parent_corruption_before_and_after_completion_blocks_access(comparison):
    result=completed(comparison)
    parent=FileStore().root/'analyses'/str(comparison['org'])/comparison['basics']['before']/'result.json'
    parent.write_bytes(b'corrupted-before-parent')
    assert comparison['client'].get('/api/v1/tool-runs/'+result['tool_execution_id']).status_code==409
    assert comparison['client'].get('/api/v1/tool-runs/'+result['tool_execution_id']+'/artifacts/result').status_code==409
    response=execute(comparison)
    assert response.status_code==201 and response.json()['status']=='failed'


def test_before_confirmation_revocation_marks_history_and_prevents_new_runs(comparison):
    result=completed(comparison)
    with transaction() as conn:
        conn.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(comparison['confirmations']['before'])))
    read=comparison['client'].get('/api/v1/tool-runs/'+result['tool_execution_id'])
    assert read.status_code==200 and not read.json()['input_confirmation_active']
    assert execute(comparison).status_code==409


def test_before_revision_change_marks_history_and_rejects_new_run(comparison):
    result=completed(comparison); before=comparison['before_record']
    response=comparison['client'].post('/api/v1/measurements/'+before['id']+'/revisions',json=dict(metadata=before['revision']['metadata'],
        expected_current_revision_id=before['current_revision_id'],row_version=before['row_version'],reason='New input version'))
    assert response.status_code==201,response.text
    read=comparison['client'].get('/api/v1/tool-runs/'+result['tool_execution_id'])
    assert read.status_code==200 and not read.json()['is_current_before_revision']
    assert execute(comparison).status_code==409


def test_access_to_after_only_does_not_leak_before_via_list_options_or_artifacts(comparison):
    result=completed(comparison)
    with transaction() as conn:
        for record in (comparison['before_record'],comparison['after_record']):
            conn.execute(text('INSERT INTO measurement_grants VALUES (:org,:measurement,:owner,true,false,:peer)'),dict(comparison,measurement=UUID(record['id'])))
    with login(comparison,'owner') as viewer:
        path='/api/v1/tool-runs?measurement_id='+comparison['after_record']['id']
        assert result['tool_execution_id'] in {r['tool_execution_id'] for r in viewer.get(path).json()['items']}
        with transaction() as conn:
            conn.execute(text('DELETE FROM measurement_grants WHERE measurement_id=:id AND user_id=:user'),dict(id=UUID(comparison['before_record']['id']),user=comparison['owner']))
        assert viewer.get(path).json()['items']==[]
        assert viewer.get('/api/v1/tool-runs/comparison-options/'+comparison['after_record']['id']).json()['items']==[]
        assert viewer.get('/api/v1/tool-runs/'+result['tool_execution_id']).status_code==404
        assert viewer.get('/api/v1/tool-runs/'+result['tool_execution_id']+'/artifacts/result').status_code==404


def test_shared_capacity_and_interruption_do_not_publish_comparison(comparison,monkeypatch):
    from vibradiag.services.compute_capacity import capacity
    capacity.acquire();capacity.acquire()
    try: assert execute(comparison).status_code==429
    finally: capacity.release();capacity.release()
    original=comparison_service.compute
    def interrupted(*args):
        from vibradiag.services.analysis_runtime import recover_interrupted
        recover_interrupted();return original(*args)
    monkeypatch.setattr(comparison_service,'compute',interrupted)
    response=execute(comparison); assert response.status_code==201,response.text
    assert response.json()['status']=='interrupted' and not response.json()['artifacts']


def test_revocation_during_compute_cannot_complete(comparison,monkeypatch):
    original=comparison_service.compute
    def revoked(*args):
        result=original(*args)
        with transaction() as conn:
            conn.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(comparison['confirmations']['before'])))
        return result
    monkeypatch.setattr(comparison_service,'compute',revoked)
    response=execute(comparison); assert response.status_code==201,response.text
    assert response.json()['status']=='failed' and not response.json()['artifacts']


def test_storage_failure_same_key_and_explicit_retry(comparison,monkeypatch):
    original=ToolStore.publish
    monkeypatch.setattr(ToolStore,'publish',lambda *args:(_ for _ in ()).throw(OSError('Injected private path')))
    key=str(uuid4()); response=execute(comparison,key)
    assert response.status_code==201 and response.json()['status']=='failed'
    monkeypatch.setattr(ToolStore,'publish',original)
    assert execute(comparison,key).json()['tool_execution_id']==response.json()['tool_execution_id']
    comparison['comparison_payload']['retry_of']=response.json()['tool_execution_id']
    assert completed(comparison)['tool_execution_id']!=response.json()['tool_execution_id']


def test_optional_full_envelope_parents_compare_in_si(comparison):
    declaration=dict(comparison['declaration'])
    for name,record in [('before',comparison['before_record']),('after',comparison['after_record'])]:
        response=comparison['client'].post('/api/v1/tool-runs',json=dict(tool_name='analyze_envelope',
            measurement_revision_id=record['current_revision_id'],confirmation_id=comparison['confirmations'][name],
            context_id=str(comparison['contexts'][name]),profile_id=str(comparison['profile'])),headers={'Idempotency-Key':str(uuid4())})
        assert response.status_code==201 and response.json()['result_status']=='ok',response.text
        declaration[name+'_envelope_id']=response.json()['tool_execution_id']
    comparison['comparison_payload']['declaration_id']=str(create_declaration(declaration,comparison['principal']))
    result=completed(comparison)
    assert result['result']['summary']['metrics']['envelope_rms']['relative_change_percent']==pytest.approx(-50.,abs=1e-8)


def test_real_duplicate_upload_detected_even_with_distinct_declared_roots(comparison):
    response=comparison['client'].post('/api/v1/measurements',files={'file':('copy.csv',comparison['raw'],'text/csv')},
        data={'metadata':json.dumps(comparison['after_record']['revision']['metadata'])})
    assert response.status_code==201,response.text
    record=response.json();revision=record['current_revision_id']
    confirm=comparison['client'].post('/api/v1/measurements/'+record['id']+'/confirmations',json={'revision_id':revision}).json()['id']
    context=create_context(dict(measurement_revision_id=revision,component_id=comparison['component'],bearing_revision_id=comparison['bearing'],
        conditions=dict(rpm_basis='synthetic_exact',rpm_source=SOURCE,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=SOURCE,
            speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),acquisition=dict(low_hz=100.,high_hz=5000.,source=SOURCE),
        source_reference=SOURCE,reason='Explicit duplicate fixture'),comparison['principal'])
    basic=comparison['client'].post('/api/v1/analyses',json=dict(measurement_revision_id=revision,confirmation_id=confirm),headers={'Idempotency-Key':str(uuid4())}).json()
    declaration=dict(comparison['declaration'],after_context_id=str(context),after_basic_id=basic['analysis_id'])
    comparison['comparison_payload'].update(declaration_id=str(create_declaration(declaration,comparison['principal'])),measurement_revision_id=revision,confirmation_id=confirm)
    result=completed(comparison)
    assert result['result']['summary']['comparison_status']=='related_or_same_acquisition'
    assert 'identical_normalized_samples' in result['result']['summary']['related_reasons']


def test_foreign_org_and_admin_cannot_run_or_read_pair(comparison):
    result=completed(comparison)
    for user,org,status in [('outsider','other_org',404),('admin','org',403)]:
        with login(comparison,user,org) as client:
            response=client.post('/api/v1/tool-runs',json=comparison['comparison_payload'],headers={'Idempotency-Key':str(uuid4())})
            assert response.status_code==status
            assert client.get('/api/v1/tool-runs/'+result['tool_execution_id']).status_code==404


def test_before_scope_revoked_during_compute_cannot_publish_after_only_result(comparison,monkeypatch):
    with transaction() as conn:
        conn.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:owner,:org,'engineer',:owner) ON CONFLICT DO NOTHING"),comparison)
        for record in (comparison['before_record'],comparison['after_record']):
            conn.execute(text('INSERT INTO measurement_grants VALUES (:org,:measurement,:owner,true,true,:peer)'),dict(comparison,measurement=UUID(record['id'])))
    with login(comparison,'owner') as actor:
        body=dict(comparison['comparison_payload'])
        for name in ('before','after'):
            record=comparison[name+'_record']
            response=actor.post('/api/v1/measurements/'+record['id']+'/confirmations',json={'revision_id':record['current_revision_id']})
            assert response.status_code==200,response.text
            body['before_confirmation_id' if name=='before' else 'confirmation_id']=response.json()['id']
        original=comparison_service.compute
        def revoke(*args):
            result=original(*args)
            with transaction() as conn:
                conn.execute(text('DELETE FROM measurement_grants WHERE measurement_id=:id AND user_id=:user'),dict(id=UUID(comparison['before_record']['id']),user=comparison['owner']))
            return result
        monkeypatch.setattr(comparison_service,'compute',revoke)
        response=actor.post('/api/v1/tool-runs',json=body,headers={'Idempotency-Key':str(uuid4())})
        assert response.status_code==404
        with transaction() as conn:
            status=conn.execute(text("SELECT status FROM tool_runs WHERE requested_by=:actor AND tool_name='compare_measurements'"),dict(actor=comparison['owner'])).scalar_one()
        assert status=='failed'
