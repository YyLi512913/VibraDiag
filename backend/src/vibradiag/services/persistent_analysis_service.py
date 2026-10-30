"""Bounded synchronous analysis with durable acceptance, snapshots and idempotency."""
from hashlib import sha256
import io
import json
from uuid import uuid4

import numpy as np
from sqlalchemy import text

from vibradiag.analysis.basic import ALGORITHM_VERSION, CONFIG, compute_basic
from vibradiag.analysis.csv_input import parse_csv
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.analysis_store import AnalysisStore, KINDS, json_bytes
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.services.analysis_runtime import runtime
from vibradiag.services.identity_service import asset_allowed, audit
from vibradiag.services.measurement_service import (authorized_measurement, fresh, not_found,
                                                  original_bytes, revision_payload)

from vibradiag.infrastructure.compute import runner as numeric
from vibradiag.services.compute_capacity import capacity as _capacity
CONFIG_HASH=sha256(json_bytes(CONFIG)).hexdigest()


def authorized_analysis(connection,principal,identifier,*,write=False,lock=False):
    run=connection.execute(text('''SELECT a.*,r.measurement_id,r.asset_id,c.revoked_at AS confirmation_revoked_at
        FROM analysis_runs a JOIN measurement_revisions r ON r.id=a.measurement_revision_id AND r.organization_id=a.organization_id
        JOIN input_confirmations c ON c.id=a.confirmation_id AND c.organization_id=a.organization_id
        WHERE a.id=:id AND a.organization_id=:org'''+(' FOR UPDATE OF a' if lock else '')),
        dict(id=identifier,org=principal.organization_id)).mappings().first()
    if not run:
        not_found()
    record=authorized_measurement(connection,principal,run['measurement_id'],write=write)
    if run['asset_id'] and not asset_allowed(connection,principal,run['asset_id']):
        not_found()
    return run,record


def fail_run(identifier,code):
    with transaction() as connection:
        run=connection.execute(text('SELECT * FROM analysis_runs WHERE id=:id FOR UPDATE'),dict(id=identifier)).mappings().one()
        if run['status']!='running':
            return
        connection.execute(text("UPDATE analysis_runs SET status='failed',finished_at=now(),error_code=:code WHERE id=:id"),
                           dict(id=identifier,code=code))
        connection.execute(text("""UPDATE idempotency_records SET state='failed',response_summary=CAST(:summary AS jsonb)
            WHERE resource_id=:id"""),dict(id=identifier,summary=json.dumps({'error_code':code})))
        audit(connection,run['organization_id'],None,'analysis.failed','analysis',identifier)


def create_analysis(payload,key,principal,request_id):
    if not key or len(key)>128 or any(ord(c)<33 or ord(c)>126 for c in key):
        raise MeasurementError('IDEMPOTENCY_KEY_INVALID','Idempotency-Key must contain 1–128 visible ASCII characters')
    runtime.ensure()
    request_hash=sha256(json_bytes(payload.model_dump(mode='json'))).hexdigest()
    key_hash=sha256(key.encode()).hexdigest()
    lock_hash=sha256(f'{principal.organization_id}/{principal.user_id}/basic-analysis/{key_hash}'.encode()).digest()
    advisory_key=int.from_bytes(lock_hash[:8],'big',signed=True)
    acquired=False;identifier=None;existing_id=None
    try:
        with transaction() as connection:
            current=fresh(connection,principal,write=True)
            connection.execute(text('SELECT pg_advisory_xact_lock(:key)'),dict(key=advisory_key))
            existing=connection.execute(text('''SELECT * FROM idempotency_records WHERE organization_id=:org
                AND actor_id=:actor AND operation='basic-analysis' AND key_hash=:key'''),
                dict(org=current.organization_id,actor=current.user_id,key=key_hash)).mappings().first()
            if existing:
                if existing['request_hash']!=request_hash:
                    raise MeasurementError('IDEMPOTENCY_CONFLICT','Same key was used for a different request',status=409)
                authorized_analysis(connection,current,existing['resource_id'])
                existing_id=existing['resource_id']
            else:
                revision=connection.execute(text('SELECT * FROM measurement_revisions WHERE id=:id AND organization_id=:org'),
                    dict(id=payload.measurement_revision_id,org=current.organization_id)).mappings().first()
                if not revision:
                    not_found()
                record=authorized_measurement(connection,current,revision['measurement_id'],write=True,lock=True)
                if record['current_revision_id']!=revision['id']:
                    raise MeasurementError('REVISION_CONFLICT','New analysis requires the current input revision',status=409)
                confirmation=connection.execute(text('''SELECT 1 FROM input_confirmations WHERE id=:id
                    AND revision_id=:revision AND organization_id=:org AND actor_id=:actor AND revoked_at IS NULL'''),
                    dict(id=payload.confirmation_id,revision=revision['id'],org=current.organization_id,actor=current.user_id)).first()
                if not confirmation:
                    raise MeasurementError('CONFIRMATION_INVALID','Confirm this input revision with your own account before analysis',status=409)
                if payload.retry_of:
                    old,_=authorized_analysis(connection,current,payload.retry_of)
                    if old['status'] not in {'failed','interrupted'} or old['measurement_revision_id']!=revision['id']:
                        raise MeasurementError('RETRY_INVALID','Retry must refer to a failed/interrupted run of the same revision',status=409)
                if not _capacity.acquire(blocking=False):
                    raise MeasurementError('ANALYSIS_CAPACITY','Analysis capacity is full; retry later',status=429)
                acquired=True
                identifier=uuid4()
                snapshot={'input_sha256':record['sha256'],'input_byte_size':record['byte_size'],
                          'file_object_id':str(record['file_object_id']),'revision':json.loads(json.dumps(revision_payload(revision),default=str)),
                          'confirmation_id':str(payload.confirmation_id),'configuration':dict(CONFIG)}
                connection.execute(text('''INSERT INTO analysis_runs(id,organization_id,measurement_revision_id,confirmation_id,
                    requested_by,retry_of,status,algorithm_version,config_hash,config_snapshot)
                    VALUES (:id,:org,:revision,:confirmation,:actor,:retry,'running',:algorithm,:hash,CAST(:snapshot AS jsonb))'''),
                    dict(id=identifier,org=current.organization_id,revision=revision['id'],confirmation=payload.confirmation_id,
                         actor=current.user_id,retry=payload.retry_of,algorithm=ALGORITHM_VERSION,hash=CONFIG_HASH,
                         snapshot=json.dumps(snapshot,allow_nan=False)))
                connection.execute(text('''INSERT INTO idempotency_records
                    (organization_id,actor_id,operation,key_hash,request_hash,resource_id,state)
                    VALUES (:org,:actor,'basic-analysis',:key,:request,:id,'running')'''),
                    dict(org=current.organization_id,actor=current.user_id,key=key_hash,request=request_hash,id=identifier))
                audit(connection,current.organization_id,current.user_id,'analysis.accepted','analysis',identifier,request_id)
        if existing_id:
            result=read_analysis(existing_id,principal)
            return result,202 if result['status']=='running' else 200
        try:
            raw=original_bytes(FileStore(),record)
            def live():
                runtime.ensure()
                with transaction() as connection:
                    current=fresh(connection,principal,write=True)
                    run,_=authorized_analysis(connection,current,identifier,write=True)
                    if run['status']!='running':raise MeasurementError('ANALYSIS_INTERRUPTED','Run no longer active',status=409)
                    if run['confirmation_revoked_at'] is not None:raise MeasurementError('CONFIRMATION_REVOKED','Input confirmation revoked',status=409)
            computed=numeric.run('basic',dict(raw=raw,sampling_rate=revision['sampling_rate_hz'],unit=revision['acceleration_unit']),
                lambda:compute_basic(parse_csv(raw).values,revision['sampling_rate_hz'],revision['acceleration_unit']),check=live)
            result=dict(computed.summary,analysis_id=str(identifier),measurement_revision_id=str(revision['id']),
                        input_sha256=record['sha256'],algorithm_version=ALGORITHM_VERSION,config_hash=CONFIG_HASH,compute_execution=numeric.execution_receipt())
            artifacts=AnalysisStore().publish(current.organization_id,identifier,result,computed)
            runtime.ensure()
            with transaction() as connection:
                current=fresh(connection,principal,write=True)
                run,_=authorized_analysis(connection,current,identifier,write=True,lock=True)
                if run['status']!='running':
                    raise MeasurementError('ANALYSIS_INTERRUPTED','Run is no longer active',status=409)
                if run['confirmation_revoked_at'] is not None:
                    raise MeasurementError('CONFIRMATION_REVOKED','Input confirmation was revoked',status=409)
                # Verify durable input and all outputs before registering completed.
                original_bytes(FileStore(),record)
                AnalysisStore().verified([dict(item,state='ready') for item in artifacts])
                for item in artifacts:
                    connection.execute(text('''INSERT INTO analysis_artifacts
                        (id,organization_id,analysis_id,kind,storage_key,sha256,byte_size,format,manifest,state)
                        VALUES (:id,:org,:run,:kind,:storage_key,:sha256,:byte_size,:format,'{}','ready')'''),
                        dict(item,org=current.organization_id,run=identifier))
                connection.execute(text("""UPDATE analysis_runs SET status='completed',finished_at=now(),
                    metrics=CAST(:metrics AS jsonb),warnings=CAST(:warnings AS jsonb) WHERE id=:id"""),
                    dict(id=identifier,metrics=json.dumps(computed.summary['metrics'],allow_nan=False),
                         warnings=json.dumps(computed.summary['warnings'])))
                connection.execute(text("""UPDATE idempotency_records SET state='completed',
                    response_summary=CAST(:summary AS jsonb) WHERE resource_id=:id"""),
                    dict(id=identifier,summary=json.dumps({'analysis_id':str(identifier),'status':'completed'})))
                audit(connection,current.organization_id,current.user_id,'analysis.completed','analysis',identifier,request_id)
        except MeasurementError as error:
            fail_run(identifier,error.code)
            if error.status in {401,403,404,503}:
                raise
        except OSError:
            fail_run(identifier,'STORAGE_UNAVAILABLE')
        except Exception:
            # If a commit actually succeeded, fail_run preserves the completed record.
            fail_run(identifier,'ANALYSIS_INTERNAL_FAILED')
            raise MeasurementError('ANALYSIS_UNAVAILABLE','Analysis failed; retry with the same key to inspect its state',status=503) from None
        return read_analysis(identifier,principal),201
    except Exception:
        if identifier is not None:
            try:
                fail_run(identifier,'EXECUTION_UNCERTAIN')
            except Exception:
                pass  # Database unavailable: startup recovery owns unresolved running rows.
        raise
    finally:
        if acquired:
            _capacity.release()


def read_bundle(identifier,principal):
    with transaction() as connection:
        current=fresh(connection,principal)
        run,record=authorized_analysis(connection,current,identifier)
        artifacts=connection.execute(text('SELECT * FROM analysis_artifacts WHERE analysis_id=:id AND organization_id=:org ORDER BY kind'),
            dict(id=identifier,org=current.organization_id)).mappings().all()
    blobs={}
    if run['status']=='completed':
        original_bytes(FileStore(),record)
        blobs=AnalysisStore().verified(artifacts)
        result=json.loads(blobs['result']);manifest=json.loads(blobs['manifest'])
        expected={'analysis_id':str(identifier),'measurement_revision_id':str(run['measurement_revision_id']),
                  'input_sha256':run['config_snapshot']['input_sha256'],
                  'algorithm_version':run['algorithm_version'],'config_hash':run['config_hash']}
        if any(result.get(k)!=v or manifest.get(k)!=v for k,v in expected.items()) or result.get('metrics')!=run['metrics']:
            raise MeasurementError('ARTIFACT_INTEGRITY','Result or manifest does not match its analysis snapshot',status=409)
    with transaction() as connection:
        current=fresh(connection,principal)
        authorized_analysis(connection,current,identifier)
    return run,record,artifacts,blobs


def read_analysis(identifier,principal):
    run,record,artifacts,blobs=read_bundle(identifier,principal)
    result=json.loads(blobs['result']) if blobs else None
    return {'analysis_id':str(run['id']),'status':run['status'],
            'measurement_id':str(run['measurement_id']),'measurement_revision_id':str(run['measurement_revision_id']),
            'is_current_revision':record['current_revision_id']==run['measurement_revision_id'],
            'confirmation_id':str(run['confirmation_id']),'input_confirmation_active':run['confirmation_revoked_at'] is None,
            'requested_by':str(run['requested_by']),'started_at':run['started_at'],'finished_at':run['finished_at'],
            'algorithm_version':run['algorithm_version'],'config_hash':run['config_hash'],
            'input_snapshot':run['config_snapshot'],'metrics':run['metrics'],'warnings':run['warnings'],
            'error_code':run['error_code'],'result_summary':result,
            'artifacts':[{'kind':a['kind'],'sha256':a['sha256'],'byte_size':a['byte_size'],
                          'url':f'/api/v1/analyses/{identifier}/artifacts/{a["kind"]}'} for a in artifacts]}


def list_analyses(measurement_id,principal,page,page_size):
    with transaction() as connection:
        current=fresh(connection,principal)
        authorized_measurement(connection,current,measurement_id)
        rows=connection.execute(text('''SELECT a.id AS analysis_id,a.status,a.measurement_revision_id,
            r.revision_no,a.started_at,a.finished_at,a.error_code,a.algorithm_version,a.requested_by,a.retry_of
            FROM analysis_runs a JOIN measurement_revisions r
            ON r.id=a.measurement_revision_id AND r.organization_id=a.organization_id
            WHERE r.measurement_id=:measurement AND a.organization_id=:org
            AND (r.asset_id IS NULL OR EXISTS(SELECT 1 FROM assets s JOIN asset_grants g
                ON g.asset_id=s.id AND g.organization_id=s.organization_id
                WHERE s.id=r.asset_id AND s.organization_id=r.organization_id AND s.status='active'
                AND g.user_id=:user AND g.can_read))
            ORDER BY a.started_at DESC,a.id LIMIT :limit OFFSET :offset'''),
            dict(measurement=measurement_id,org=current.organization_id,user=current.user_id,
                 limit=page_size,offset=(page-1)*page_size)).mappings().all()
    return {'items':[dict(row) for row in rows],'page':page,'page_size':page_size}


def series(identifier,kind,principal):
    run,_,_,blobs=read_bundle(identifier,principal)
    if run['status']!='completed':
        raise MeasurementError('RESULT_NOT_READY','Analysis has no completed result',status=409)
    if kind=='waveform':
        return json.loads(blobs['waveform_preview'])
    with np.load(io.BytesIO(blobs['amplitude_spectrum']),allow_pickle=False) as data:
        frequency=data['frequency_hz'];amplitude=data['amplitude_m_s2']
    indices=sorted({int(i) for group in np.array_split(np.arange(len(frequency)),min(1000,len(frequency)))
                    for i in (group[np.argmin(amplitude[group])],group[np.argmax(amplitude[group])])})
    return {'frequency_hz':frequency[indices].tolist(),'amplitude_m_s2':amplitude[indices].tolist(),
            'full_bin_count':len(frequency),'method':'bucket_min_max','display_only':True,'max_points':2000}


def artifact(identifier,kind,principal,request_id):
    run,_,_,blobs=read_bundle(identifier,principal)
    if run['status']!='completed':
        raise MeasurementError('RESULT_NOT_READY','Analysis has no completed result',status=409)
    with transaction() as connection:
        current=fresh(connection,principal)
        authorized_analysis(connection,current,identifier)
        audit(connection,current.organization_id,current.user_id,'analysis.download_artifact','analysis',identifier,request_id)
    return blobs[kind]
