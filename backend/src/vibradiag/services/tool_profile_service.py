"""Only exploratory profiles can be registered; no caller-supplied qualification."""
from hashlib import sha256
import json
from uuid import uuid4
from sqlalchemy import text
from vibradiag.analysis.envelope import VERSION, numeric_configuration
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.analysis_store import json_bytes
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.schemas.tool_runs import EnvelopeProfileDeclaration
from vibradiag.schemas.tools import EnvelopeParameters
from vibradiag.services.diagnostic_context_service import engineer
from vibradiag.services.identity_service import asset_allowed, audit
from vibradiag.services.measurement_service import not_found


def create_profile(payload, principal, request_id=None):
    payload = EnvelopeProfileDeclaration.model_validate(payload)
    config = payload.model_dump(mode='json', exclude={'asset_id'})
    parameters = EnvelopeParameters.model_validate(dict(config, acceleration_unit='m/s2'))
    config['numeric_configuration'] = numeric_configuration(parameters)
    fingerprint = sha256(json_bytes(config)).hexdigest()
    with transaction() as connection:
        current = engineer(connection, principal)
        if not asset_allowed(connection, current, payload.asset_id, write=True):
            not_found()
        identifier = uuid4()
        connection.execute(text('''INSERT INTO tool_profiles
            (id,organization_id,asset_id,tool_name,tool_version,configuration,config_hash,created_by)
            VALUES (:id,:org,:asset,'analyze_envelope',:version,CAST(:config AS jsonb),:hash,:actor)'''),
            dict(id=identifier, org=current.organization_id, asset=payload.asset_id, version=VERSION,
                 config=json.dumps(config, allow_nan=False), hash=fingerprint, actor=current.user_id))
        audit(connection, current.organization_id, current.user_id, 'tool_profile.create_exploratory', 'tool_profile', identifier, request_id)
    return identifier


def resolve_profile(connection, principal, identifier, revision):
    row = connection.execute(text('SELECT * FROM tool_profiles WHERE id=:id AND organization_id=:org'),
        dict(id=identifier, org=principal.organization_id)).mappings().first()
    if not row or not asset_allowed(connection, principal, row['asset_id']):
        not_found()
    if row['asset_id'] != revision['asset_id']:
        raise MeasurementError('PROFILE_SCOPE_MISMATCH', 'Profile belongs to another asset', status=409)
    config = dict(row['configuration'])
    if sha256(json_bytes(config)).hexdigest() != row['config_hash']:
        raise MeasurementError('PROFILE_INTEGRITY', 'Profile configuration integrity failure', status=409)
    numeric = config.pop('numeric_configuration')
    parameters = EnvelopeParameters.model_validate(dict(config, acceleration_unit=revision['acceleration_unit']))
    if parameters.sampling_rate_hz != revision['sampling_rate_hz']:
        raise MeasurementError('PROFILE_SAMPLING_MISMATCH', 'Profile sampling rate differs from input', status=409)
    if row['tool_version'] != VERSION or numeric_configuration(parameters) != numeric:
        raise MeasurementError('PROFILE_VERSION_MISMATCH', 'Recreate profile for the current numerical version', status=409)
    return row, parameters
