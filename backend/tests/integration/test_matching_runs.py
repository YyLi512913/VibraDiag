from uuid import UUID, uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_tool_runs import case, completed, payload, login, SOURCE
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.services.diagnostic_context_service import create_bearing_revision, create_context
from vibradiag.services import tool_run_service, evidence_service


@pytest.fixture
def matching(case):
    bearing=create_bearing_revision(dict(component_id=case['component'],expected_current_revision_id=case['bearing'],parameterization='geometry',
        geometry=dict(rolling_elements=8,rolling_element_diameter=10.,pitch_diameter=40.,length_unit='mm',contact_angle_deg=0.,
            bearing_design='single_row_ball',source=SOURCE,uncertainty=dict(rolling_element_diameter_absolute=0.,pitch_diameter_absolute=0.,
                contact_angle_deg_absolute=0.,source=SOURCE)),reason='Exact synthetic uncertainty'),case['principal'])
    context=create_context(dict(measurement_revision_id=case['revision'],component_id=case['component'],bearing_revision_id=bearing,
        conditions=dict(rpm_basis='synthetic_exact',rpm_source=SOURCE,rpm_uncertainty_rpm=0.,rpm_uncertainty_source=SOURCE,
            speed_condition='constant_steady_checked',rotation='inner_ring_rotates_outer_fixed'),source_reference=SOURCE,reason='Exact synthetic fixture'),case['principal'])
    case['context']=context
    envelope=completed(case); frequency=completed(case,payload(case,'calculate_bearing_frequencies'))
    response=case['client'].post('/api/v1/analyses',json=dict(measurement_revision_id=case['revision'],confirmation_id=case['confirmation']),headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==201,response.text
    case['matching_payload']=dict(tool_name='match_frequency_evidence',measurement_revision_id=case['revision'],confirmation_id=case['confirmation'],
        context_id=str(context),envelope_run_id=envelope['tool_execution_id'],frequency_run_id=frequency['tool_execution_id'],basic_analysis_id=response.json()['analysis_id'])
    return case


def execute(case,key=None):
    return case['client'].post('/api/v1/tool-runs',json=case['matching_payload'],headers={'Idempotency-Key':key or str(uuid4())})


def successful(case):
    response=execute(case); assert response.status_code==201,response.text
    result=response.json(); assert result['status']=='completed' and result['result_status']=='ok',result
    return result


def test_existing_matching_result_is_not_recomputed_by_new_algorithm_version(matching,monkeypatch):
    result=successful(matching)
    stored_version=result['result']['tool_version']
    assert stored_version=='frequency-matching-v2'
    monkeypatch.setattr(tool_run_service.matching_service,'VERSION','future-version')
    def fail(*args,**kwargs):raise AssertionError('history must never be recomputed')
    monkeypatch.setattr(tool_run_service.matching_service,'compute',fail)
    restored=matching['client'].get('/api/v1/tool-runs/'+result['tool_execution_id'])
    assert restored.status_code==200
    assert restored.json()['result']['tool_version']==stored_version
    assert restored.json()['fact_refs']==result['fact_refs']


def test_matching_graph_and_authorized_fact_evidence_resolution(matching):
    result=successful(matching)
    assert len(result['evidence_refs'])==63 and len(result['fact_refs'])>=126
    assert result['result']['summary']['independent_acquisition_count']==1
    assert not result['result']['summary']['evidence_eligible']
    evidence=matching['client'].get('/api/v1/evidence/'+result['evidence_refs'][0])
    assert evidence.status_code==200,evidence.text
    item=evidence.json(); assert item['purpose']=='exploratory_observation' and item['validity']['input_confirmations_active']
    assert not item['validity']['evidence_eligible']
    fact=matching['client'].get('/api/v1/facts/'+item['fact_id'])
    assert fact.status_code==200,fact.text
    relation=fact.json(); assert relation['kind']=='matching_relation'
    assert relation['root_acquisition_refs']==item['root_acquisition_refs']
    for parent in relation['parent_fact_ids']:
        assert matching['client'].get('/api/v1/facts/'+parent).status_code==200


def test_same_key_replay_no_duplicate_graph(matching):
    key=str(uuid4()); first=execute(matching,key); assert first.status_code==201,first.text
    second=execute(matching,key)
    assert second.status_code==200 and second.json()['evidence_refs']==first.json()['evidence_refs']
    with transaction() as conn:
        count=conn.execute(text('SELECT count(*) FROM evidence_items WHERE tool_run_id=:id'),dict(id=UUID(first.json()['tool_execution_id']))).scalar_one()
    assert count==63


@pytest.mark.parametrize('field',['envelope_run_id','frequency_run_id','basic_analysis_id','context_id'])
def test_forged_parent_ids_rejected(matching,field):
    matching['matching_payload'][field]=str(uuid4())
    assert execute(matching).status_code==404


def test_wrong_parent_kind_is_not_accepted(matching):
    matching['matching_payload']['envelope_run_id']=matching['matching_payload']['frequency_run_id']
    assert execute(matching).status_code==409


def test_existing_graph_ids_do_not_bypass_org_and_record_scope(matching):
    result=successful(matching)
    for user,org in [('owner','org'),('admin','org'),('outsider','other_org')]:
        with login(matching,user,org) as client:
            assert client.get('/api/v1/facts/'+result['fact_refs'][0]).status_code==404
            assert client.get('/api/v1/evidence/'+result['evidence_refs'][0]).status_code==404
    assert matching['client'].get('/api/v1/evidence/'+str(uuid4())).status_code==404


def test_parent_artifact_corruption_invalidates_matching_and_graph_reads(matching):
    result=successful(matching)
    parent=matching['matching_payload']['envelope_run_id']
    original=FileStore().root/'tools'/str(matching['org'])/parent/'arrays.npz'
    original.write_bytes(b'tampered')
    for path in ['/tool-runs/'+result['tool_execution_id'],'/evidence/'+result['evidence_refs'][0],'/facts/'+result['fact_refs'][0]]:
        response=matching['client'].get('/api/v1'+path)
        assert response.status_code==409,response.text


def test_revoked_confirmation_retains_history_but_marks_invalid(matching):
    result=successful(matching)
    with transaction() as conn:
        conn.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(matching['confirmation'])))
    response=matching['client'].get('/api/v1/evidence/'+result['evidence_refs'][0])
    assert response.status_code==200,response.text
    assert not response.json()['validity']['input_confirmations_active']
    assert execute(matching).status_code==409


def test_fact_and_evidence_immutable_and_not_promotable(matching):
    result=successful(matching)
    for sql,id in [("UPDATE analysis_facts SET content=jsonb_build_object('amplitude_unit','g') WHERE id=:id",result['fact_refs'][0]),
        ('DELETE FROM evidence_items WHERE id=:id',result['evidence_refs'][0]),
        ('UPDATE evidence_items SET evidence_eligible=true WHERE id=:id',result['evidence_refs'][0])]:
        with pytest.raises(DBAPIError),transaction() as conn: conn.execute(text(sql),dict(id=UUID(id)))


def test_graph_registration_rollback_keeps_failed_run_without_partial_facts(matching,monkeypatch):
    original=evidence_service.register
    def fail(*args):
        original(*args)
        raise OSError('Injected graph transaction failure')
    monkeypatch.setattr(evidence_service,'register',fail)
    response=execute(matching); assert response.status_code==201,response.text
    assert response.json()['status']=='failed' and not response.json()['fact_refs']
    with transaction() as conn:
        assert conn.execute(text('SELECT count(*) FROM analysis_facts WHERE tool_run_id=:id'),dict(id=UUID(response.json()['tool_execution_id']))).scalar_one()==0


def test_matching_respects_shared_capacity(matching):
    from vibradiag.services.compute_capacity import capacity
    capacity.acquire();capacity.acquire()
    try: assert execute(matching).status_code==429
    finally: capacity.release();capacity.release()


def test_missing_uncertainty_persists_unavailable_without_fake_graph(case):
    envelope=completed(case); frequency=completed(case,payload(case,'calculate_bearing_frequencies'))
    basic=case['client'].post('/api/v1/analyses',json=dict(measurement_revision_id=case['revision'],confirmation_id=case['confirmation']),headers={'Idempotency-Key':str(uuid4())}).json()
    case['matching_payload']=dict(tool_name='match_frequency_evidence',measurement_revision_id=case['revision'],confirmation_id=case['confirmation'],context_id=str(case['context']),
        envelope_run_id=envelope['tool_execution_id'],frequency_run_id=frequency['tool_execution_id'],basic_analysis_id=basic['analysis_id'])
    response=execute(case); assert response.status_code==201,response.text
    assert response.json()['result_status']=='unavailable' and not response.json()['evidence_refs']


def test_options_are_scoped_and_match_current_input(matching):
    path='/api/v1/tool-runs/options/'+matching['measurement']['id']
    response=matching['client'].get(path); assert response.status_code==200,response.text
    assert response.json()['measurement_revision_id']==matching['revision']
    assert str(matching['context']) in {c['id'] for c in response.json()['contexts']}
    for user,org in [('owner','org'),('admin','org'),('outsider','other_org')]:
        with login(matching,user,org) as client: assert client.get(path).status_code==404


def test_parent_confirmation_revoked_during_matching_cannot_register_graph(matching,monkeypatch):
    original=tool_run_service.matching_service.compute
    def revoked(*args):
        result=original(*args)
        with transaction() as conn:
            conn.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(matching['confirmation'])))
        return result
    monkeypatch.setattr(tool_run_service.matching_service,'compute',revoked)
    response=execute(matching); assert response.status_code==201,response.text
    assert response.json()['status']=='failed' and not response.json()['fact_refs']


def test_interrupted_matching_never_appends_graph(matching,monkeypatch):
    original=tool_run_service.matching_service.compute
    def interrupted(*args):
        from vibradiag.services.analysis_runtime import recover_interrupted
        recover_interrupted()
        return original(*args)
    monkeypatch.setattr(tool_run_service.matching_service,'compute',interrupted)
    response=execute(matching); assert response.status_code==201,response.text
    assert response.json()['status']=='interrupted' and not response.json()['evidence_refs']


def test_missing_graph_blocks_database_completion_atomically(matching,monkeypatch):
    monkeypatch.setattr(evidence_service,'register',lambda *args:None)
    key=str(uuid4()); response=execute(matching,key)
    assert response.status_code==503,response.text
    replay=execute(matching,key)
    assert replay.status_code==200 and replay.json()['status']=='failed' and not replay.json()['artifacts']


def test_scope_revocation_blocks_existing_fact_and_evidence_ids(matching):
    result=successful(matching)
    with transaction() as conn:
        conn.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer AND asset_id=:asset'),matching)
    for path in ['/evidence/'+result['evidence_refs'][0],'/facts/'+result['fact_refs'][0],'/tool-runs/'+result['tool_execution_id']]:
        assert matching['client'].get('/api/v1'+path).status_code==404


def test_matching_does_not_accept_arbitrary_thresholds_or_qualification(matching):
    for key,value in [('slip_allowance_relative',0.),('evidence_eligible',True),('qualification',{})]:
        body=dict(matching['matching_payload'],**{key:value})
        assert matching['client'].post('/api/v1/tool-runs',json=body,headers={'Idempotency-Key':str(uuid4())}).status_code==422


def test_another_actors_revoked_parent_confirmation_marks_matching_history_invalid(matching):
    # A second scoped engineer confirms the same shared input; its own confirmation
    # stays active after the original parent actor's confirmation is revoked.
    with transaction() as conn:
        conn.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:owner,:org,'engineer',:owner) ON CONFLICT DO NOTHING"),matching)
        conn.execute(text('INSERT INTO measurement_grants VALUES (:org,:measurement,:owner,true,true,:peer)'),dict(matching,measurement=UUID(matching['measurement']['id'])))
    with login(matching,'owner') as client:
        confirmation=client.post(f"/api/v1/measurements/{matching['measurement']['id']}/confirmations",json={'revision_id':matching['revision']})
        assert confirmation.status_code==200,confirmation.text
        body=dict(matching['matching_payload'],confirmation_id=confirmation.json()['id'])
        response=client.post('/api/v1/tool-runs',json=body,headers={'Idempotency-Key':str(uuid4())})
        assert response.status_code==201,response.text
        assert response.json()['status']=='completed'
        with transaction() as conn:
            conn.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(matching['confirmation'])))
        read=client.get('/api/v1/tool-runs/'+response.json()['tool_execution_id'])
        assert read.status_code==200,read.text
        assert not read.json()['input_confirmation_active']
