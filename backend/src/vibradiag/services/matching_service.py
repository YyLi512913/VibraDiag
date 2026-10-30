"""Resolve matching parents through the same permission/integrity checks as downloads."""
import io
import json
import numpy as np
from sqlalchemy import text
from vibradiag.analysis.matching import match_frequency_evidence, VERSION, CONFIG
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.domain.tool_results import NumericToolResult
from vibradiag.services import persistent_analysis_service as basic_service


def mismatch(message='Matching parents must share the accepted input/context'):
    raise MeasurementError('MATCHING_PARENT_INVALID',message,status=409)


def descriptor(run, artifacts, *, basic=False):
    return dict(id=str(run['id']),version=run['algorithm_version'] if basic else run['tool_version'],
        fingerprint=run['config_hash'] if basic else run['input_fingerprint'],
        artifacts={a['kind']:dict(sha256=a['sha256'],byte_size=a['byte_size']) for a in artifacts})


def accept_parents(connection, current, payload, record):
    from vibradiag.services.tool_run_service import authorized_run, confirmation_active
    result={}
    for name,identifier,tool in [('envelope',payload.envelope_run_id,'analyze_envelope'),
                                 ('frequency',payload.frequency_run_id,'calculate_bearing_frequencies')]:
        run,_,_=authorized_run(connection,current,identifier)
        if run['tool_name']!=tool or run['status']!='completed' or run['result_status']!='ok' or run['context_id']!=payload.context_id or run['measurement_revision_id']!=payload.measurement_revision_id:
            mismatch()
        if not confirmation_active(connection,run): mismatch('Parent input confirmation was revoked')
        if run['input_snapshot']['input_sha256']!=record['sha256']: mismatch('Parent original hash differs from input')
        artifacts=connection.execute(text('SELECT * FROM tool_artifacts WHERE tool_run_id=:id AND organization_id=:org'),
            dict(id=identifier,org=current.organization_id)).mappings().all()
        result[name]=descriptor(run,artifacts)
    basic,_=basic_service.authorized_analysis(connection,current,payload.basic_analysis_id)
    if basic['status']!='completed' or basic['measurement_revision_id']!=payload.measurement_revision_id or basic['confirmation_revoked_at'] is not None:
        mismatch('Basic analysis must be completed for this input with an active confirmation')
    if basic['config_snapshot']['input_sha256']!=record['sha256']: mismatch()
    artifacts=connection.execute(text('SELECT * FROM analysis_artifacts WHERE analysis_id=:id AND organization_id=:org'),
        dict(id=payload.basic_analysis_id,org=current.organization_id)).mappings().all()
    result['basic']=descriptor(basic,artifacts,basic=True)
    return result


def load_parents(snapshot, principal, *, require_active):
    from vibradiag.services.tool_run_service import read_bundle
    loaded={}; active=True
    for name in ('envelope','frequency'):
        expected=snapshot['parents'][name]
        run,record,confirmation,artifacts,blobs=read_bundle(expected['id'],principal)
        if descriptor(run,artifacts)!=expected or run['measurement_revision_id'].__str__()!=snapshot['measurement_revision_id'] or str(run['context_id'])!=snapshot['context']['id'] or record['sha256']!=snapshot['input_sha256']:
            mismatch('Parent lineage differs from the accepted snapshot')
        active=active and confirmation
        if run['status']!='completed' or run['result_status']!='ok': mismatch()
        metadata=json.loads(blobs['result']); arrays={}
        if 'arrays' in blobs:
            with np.load(io.BytesIO(blobs['arrays']),allow_pickle=False) as data:
                arrays={key:data[key] for key in data.files}
        loaded[name]=NumericToolResult(metadata['tool_name'],metadata['tool_version'],metadata['status'],metadata['summary'],arrays,
            tuple(metadata['missing_requirements']),tuple(metadata['limitations']),metadata['error_code'])
    expected=snapshot['parents']['basic']
    run,record,artifacts,blobs=basic_service.read_bundle(expected['id'],principal)
    if descriptor(run,artifacts,basic=True)!=expected or str(run['measurement_revision_id'])!=snapshot['measurement_revision_id'] or record['sha256']!=snapshot['input_sha256'] or run['status']!='completed':
        mismatch('Basic analysis lineage differs from the accepted snapshot')
    active=active and run['confirmation_revoked_at'] is None
    if require_active and not active: mismatch('Parent input confirmation was revoked')
    with np.load(io.BytesIO(blobs['amplitude_spectrum']),allow_pickle=False) as data:
        loaded['basic']=dict(frequency_hz=data['frequency_hz'],amplitude_m_s2=data['amplitude_m_s2'],sample_count=json.loads(blobs['result'])['sample_count'])
    return loaded,active


def compute(snapshot, principal):
    parents,_=load_parents(snapshot,principal,require_active=True)
    common=dict(measurement_revision_id=snapshot['measurement_revision_id'],context_id=snapshot['context']['id'],input_sha256=snapshot['input_sha256'])
    parameters=dict(envelope_source=dict(common,execution_id=snapshot['parents']['envelope']['id']),
        frequency_source=dict(common,execution_id=snapshot['parents']['frequency']['id']),
        basic_source=dict(common,execution_id=snapshot['parents']['basic']['id']))
    from vibradiag.infrastructure.compute import runner as numeric
    from vibradiag.services.analysis_runtime import runtime
    return numeric.run('matching',dict(envelope=parents['envelope'],frequencies=parents['frequency'],parameters=parameters,basic_spectrum=parents['basic']),
        lambda:match_frequency_evidence(parents['envelope'],parents['frequency'],parameters,basic_spectrum=parents['basic']),check=runtime.ensure)
