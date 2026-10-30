"""Real PostgreSQL target binding, versioning and authorization regression."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from vibradiag.analysis.bearing_frequencies import calculate_bearing_frequencies
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.schemas.diagnostic_contexts import CreateBearingRevision, CreateDiagnosticContext
from vibradiag.services import diagnostic_context_service as service
from vibradiag.services.identity_service import login, authenticate

SOURCE = dict(kind='synthetic_exact', reference='Analytic fixture Z8 d10 D40 alpha0; not a real bearing model', version='1')
GEOMETRY = dict(rolling_elements=8, rolling_element_diameter=.01, pitch_diameter=.04,
                length_unit='m', contact_angle_deg=0., bearing_design='single_row_ball', source=SOURCE)


def signed_in(data, user='peer'):
    with transaction() as connection:
        credentials = login(connection, data['other_org'] if user == 'outsider' else data['org'], user, data['password'], uuid4())
        return authenticate(connection, credentials[0])


@pytest.fixture
def target(database):
    data = dict(database)
    with transaction() as connection:
        connection.execute(text('''INSERT INTO asset_grants VALUES (:org,:peer,:asset,true,true)
            ON CONFLICT (user_id,asset_id) DO UPDATE SET can_read=true,can_write=true'''), data)
        connection.execute(text('''INSERT INTO measurement_grants VALUES (:org,:measurement,:peer,true,true,:owner)
            ON CONFLICT (measurement_id,user_id) DO UPDATE SET can_read=true,can_write=true'''), data)
        # A new immutable revision with known synthetic rpm; don't mutate baseline input.
        number = connection.execute(text('SELECT max(revision_no)+1 FROM measurement_revisions WHERE measurement_id=:measurement'), data).scalar_one()
        parent = connection.execute(text('SELECT current_revision_id FROM measurements WHERE id=:measurement'), data).scalar_one()
        data['revision'] = uuid4()
        connection.execute(text('''INSERT INTO measurement_revisions
            (id,organization_id,measurement_id,revision_no,parent_id,sampling_rate_hz,acceleration_unit,
             asset_id,measurement_point_id,rpm,source_type,parameter_sources,reason,created_by)
            VALUES (:revision,:org,:measurement,:number,:parent,12000,'m/s2',:asset,:point,1800,'synthetic','{}','fixture',:owner)'''),
            dict(data, number=number, parent=parent))
        connection.execute(text('UPDATE measurements SET current_revision_id=:revision WHERE id=:measurement'), data)
    data['principal'] = signed_in(data)
    data['component'] = service.create_component(dict(asset_id=data['asset'], component_code=str(uuid4()), name='Drive end'), data['principal'])
    declaration = CreateBearingRevision.model_validate(dict(component_id=data['component'], expected_current_revision_id=None,
        parameterization='geometry', geometry=GEOMETRY, reason='Analytic test'))
    assert 'checked' not in declaration.geometry.model_dump()
    data['bearing'] = service.create_bearing_revision(declaration, data['principal'])
    return data


def context_payload(data, **overrides):
    return dict(measurement_revision_id=data['revision'], component_id=data['component'], bearing_revision_id=data['bearing'],
        conditions=dict(rpm_basis='synthetic_exact', rpm_source=SOURCE, rpm_uncertainty_rpm=0., rpm_uncertainty_source=SOURCE,
                        speed_condition='constant_steady_checked', rotation='inner_ring_rotates_outer_fixed'),
        acquisition=dict(low_hz=100., high_hz=5000., source=SOURCE), source_reference=SOURCE,
        reason='Explicit software attestation to analytic fixture', **overrides)


def test_resolves_m1_rpm_normalized_geometry_and_audit(target):
    identifier = service.create_context(context_payload(target), target['principal'])
    with transaction() as connection:
        resolved = service.resolve_context(connection, target['principal'], identifier)
        params = resolved['frequency_parameters']
        assert params.geometry.length_unit == 'mm'
        assert params.geometry.rolling_element_diameter == 10
        assert params.geometry.pitch_diameter == 40
        assert params.conditions.rpm == 1800 and params.conditions.rpm_checked
        assert params.geometry.checked and resolved['acquisition'].checked
        assert 'rpm' not in resolved['context']['conditions']
        assert resolved['context']['checked_by'] == target['peer']
        assert connection.execute(text('SELECT count(*) FROM audit_events WHERE object_id=:id AND actor_id=:actor'),
            dict(id=identifier, actor=target['peer'])).scalar_one() == 1
    result = calculate_bearing_frequencies(params)
    assert result.status == 'ok' and result.summary['frequencies']['BPFO']['frequency_hz'] == 90
    assert not result.summary['uncertainty_complete']  # No geometry tolerance supplied.
    public = service.read_context(identifier, target['principal'])
    assert public['is_current_revision'] and public['is_current_bearing_revision']
    assert 'not independently verified' in public['attestation']


@pytest.mark.parametrize('field,value', [('rpm', 900), ('rpm_checked', True), ('target', {}), ('checked_by', str(uuid4()))])
def test_cannot_inject_trusted_conditions(field, value):
    body = dict(measurement_revision_id=uuid4(), component_id=uuid4(), bearing_revision_id=uuid4(),
                conditions={field:value}, source_reference=SOURCE, reason='test')
    with pytest.raises(ValidationError):
        CreateDiagnosticContext.model_validate(body)


@pytest.mark.parametrize('extra', [{'checked':True}, {'checked':False}, {'checked_by':str(uuid4())}, {'qualification':{}}])
def test_cannot_self_approve_bearing(extra):
    with pytest.raises(ValidationError):
        CreateBearingRevision.model_validate(dict(component_id=uuid4(), expected_current_revision_id=None,
            parameterization='geometry', geometry=dict(GEOMETRY, **extra), reason='test'))


def test_only_engineer_with_both_scopes_can_write(target):
    for user, expected in [('owner',403), ('admin',403), ('outsider',404)]:
        with pytest.raises(MeasurementError) as error:
            service.create_context(context_payload(target), signed_in(target, user))
        assert error.value.status == expected
    with transaction() as connection:
        connection.execute(text('UPDATE measurement_grants SET can_write=false WHERE measurement_id=:measurement AND user_id=:peer'), target)
    with pytest.raises(MeasurementError) as error:
        service.create_context(context_payload(target), target['principal'])
    assert error.value.status == 404


def test_revocation_session_and_read_scope(target):
    identifier = service.create_context(context_payload(target), target['principal'])
    with transaction() as connection:
        connection.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE asset_id=:asset AND user_id=:peer'), target)
    with pytest.raises(MeasurementError) as error:
        service.read_context(identifier, target['principal'])
    assert error.value.status == 404
    with transaction() as connection:
        connection.execute(text('UPDATE auth_sessions SET revoked_at=now() WHERE id=:id'), dict(id=target['principal'].session_id))
    with pytest.raises(MeasurementError) as error:
        service.read_context(identifier, target['principal'])
    assert error.value.status == 401


def test_historical_bearing_remains_bound_and_concurrent_revision_has_one_winner(target):
    identifier = service.create_context(context_payload(target), target['principal'])
    body = dict(component_id=target['component'], expected_current_revision_id=target['bearing'],
                parameterization='geometry', geometry=GEOMETRY, reason='New parameter version')
    def revise(_):
        try:
            return service.create_bearing_revision(body, target['principal'])
        except MeasurementError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(revise, range(2)))
    assert sum(value == 409 for value in results) == 1
    assert not service.read_context(identifier, target['principal'])['is_current_bearing_revision']
    with pytest.raises(MeasurementError) as error:
        service.create_context(context_payload(target), target['principal'])
    assert error.value.code == 'BEARING_REVISION_CONFLICT'


@pytest.mark.parametrize('table', ['bearing_revisions', 'diagnostic_contexts'])
def test_history_immutable_in_database(target, table):
    identifier = target['bearing'] if table == 'bearing_revisions' else service.create_context(context_payload(target), target['principal'])
    for sql in [f"UPDATE {table} SET reason='tampered' WHERE id=:id", f'DELETE FROM {table} WHERE id=:id']:
        with pytest.raises(DBAPIError), transaction() as connection:
            connection.execute(text(sql), dict(id=identifier))


def test_component_identity_cannot_move_or_disappear(target):
    context_id = service.create_context(context_payload(target), target['principal'])
    for sql in ["UPDATE asset_components SET name='changed' WHERE id=:id", 'DELETE FROM asset_components WHERE id=:id']:
        with pytest.raises(DBAPIError), transaction() as connection:
            connection.execute(text(sql), dict(id=target['component']))
    with transaction() as connection:
        connection.execute(text("UPDATE asset_components SET status='inactive' WHERE id=:id"), dict(id=target['component']))
    assert service.read_context(context_id, target['principal'])['is_current_bearing_revision']
    with pytest.raises(MeasurementError) as error:
        service.create_bearing_revision(dict(component_id=target['component'], expected_current_revision_id=target['bearing'],
            parameterization='geometry', geometry=GEOMETRY, reason='test'), target['principal'])
    assert error.value.code == 'COMPONENT_INACTIVE'


def test_wrong_component_bearing_and_cross_org_fk(target):
    other = service.create_component(dict(asset_id=target['asset'], component_code=str(uuid4()), name='Other'), target['principal'])
    body = context_payload(target); body['component_id'] = other
    with pytest.raises(MeasurementError) as error:
        service.create_context(body, target['principal'])
    assert error.value.status == 404
    with pytest.raises(DBAPIError), transaction() as connection:
        connection.execute(text('UPDATE asset_components SET current_bearing_revision_id=:bearing WHERE id=:component'),
            dict(component=other, bearing=target['bearing']))
    with pytest.raises(DBAPIError), transaction() as connection:
        connection.execute(text('''INSERT INTO asset_components(id,organization_id,asset_id,component_code,name,created_by)
            VALUES (:id,:other_org,:asset,'bad','bad',:outsider)'''), dict(target, id=uuid4()))


def test_incomplete_conditions_preserved_and_return_unavailable(target):
    body = context_payload(target); body['conditions'] = {}; body['acquisition'] = None
    identifier = service.create_context(body, target['principal'])
    with transaction() as connection:
        result = calculate_bearing_frequencies(service.resolve_context(connection, target['principal'], identifier)['frequency_parameters'])
    assert result.status == 'unavailable' and not result.summary
    assert 'reliable_current_rpm_basis' in result.missing_requirements


def test_bandwidth_not_inferred_and_rpm_uncertainty_validated(target):
    body = context_payload(target); body['acquisition']['high_hz'] = 6000.
    with pytest.raises(MeasurementError) as error:
        service.create_context(body, target['principal'])
    assert error.value.code == 'BANDWIDTH_INVALID'
    body = context_payload(target); body['conditions']['rpm_uncertainty_rpm'] = 1800.
    with pytest.raises(ValidationError):
        service.create_context(body, target['principal'])


def test_m1_uses_shared_total_two_slot_budget():
    from vibradiag.services.compute_capacity import capacity
    from vibradiag.services.persistent_analysis_service import _capacity
    assert _capacity is capacity
    assert capacity.acquire(blocking=False) and capacity.acquire(blocking=False)
    try:
        assert not _capacity.acquire(blocking=False)
    finally:
        capacity.release(); capacity.release()


def append_revision(data, *, source='synthetic', asset=None):
    identifier = uuid4()
    with transaction() as connection:
        number = connection.execute(text('SELECT max(revision_no)+1 FROM measurement_revisions WHERE measurement_id=:measurement'), data).scalar_one()
        connection.execute(text('''INSERT INTO measurement_revisions
            (id,organization_id,measurement_id,revision_no,parent_id,sampling_rate_hz,acceleration_unit,
             asset_id,rpm,source_type,parameter_sources,reason,created_by)
            VALUES (:id,:org,:measurement,:number,:revision,12000,'m/s2',:new_asset,900,:source,'{}','revision',:owner)'''),
            dict(data, id=identifier, number=number, new_asset=asset, source=source))
        connection.execute(text('UPDATE measurements SET current_revision_id=:id WHERE id=:measurement'), dict(data, id=identifier))
    return identifier


def test_old_context_stays_on_old_rpm_and_new_context_requires_current_input(target):
    identifier = service.create_context(context_payload(target), target['principal'])
    append_revision(target)  # Current input is now unassociated; historical asset scope still matters.
    summary = service.read_context(identifier, target['principal'])
    assert not summary['is_current_revision']
    with transaction() as connection:
        assert service.resolve_context(connection, target['principal'], identifier)['frequency_parameters'].conditions.rpm == 1800
    with pytest.raises(MeasurementError) as error:
        service.create_context(context_payload(target), target['principal'])
    assert error.value.code == 'REVISION_CONFLICT'
    with transaction() as connection:
        connection.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE asset_id=:asset AND user_id=:peer'), target)
    with pytest.raises(MeasurementError) as error:
        service.read_context(identifier, target['principal'])
    assert error.value.status == 404


def test_non_synthetic_input_cannot_acquire_exact_synthetic_attestation(target):
    target['revision'] = append_revision(target, source='field_upload', asset=target['asset'])
    with pytest.raises(MeasurementError) as error:
        service.create_context(context_payload(target), target['principal'])
    assert error.value.code == 'SOURCE_CONFLICT'


def test_target_on_another_authorized_asset_rejected_by_service_and_database(target):
    other_asset = uuid4()
    with transaction() as connection:
        connection.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:id,:org,:code,'Other','motor')"),
            dict(id=other_asset, org=target['org'], code=str(other_asset)))
        connection.execute(text('INSERT INTO asset_grants VALUES (:org,:peer,:asset,true,true)'), dict(target, asset=other_asset))
    other = service.create_component(dict(asset_id=other_asset, component_code='DE', name='Other target'), target['principal'])
    body = context_payload(target); body['component_id'] = other
    with pytest.raises(MeasurementError) as error:
        service.create_context(body, target['principal'])
    assert error.value.code == 'CONTEXT_TARGET_MISMATCH'
    # Direct SQL also cannot bind a component from another asset to this revision.
    with pytest.raises(DBAPIError), transaction() as connection:
        connection.execute(text('''INSERT INTO diagnostic_contexts
            (id,organization_id,measurement_revision_id,asset_id,component_id,bearing_revision_id,
             conditions,source_type,source_reference,reason,checked_by)
            VALUES (:id,:org,:revision,:asset,:other,:bearing,'{}','synthetic','{}','bad',:peer)'''),
            dict(target, id=uuid4(), other=other))


def test_geometry_normalization_overflow_has_clear_error():
    payload = CreateBearingRevision.model_validate(dict(component_id=uuid4(), expected_current_revision_id=None,
        parameterization='geometry', geometry=dict(GEOMETRY, rolling_element_diameter=1e307, pitch_diameter=1e308), reason='test'))
    with pytest.raises(MeasurementError) as error:
        service.normalized_parameters(payload)
    assert error.value.code == 'PARAMETER_RANGE'
