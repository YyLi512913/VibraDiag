"""Durable, bounded numerical tools; no LLM and no fault-classification claims."""
from hashlib import sha256
import io
import json
from time import perf_counter
from uuid import uuid4
import numpy as np
from pydantic import TypeAdapter
from sqlalchemy import text
from vibradiag.analysis.envelope import analyze_envelope, VERSION as ENVELOPE_VERSION
from vibradiag.analysis.bearing_frequencies import calculate_bearing_frequencies, VERSION as FREQUENCY_VERSION
from vibradiag.analysis.csv_input import parse_csv
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.domain.tool_results import ToolInputError
from vibradiag.infrastructure.persistence.analysis_store import json_bytes
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.infrastructure.persistence.tool_store import ToolStore, KINDS
from vibradiag.schemas.tool_runs import ToolRunRequest
from vibradiag.schemas.tools import EnvelopeParameters
from vibradiag.services.analysis_runtime import runtime
from vibradiag.services.compute_capacity import capacity
from vibradiag.services.diagnostic_context_service import input_revision, resolve_context
from vibradiag.services.tool_profile_service import resolve_profile
from vibradiag.services.identity_service import audit
from vibradiag.services.measurement_service import fresh, not_found, original_bytes, authorized_measurement
from vibradiag.services import matching_service, evidence_service, comparison_service

REQUEST = TypeAdapter(ToolRunRequest)


def authorized_run(connection, principal, identifier, *, write=False, lock=False):
    run = connection.execute(text('SELECT * FROM tool_runs WHERE id=:id AND organization_id=:org' + (' FOR UPDATE' if lock else '')),
        dict(id=identifier, org=principal.organization_id)).mappings().first()
    if not run:
        not_found()
    revision, record = input_revision(connection, principal, run['measurement_revision_id'], write=write)
    if run['context_id']:
        context = resolve_context(connection, principal, run['context_id'])
        run = dict(run, is_current_bearing_revision=context['is_current_bearing_revision'])
    if run['tool_name']=='compare_measurements':
        run=comparison_service.authorize(connection,principal,run,write=write)
    return run, revision, record


def confirmation_active(connection, run):
    pairs=[(run['confirmation_id'],run['measurement_revision_id'])]
    if run.get('before_confirmation_id'):
        pairs.append((run['before_confirmation_id'],run['before_revision_id']))
    return all(bool(connection.execute(text('''SELECT 1 FROM input_confirmations WHERE id=:id
        AND revision_id=:revision AND organization_id=:org AND actor_id=:actor AND revoked_at IS NULL'''),
        dict(id=identifier, revision=revision, org=run['organization_id'], actor=run['requested_by'])).first()) for identifier,revision in pairs)


def fail_run(identifier, code):
    with transaction() as connection:
        run = connection.execute(text('SELECT * FROM tool_runs WHERE id=:id FOR UPDATE'), dict(id=identifier)).mappings().one()
        if run['status'] != 'running':
            return
        connection.execute(text("UPDATE tool_runs SET status='failed',result_status='failed',finished_at=now(),error_code=:code WHERE id=:id"),
            dict(id=identifier, code=code))
        connection.execute(text("UPDATE tool_idempotency_records SET state='failed' WHERE resource_id=:id"), dict(id=identifier))
        audit(connection, run['organization_id'], None, 'tool.failed', 'tool_run', identifier)


def prepare(connection, current, payload):
    if payload.tool_name=='compare_measurements':
        return comparison_service.prepare(connection,current,payload)
    revision, record = input_revision(connection, current, payload.measurement_revision_id, write=True, lock=True)
    if record['current_revision_id'] != revision['id']:
        raise MeasurementError('REVISION_CONFLICT', 'New tool execution requires the current input revision', status=409)
    accepted = dict(confirmation_id=payload.confirmation_id, measurement_revision_id=revision['id'],
                    organization_id=current.organization_id, requested_by=current.user_id)
    if not confirmation_active(connection, accepted):
        raise MeasurementError('CONFIRMATION_INVALID', 'Confirm this input revision with your own account', status=409)
    context = resolve_context(connection, current, payload.context_id) if payload.context_id else None
    if context and context['revision']['id'] != revision['id']:
        raise MeasurementError('CONTEXT_INPUT_MISMATCH', 'Context belongs to another input revision', status=409)
    if context and not context['is_current_bearing_revision']:
        raise MeasurementError('BEARING_REVISION_CONFLICT', 'Create a context for the current bearing parameters', status=409)
    profile = None
    if payload.tool_name == 'analyze_envelope':
        profile, base = resolve_profile(connection, current, payload.profile_id, revision)
        parameters = EnvelopeParameters.model_validate(dict(base.model_dump(), execution_mode=payload.mode,
            acquisition=context['acquisition'] if context else None))
        version = ENVELOPE_VERSION
    elif payload.tool_name == 'calculate_bearing_frequencies':
        parameters = context['frequency_parameters']
        version = FREQUENCY_VERSION
    else:
        parameters = dict(config_profile=payload.config_profile,configuration=dict(matching_service.CONFIG))
        version = matching_service.VERSION
    snapshot = dict(organization_id=str(current.organization_id), requested_by=str(current.user_id),
        measurement_revision_id=str(revision['id']), input_sha256=record['sha256'], input_byte_size=record['byte_size'],
        file_object_id=str(record['file_object_id']), confirmation_id=str(payload.confirmation_id),
        revision={k:revision[k] for k in ('sampling_rate_hz','acceleration_unit','rpm','asset_id','measurement_point_id','source_type')},
        context=context['context'] if context else None, bearing=context['bearing'] if context else None,
        profile=dict(profile) if profile else None, parameters=parameters.model_dump(mode='json') if hasattr(parameters,'model_dump') else parameters,
        tool_name=payload.tool_name, tool_version=version)
    if payload.tool_name == 'match_frequency_evidence':
        snapshot['parents']=matching_service.accept_parents(connection,current,payload,record)
    snapshot = json.loads(json.dumps(snapshot, default=str, allow_nan=False))
    return revision, record, parameters, version, snapshot


def create_run(payload, key, principal, request_id):
    payload = REQUEST.validate_python(payload)
    if not key or len(key)>128 or any(ord(c)<33 or ord(c)>126 for c in key):
        raise MeasurementError('IDEMPOTENCY_KEY_INVALID', 'Idempotency-Key requires 1–128 visible ASCII characters')
    runtime.ensure()
    request_hash = sha256(json_bytes(payload.model_dump(mode='json'))).hexdigest()
    key_hash = sha256(key.encode()).hexdigest()
    advisory = int.from_bytes(sha256(f'{principal.organization_id}/{principal.user_id}/tools/{key_hash}'.encode()).digest()[:8], 'big', signed=True)
    acquired = False; identifier = None; existing_id = None
    try:
        with transaction() as connection:
            current = fresh(connection, principal, write=True)
            connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), dict(key=advisory))
            existing = connection.execute(text('''SELECT * FROM tool_idempotency_records
                WHERE organization_id=:org AND actor_id=:actor AND key_hash=:key'''),
                dict(org=current.organization_id, actor=current.user_id, key=key_hash)).mappings().first()
            if existing:
                if existing['request_hash'] != request_hash:
                    raise MeasurementError('IDEMPOTENCY_CONFLICT', 'Same key was used for a different tool request', status=409)
                authorized_run(connection, current, existing['resource_id'])
                existing_id = existing['resource_id']
            else:
                revision, record, parameters, version, snapshot = prepare(connection, current, payload)
                bindings=parameters if payload.tool_name=='compare_measurements' else {}
                if payload.retry_of:
                    old, _, _ = authorized_run(connection, current, payload.retry_of)
                    if old['status'] not in {'failed','interrupted'} or old['measurement_revision_id'] != revision['id'] or old['tool_name'] != payload.tool_name:
                        raise MeasurementError('RETRY_INVALID', 'Retry must reference a failed/interrupted run of this input and tool', status=409)
                    if payload.tool_name=='compare_measurements' and old['comparison_declaration_id']!=bindings['comparison_declaration_id']:
                        raise MeasurementError('RETRY_INVALID','Retry must use the same comparison declaration',status=409)
                if not capacity.acquire(blocking=False):
                    raise MeasurementError('ANALYSIS_CAPACITY', 'Shared analysis capacity is full', status=429)
                acquired = True
                identifier = uuid4()
                fingerprint = sha256(json_bytes(snapshot)).hexdigest()
                connection.execute(text('''INSERT INTO tool_runs
                    (id,organization_id,measurement_revision_id,confirmation_id,context_id,profile_id,requested_by,
                     tool_name,tool_version,retry_of,status,input_fingerprint,input_snapshot,envelope_run_id,frequency_run_id,basic_analysis_id,
                     comparison_declaration_id,before_revision_id,before_confirmation_id)
                    VALUES (:id,:org,:revision,:confirmation,:context,:profile,:actor,:tool,:version,:retry,'running',:hash,CAST(:snapshot AS jsonb),:envelope,:frequency,:basic,:declaration,:before_revision,:before_confirmation)'''),
                    dict(id=identifier, org=current.organization_id, revision=revision['id'], confirmation=payload.confirmation_id,
                         context=getattr(payload,'context_id',None) or bindings.get('context_id'), profile=getattr(payload,'profile_id',None), actor=current.user_id,
                         tool=payload.tool_name, version=version, retry=payload.retry_of, hash=fingerprint, snapshot=json.dumps(snapshot, allow_nan=False),
                         envelope=getattr(payload,'envelope_run_id',None),frequency=getattr(payload,'frequency_run_id',None),basic=getattr(payload,'basic_analysis_id',None),
                         declaration=bindings.get('comparison_declaration_id'),before_revision=bindings.get('before_revision_id'),before_confirmation=bindings.get('before_confirmation_id')))
                connection.execute(text('''INSERT INTO tool_idempotency_records
                    (organization_id,actor_id,key_hash,request_hash,resource_id,state) VALUES (:org,:actor,:key,:hash,:id,'running')'''),
                    dict(org=current.organization_id, actor=current.user_id, key=key_hash, hash=request_hash, id=identifier))
                audit(connection, current.organization_id, current.user_id, 'tool.accepted', 'tool_run', identifier, request_id)
        if existing_id:
            result = read_run(existing_id, principal)
            return result, 202 if result['status']=='running' else 200
        started = perf_counter()
        try:
            raw = original_bytes(FileStore(), record)
            if payload.tool_name == 'analyze_envelope':
                computed = analyze_envelope(parse_csv(raw).values, parameters)
            elif payload.tool_name == 'calculate_bearing_frequencies':
                computed = calculate_bearing_frequencies(parameters)
            elif payload.tool_name=='compare_measurements':
                computed=comparison_service.compute(snapshot,principal)
            else:
                computed = matching_service.compute(snapshot,principal)
            if computed.status == 'failed':
                fail_run(identifier, computed.error_code or 'TOOL_NUMERIC_FAILED')
            else:
                metadata = computed.metadata()
                result = dict(metadata, tool_execution_id=str(identifier), measurement_revision_id=str(revision['id']),
                    input_sha256=record['sha256'], input_fingerprint=fingerprint,
                    compute_elapsed_seconds=perf_counter()-started,
                    provenance_notice='Engineering declarations are not independent source authentication or field calibration')
                facts=[]; evidence=[]
                if payload.tool_name == 'match_frequency_evidence':
                    facts,evidence=evidence_service.graph(identifier,result)
                    result['fact_refs']=[item['id'] for item in facts]
                    result['evidence_refs']=[item['id'] for item in evidence]
                artifacts = ToolStore().publish(current.organization_id, identifier, result, computed)
                runtime.ensure()
                if payload.tool_name == 'match_frequency_evidence':
                    matching_service.load_parents(snapshot,principal,require_active=True)
                elif payload.tool_name=='compare_measurements':
                    comparison_service.load_parents(snapshot,principal,require_active=True)
                with transaction() as connection:
                    current = fresh(connection, principal, write=True)
                    run, _, accepted_record = authorized_run(connection, current, identifier, write=True, lock=True)
                    if run['status'] != 'running':
                        raise MeasurementError('TOOL_INTERRUPTED', 'Tool execution is no longer running', status=409)
                    if not confirmation_active(connection, run):
                        raise MeasurementError('CONFIRMATION_REVOKED', 'Input confirmation was revoked during execution', status=409)
                    original_bytes(FileStore(), accepted_record)
                    if payload.tool_name == 'match_frequency_evidence':
                        if matching_service.accept_parents(connection,current,payload,accepted_record)!=snapshot['parents']:
                            matching_service.mismatch()
                    elif payload.tool_name=='compare_measurements':
                        comparison_service.recheck(connection,current,run)
                    ToolStore().verified(artifacts, arrays_expected=bool(computed.arrays))
                    for item in artifacts:
                        connection.execute(text('''INSERT INTO tool_artifacts
                            (id,organization_id,tool_run_id,kind,storage_key,sha256,byte_size,format,state)
                            VALUES (:id,:org,:run,:kind,:storage_key,:sha256,:byte_size,:format,:state)'''),
                            dict(item, org=current.organization_id, run=identifier))
                    if payload.tool_name == 'match_frequency_evidence':
                        evidence_service.register(connection,run,facts,evidence)
                    connection.execute(text('''UPDATE tool_runs SET status='completed',result_status=:status,
                        result_metadata=CAST(:metadata AS jsonb),finished_at=now(),error_code=:code WHERE id=:id'''),
                        dict(id=identifier, status=computed.status, metadata=json.dumps(result, allow_nan=False), code=computed.error_code))
                    connection.execute(text("UPDATE tool_idempotency_records SET state='completed' WHERE resource_id=:id"), dict(id=identifier))
                    audit(connection, current.organization_id, current.user_id, 'tool.completed', 'tool_run', identifier, request_id)
        except MeasurementError as error:
            fail_run(identifier, error.code)
            if error.status in {401,403,404,503}:
                raise
        except ToolInputError as error:
            fail_run(identifier, error.code)
        except OSError:
            fail_run(identifier, 'STORAGE_UNAVAILABLE')
        except Exception:
            fail_run(identifier, 'TOOL_INTERNAL_FAILED')
            raise MeasurementError('TOOL_UNAVAILABLE', 'Tool execution failed; replay the same key to inspect its state', status=503) from None
        return read_run(identifier, principal), 201
    except Exception:
        if identifier:
            try:
                fail_run(identifier, 'EXECUTION_UNCERTAIN')
            except Exception:
                pass
        raise
    finally:
        if acquired:
            capacity.release()


def read_bundle(identifier, principal):
    with transaction() as connection:
        current = fresh(connection, principal)
        run, revision, record = authorized_run(connection, current, identifier)
        active = confirmation_active(connection, run)
        artifacts = connection.execute(text('SELECT * FROM tool_artifacts WHERE tool_run_id=:id AND organization_id=:org ORDER BY kind'),
            dict(id=identifier, org=current.organization_id)).mappings().all()
    blobs = {}; parent_confirmations_active = True
    if run['status'] == 'completed':
        original_bytes(FileStore(), record)
        blobs = ToolStore().verified(artifacts, arrays_expected=run['tool_name']=='analyze_envelope' and run['result_status']=='ok')
        try:
            result = json.loads(blobs['result']); manifest = json.loads(blobs['manifest'])
            expected = dict(tool_execution_id=str(identifier), measurement_revision_id=str(revision['id']),
                input_sha256=run['input_snapshot']['input_sha256'], input_fingerprint=run['input_fingerprint'],
                tool_name=run['tool_name'], tool_version=run['tool_version'])
            if any(result.get(k)!=v or manifest.get(k)!=v for k,v in expected.items()) or result != run['result_metadata']:
                raise ValueError()
        except (ValueError, TypeError):
            raise MeasurementError('ARTIFACT_INTEGRITY', 'Tool output does not match its accepted snapshot', status=409) from None
        if run['tool_name']=='match_frequency_evidence':
            _,parent_confirmations_active=matching_service.load_parents(run['input_snapshot'],principal,require_active=False)
        elif run['tool_name']=='compare_measurements':
            _,parent_confirmations_active=comparison_service.load_parents(run['input_snapshot'],principal,require_active=False)
    with transaction() as connection:
        current = fresh(connection, principal)
        _, _, record = authorized_run(connection, current, identifier)
        active = confirmation_active(connection, run) and parent_confirmations_active
    return run, record, active, artifacts, blobs


def read_run(identifier, principal):
    run, record, active, artifacts, _ = read_bundle(identifier, principal)
    return dict(tool_execution_id=str(run['id']), tool_name=run['tool_name'], tool_version=run['tool_version'],
        status=run['status'], result_status=run['result_status'], measurement_id=str(record['id']),
        measurement_revision_id=str(run['measurement_revision_id']), context_id=run['context_id'], profile_id=run['profile_id'],
        is_current_revision=record['current_revision_id']==run['measurement_revision_id'],
        is_current_bearing_revision=run.get('is_current_bearing_revision'),
        input_confirmation_active=active, retry_of=run['retry_of'], requested_by=run['requested_by'],
        started_at=run['started_at'], finished_at=run['finished_at'], input_fingerprint=run['input_fingerprint'],
        input_snapshot=run['input_snapshot'], result=run['result_metadata'], error_code=run['error_code'],
        envelope_run_id=run['envelope_run_id'],frequency_run_id=run['frequency_run_id'],basic_analysis_id=run['basic_analysis_id'],
        declaration_id=run['comparison_declaration_id'],before_measurement_revision_id=run['before_revision_id'],
        before_measurement_id=run.get('before_measurement_id'),is_current_before_revision=run.get('is_current_before_revision'),
        fact_refs=(run['result_metadata'] or {}).get('fact_refs',[]),evidence_refs=(run['result_metadata'] or {}).get('evidence_refs',[]),
        artifacts=[dict(kind=a['kind'],sha256=a['sha256'],byte_size=a['byte_size'],
            url=f'/api/v1/tool-runs/{identifier}/artifacts/{a["kind"]}') for a in artifacts])


def list_runs(measurement_id, principal, page, page_size):
    with transaction() as connection:
        current = fresh(connection, principal)
        authorized_measurement(connection, current, measurement_id)
        rows = connection.execute(text('''SELECT t.id AS tool_execution_id,t.tool_name,t.tool_version,t.status,t.result_status,
            t.measurement_revision_id,t.context_id,t.profile_id,t.started_at,t.finished_at,t.error_code,t.retry_of
            FROM tool_runs t JOIN measurement_revisions r ON r.id=t.measurement_revision_id AND r.organization_id=t.organization_id
            WHERE r.measurement_id=:measurement AND t.organization_id=:org AND (r.asset_id IS NULL OR EXISTS
            (SELECT 1 FROM assets a JOIN asset_grants g ON g.asset_id=a.id AND g.organization_id=a.organization_id
             WHERE a.id=r.asset_id AND a.status='active' AND g.user_id=:actor AND g.can_read))
            AND (t.before_revision_id IS NULL OR EXISTS (
             SELECT 1 FROM measurement_revisions br JOIN measurements bm ON bm.id=br.measurement_id AND bm.organization_id=br.organization_id
             JOIN measurement_revisions cr ON cr.id=bm.current_revision_id AND cr.organization_id=bm.organization_id
             WHERE br.id=t.before_revision_id AND br.organization_id=t.organization_id AND bm.state='ready'
             AND (bm.owner_id=:actor OR EXISTS(SELECT 1 FROM measurement_grants mg WHERE mg.measurement_id=bm.id AND mg.organization_id=bm.organization_id AND mg.user_id=:actor AND mg.can_read))
             AND (br.asset_id IS NULL OR EXISTS(SELECT 1 FROM asset_grants ag JOIN assets a ON a.id=ag.asset_id AND a.organization_id=ag.organization_id WHERE a.id=br.asset_id AND a.status='active' AND ag.user_id=:actor AND ag.can_read))
             AND (cr.asset_id IS NULL OR EXISTS(SELECT 1 FROM asset_grants ag JOIN assets a ON a.id=ag.asset_id AND a.organization_id=ag.organization_id WHERE a.id=cr.asset_id AND a.status='active' AND ag.user_id=:actor AND ag.can_read))))
            ORDER BY t.started_at DESC,t.id LIMIT :limit OFFSET :offset'''),
            dict(measurement=measurement_id,org=current.organization_id,actor=current.user_id,limit=page_size,offset=(page-1)*page_size)).mappings().all()
    return dict(items=[dict(r) for r in rows], page=page, page_size=page_size)


def series(identifier, kind, principal):
    run, _, _, _, blobs = read_bundle(identifier, principal)
    if 'arrays' not in blobs:
        raise MeasurementError('RESULT_NOT_READY', 'This tool outcome has no numerical series', status=409)
    with np.load(io.BytesIO(blobs['arrays']), allow_pickle=False) as arrays:
        if kind == 'envelope':
            return run['result_metadata']['summary']['envelope_preview']
        frequency = arrays['frequency_hz']; amplitude = arrays['amplitude_m_s2']
        indices = sorted({int(i) for group in np.array_split(np.arange(len(frequency)), min(1000,len(frequency)))
            for i in (group[np.argmin(amplitude[group])], group[np.argmax(amplitude[group])])})
        return dict(frequency_hz=frequency[indices].tolist(), amplitude_m_s2=amplitude[indices].tolist(),
            unit='m/s2', full_bin_count=len(frequency), max_points=2000, display_only=True, method='bucket_min_max')


def artifact(identifier, kind, principal, request_id):
    if kind not in KINDS:
        not_found()
    _, _, _, _, blobs = read_bundle(identifier, principal)
    if kind not in blobs:
        raise MeasurementError('RESULT_NOT_READY', 'This outcome has no such artifact', status=409)
    with transaction() as connection:
        current = fresh(connection, principal)
        authorized_run(connection, current, identifier)
        audit(connection, current.organization_id, current.user_id, 'tool.download_artifact', 'tool_run', identifier, request_id)
    return blobs[kind]
