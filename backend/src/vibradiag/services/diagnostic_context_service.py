"""Trusted adapter from scoped engineering declarations to numerical parameters.

Checks attest to the logged-in user's declaration, never to independent source
verification, professional qualification or instrument calibration.
"""
import json
import math
from uuid import uuid4

from sqlalchemy import text
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.schemas.diagnostic_contexts import (
    CreateComponent, CreateBearingRevision, CreateDiagnosticContext,
)
from vibradiag.schemas.tools import (
    BearingGeometry, BearingCoefficients, BearingFrequencyParameters,
    FrequencyConditions, AcquisitionBandwidth, TargetReference,
)
from vibradiag.services.identity_service import asset_allowed, audit
from vibradiag.services.measurement_service import authorized_measurement, fresh, not_found


def engineer(connection, principal):
    current = fresh(connection, principal)
    if 'engineer' not in current.roles:
        raise MeasurementError('ENGINEER_REQUIRED', 'Engineering role is required', status=403)
    return current


def component(connection, principal, identifier, *, write=False, lock=False):
    row = connection.execute(text('''SELECT * FROM asset_components
        WHERE id=:id AND organization_id=:org''' + (' FOR UPDATE' if lock else '')),
        dict(id=identifier, org=principal.organization_id)).mappings().first()
    if not row or not asset_allowed(connection, principal, row['asset_id'], write=write):
        not_found()
    if write and row['status'] != 'active':
        raise MeasurementError('COMPONENT_INACTIVE', 'Component is inactive', status=409)
    return row


def create_component(payload, principal, request_id=None):
    payload = CreateComponent.model_validate(payload)
    with transaction() as connection:
        current = engineer(connection, principal)
        if not asset_allowed(connection, current, payload.asset_id, write=True):
            not_found()
        # Serialize codes on their owning asset; avoid unique-error internals.
        connection.execute(text('SELECT id FROM assets WHERE id=:id FOR UPDATE'), dict(id=payload.asset_id))
        exists = connection.execute(text('''SELECT 1 FROM asset_components WHERE
            organization_id=:org AND asset_id=:asset AND component_code=:code'''),
            dict(org=current.organization_id, asset=payload.asset_id, code=payload.component_code)).first()
        if exists:
            raise MeasurementError('COMPONENT_CODE_CONFLICT', 'Component code already exists on this asset', status=409)
        identifier = uuid4()
        connection.execute(text('''INSERT INTO asset_components
            (id,organization_id,asset_id,component_code,name,created_by)
            VALUES (:id,:org,:asset,:code,:name,:actor)'''),
            dict(id=identifier, org=current.organization_id, asset=payload.asset_id,
                 code=payload.component_code, name=payload.name, actor=current.user_id))
        audit(connection, current.organization_id, current.user_id, 'component.create', 'component', identifier, request_id)
    return identifier


def normalized_parameters(payload):
    selected = payload.geometry if payload.parameterization == 'geometry' else payload.coefficients
    values = selected.model_dump(mode='json', exclude={'checked'})
    if payload.geometry and values['length_unit'] == 'm':
        for key in ('rolling_element_diameter', 'pitch_diameter'):
            converted = values[key] * 1000
            if not math.isfinite(converted) or converted <= 0:
                raise MeasurementError('PARAMETER_RANGE', 'Geometry cannot be represented in millimetres')
            values[key] = converted
        if values['uncertainty']:
            for key in ('rolling_element_diameter_absolute', 'pitch_diameter_absolute'):
                before = values['uncertainty'][key]
                converted = before * 1000
                if not math.isfinite(converted) or (before > 0 and converted == 0):
                    raise MeasurementError('PARAMETER_RANGE', 'Geometry uncertainty cannot be represented in millimetres')
                values['uncertainty'][key] = converted
        values['length_unit'] = 'mm'
    model = BearingGeometry if payload.geometry else BearingCoefficients
    # Revalidate after unit conversion rather than trusting a model_copy update.
    model.model_validate(values)
    return values


def create_bearing_revision(payload, principal, request_id=None):
    payload = CreateBearingRevision.model_validate(payload)
    values = normalized_parameters(payload)
    with transaction() as connection:
        current = engineer(connection, principal)
        target = component(connection, current, payload.component_id, write=True, lock=True)
        if target['current_bearing_revision_id'] != payload.expected_current_revision_id:
            raise MeasurementError('BEARING_REVISION_CONFLICT', 'Bearing parameters changed; reload before revising', status=409)
        number = 1
        if payload.expected_current_revision_id:
            number = connection.execute(text('SELECT revision_no FROM bearing_revisions WHERE id=:id'),
                dict(id=payload.expected_current_revision_id)).scalar_one() + 1
        identifier = uuid4()
        connection.execute(text('''INSERT INTO bearing_revisions
            (id,organization_id,component_id,parent_id,revision_no,parameterization,parameters,reason,checked_by)
            VALUES (:id,:org,:component,:parent,:number,:path,CAST(:parameters AS jsonb),:reason,:actor)'''),
            dict(id=identifier, org=current.organization_id, component=target['id'],
                 parent=payload.expected_current_revision_id, number=number, path=payload.parameterization,
                 parameters=json.dumps(values, allow_nan=False), reason=payload.reason, actor=current.user_id))
        connection.execute(text('UPDATE asset_components SET current_bearing_revision_id=:revision WHERE id=:id'),
            dict(id=target['id'], revision=identifier))
        audit(connection, current.organization_id, current.user_id, 'bearing.revise', 'bearing_revision', identifier, request_id)
    return identifier


def input_revision(connection, principal, identifier, *, write=False, lock=False):
    row = connection.execute(text('SELECT * FROM measurement_revisions WHERE id=:id AND organization_id=:org'),
        dict(id=identifier, org=principal.organization_id)).mappings().first()
    if not row:
        not_found()
    record = authorized_measurement(connection, principal, row['measurement_id'], write=write, lock=lock)
    # Both the current record scope and the historical revision's asset scope apply.
    if row['asset_id'] and not asset_allowed(connection, principal, row['asset_id'], write=write):
        not_found()
    return row, record


def create_context(payload, principal, request_id=None):
    payload = CreateDiagnosticContext.model_validate(payload)
    with transaction() as connection:
        current = engineer(connection, principal)
        revision, record = input_revision(connection, current, payload.measurement_revision_id, write=True, lock=True)
        if record['current_revision_id'] != revision['id']:
            raise MeasurementError('REVISION_CONFLICT', 'New context requires the current input revision', status=409)
        target = component(connection, current, payload.component_id, write=True, lock=True)
        if revision['asset_id'] != target['asset_id']:
            raise MeasurementError('CONTEXT_TARGET_MISMATCH', 'Target and measurement must belong to the same asset', status=409)
        bearing = connection.execute(text('''SELECT * FROM bearing_revisions WHERE id=:id
            AND component_id=:component AND organization_id=:org'''),
            dict(id=payload.bearing_revision_id, component=target['id'], org=current.organization_id)).mappings().first()
        if not bearing:
            not_found()
        if target['current_bearing_revision_id'] != bearing['id']:
            raise MeasurementError('BEARING_REVISION_CONFLICT', 'New context requires the current bearing revision', status=409)
        conditions = payload.conditions.model_dump(mode='json')
        # The only rpm value is loaded from M1; declarations cannot override it.
        FrequencyConditions.model_validate(dict(conditions, rpm=revision['rpm']))
        if conditions['rpm_basis'] == 'synthetic_exact' and revision['source_type'] != 'synthetic':
            raise MeasurementError('SOURCE_CONFLICT', 'Exact synthetic speed requires synthetic input', status=409)
        for source in (payload.source_reference, payload.conditions.rpm_source, payload.conditions.rpm_uncertainty_source):
            if source and source.kind == 'synthetic_exact' and revision['source_type'] != 'synthetic':
                raise MeasurementError('SOURCE_CONFLICT', 'Synthetic source cannot attest to a non-synthetic input', status=409)
        acquisition = payload.acquisition.model_dump(mode='json') if payload.acquisition else None
        if acquisition and acquisition['high_hz'] >= revision['sampling_rate_hz'] / 2:
            raise MeasurementError('BANDWIDTH_INVALID', 'Effective acquisition bandwidth must be below Nyquist')
        if payload.acquisition and payload.acquisition.source.kind == 'synthetic_exact' and revision['source_type'] != 'synthetic':
            raise MeasurementError('SOURCE_CONFLICT', 'Synthetic bandwidth cannot attest to a non-synthetic input', status=409)
        identifier = uuid4()
        connection.execute(text('''INSERT INTO diagnostic_contexts
            (id,organization_id,measurement_revision_id,asset_id,component_id,bearing_revision_id,
             conditions,acquisition,source_type,source_reference,reason,checked_by)
            VALUES (:id,:org,:revision,:asset,:component,:bearing,CAST(:conditions AS jsonb),
                    CAST(:acquisition AS jsonb),:source_type,CAST(:source AS jsonb),:reason,:actor)'''),
            dict(id=identifier, org=current.organization_id, revision=revision['id'], asset=revision['asset_id'],
                 component=target['id'], bearing=bearing['id'], conditions=json.dumps(conditions, allow_nan=False),
                 acquisition=json.dumps(acquisition, allow_nan=False) if acquisition else None,
                 source_type=revision['source_type'], source=json.dumps(payload.source_reference.model_dump(mode='json')),
                 reason=payload.reason, actor=current.user_id))
        audit(connection, current.organization_id, current.user_id, 'diagnostic_context.create', 'diagnostic_context', identifier, request_id)
    return identifier


def resolve_context(connection, principal, identifier):
    """Resolve inside caller transaction; caller must refresh identity first."""
    context = connection.execute(text('SELECT * FROM diagnostic_contexts WHERE id=:id AND organization_id=:org'),
        dict(id=identifier, org=principal.organization_id)).mappings().first()
    if not context:
        not_found()
    revision, record = input_revision(connection, principal, context['measurement_revision_id'])
    target = component(connection, principal, context['component_id'])
    bearing = connection.execute(text('''SELECT * FROM bearing_revisions WHERE id=:id
        AND component_id=:component AND organization_id=:org'''),
        dict(id=context['bearing_revision_id'], component=target['id'], org=principal.organization_id)).mappings().one()
    selected = dict(bearing['parameters'], checked=True)
    conditions = dict(context['conditions'], rpm=revision['rpm'], rpm_checked=True,
        target=TargetReference(measurement_revision_id=revision['id'], component_id=target['id'], bearing_revision_id=bearing['id']))
    frequency = BearingFrequencyParameters.model_validate(dict(parameterization=bearing['parameterization'],
        **{bearing['parameterization']: selected}, conditions=conditions))
    acquisition = AcquisitionBandwidth.model_validate(dict(context['acquisition'], checked=True)) if context['acquisition'] else None
    return dict(context=dict(context), revision=dict(revision), record=dict(record),
                component=dict(target), bearing=dict(bearing), frequency_parameters=frequency,
                acquisition=acquisition, is_current_revision=record['current_revision_id'] == revision['id'],
                is_current_bearing_revision=target['current_bearing_revision_id'] == bearing['id'],
                attestation='User-declared engineering check; source authenticity and calibration are not independently verified')


def read_context(identifier, principal):
    with transaction() as connection:
        current = fresh(connection, principal)
        result = resolve_context(connection, current, identifier)
    # Public summary excludes storage paths and ORM rows. Numerical objects are internal.
    return {key: result[key] for key in ('context', 'is_current_revision', 'is_current_bearing_revision', 'attestation')}
