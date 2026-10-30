"""Versioned measurements. No HTTP imports and no model involvement."""
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import text

from vibradiag.analysis.csv_input import make_preview, parse_csv, validate_time_axis
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.schemas.measurements import MeasurementMetadata
from vibradiag.services.identity_service import (asset_allowed, audit, measurement_allowed,
                                               refresh_principal)


def not_found():
    raise MeasurementError('RESOURCE_NOT_FOUND', 'Resource not found', status=404)


def fresh(connection, principal, *, write=False):
    current=refresh_principal(connection,principal)
    if current is None:
        raise MeasurementError('AUTH_REQUIRED','Authentication required',status=401)
    if write and not current.roles.intersection({'inspector','engineer'}):
        raise MeasurementError('ROLE_REQUIRED','An inspector or engineer role is required',status=403)
    return current


def validate_associations(connection, principal, metadata):
    if metadata.asset_id:
        # Own-input writes by inspectors are allowed with explicit asset write scope.
        row=connection.execute(text('''SELECT 1 FROM assets a JOIN asset_grants g
            ON g.asset_id=a.id AND g.organization_id=a.organization_id
            WHERE a.id=:asset AND a.organization_id=:org AND a.status='active'
            AND g.user_id=:user AND g.can_write'''),
            dict(asset=metadata.asset_id,org=principal.organization_id,user=principal.user_id)).first()
        if not row:
            not_found()
    if metadata.measurement_point_id:
        row=connection.execute(text('''SELECT 1 FROM measurement_points WHERE id=:point
            AND asset_id=:asset AND organization_id=:org AND status='active' '''),
            dict(point=metadata.measurement_point_id,asset=metadata.asset_id,org=principal.organization_id)).first()
        if not row:
            not_found()


def authorized_measurement(connection, principal, measurement_id, *, write=False, lock=False):
    row=connection.execute(text('''SELECT m.*,f.storage_key,f.sha256,f.byte_size,f.validation_summary,
        f.state AS file_state FROM measurements m JOIN file_objects f
        ON f.id=m.file_object_id AND f.organization_id=m.organization_id
        WHERE m.id=:id AND m.organization_id=:org'''+(' FOR UPDATE OF m' if lock else '')),
        dict(id=measurement_id,org=principal.organization_id)).mappings().first()
    if not row or not measurement_allowed(connection,principal,measurement_id,write=write):
        not_found()
    return row


def original_bytes(store, row):
    if row['file_state']!='ready':
        raise MeasurementError('FILE_UNAVAILABLE','Original file is not ready',status=409)
    return store.read_verified(row['storage_key'],row['sha256'],row['byte_size'])


def public_record(row):
    return {key:row[key] for key in ('id','original_filename','owner_id','current_revision_id',
            'row_version','uploaded_at','sha256','byte_size','validation_summary')}


def revision_payload(row):
    names=MeasurementMetadata.model_fields
    return {'id':row['id'],'revision_no':row['revision_no'],'parent_id':row['parent_id'],
            'metadata':{name:row[name] for name in names},'parameter_sources':row['parameter_sources'],
            'reason':row['reason'],'created_by':row['created_by'],'created_at':row['created_at']}


def insert_revision(connection, *, principal, measurement_id, revision_id, revision_no, parent_id, metadata, reason, parameter_sources=None):
    params=metadata.model_dump(mode='json')
    sources={name:{'origin':'user_input' if name in metadata.model_fields_set and value is not None else 'unknown',
                   'raw_reference':None,'confirmed_by':None,'confirmed_at':None}
             for name,value in params.items()}
    columns=list(params)
    bindings=','.join(':'+name for name in columns)
    connection.execute(text(f'''INSERT INTO measurement_revisions
        (id,organization_id,measurement_id,revision_no,parent_id,{','.join(columns)},parameter_sources,reason,created_by)
        VALUES (:id,:org,:measurement,:number,:parent,{bindings},CAST(:sources AS jsonb),:reason,:actor)'''),
        dict(params,id=revision_id,org=principal.organization_id,measurement=measurement_id,number=revision_no,
             parent=parent_id,sources=json.dumps(parameter_sources or sources,allow_nan=False),reason=reason,actor=principal.user_id))


def create_measurement(staged, filename, metadata, principal, request_id):
    raw=staged.read_bytes()
    parsed=parse_csv(raw)
    validate_time_axis(len(parsed.values),metadata.sampling_rate_hz)
    with transaction() as connection:
        measurement_id=persist_measurement(connection,staged,filename,metadata,parsed.summary,
            sha256(raw).hexdigest(),len(raw),principal,request_id)
    return read_measurement(measurement_id,principal)


def persist_measurement(connection,staged,filename,metadata,summary,checksum,byte_size,principal,request_id,parameter_sources=None):
    """Persist already-validated input inside the caller's transaction (also used by migration CLI)."""
    store=FileStore()
    measurement_id, file_id, revision_id=uuid4(),uuid4(),uuid4()
    # A database failure after promotion leaves an unreferenced original for reconciliation.
    # Never unlink published files on ambiguous commit failures.
    current=fresh(connection,principal,write=True)
    validate_associations(connection,current,metadata)
    key=store.promote(staged,current.organization_id,file_id)
    connection.execute(text('''INSERT INTO file_objects
            (id,organization_id,storage_key,sha256,byte_size,state,created_by,validation_summary)
            VALUES (:id,:org,:key,:hash,:size,'ready',:actor,CAST(:summary AS jsonb))'''),
            dict(id=file_id,org=current.organization_id,key=key,hash=checksum,size=byte_size,actor=current.user_id,
                 summary=json.dumps(summary)))
    display_name=''.join(c for c in (filename or 'signal.csv').replace('\\','/').split('/')[-1] if ord(c)>=32 and ord(c)!=127)[:255] or 'signal.csv'
    connection.execute(text('''INSERT INTO measurements(id,organization_id,file_object_id,owner_id,
            original_filename,current_revision_id,state) VALUES (:id,:org,:file,:owner,:name,NULL,'ready')'''),
            dict(id=measurement_id,org=current.organization_id,file=file_id,owner=current.user_id,name=display_name))
    insert_revision(connection,principal=current,measurement_id=measurement_id,revision_id=revision_id,
                    revision_no=1,parent_id=None,metadata=metadata,reason='Legacy import (unconfirmed)' if parameter_sources else 'Initial import',parameter_sources=parameter_sources)
    connection.execute(text('UPDATE measurements SET current_revision_id=:revision WHERE id=:id'),
                           dict(revision=revision_id,id=measurement_id))
    audit(connection,current.organization_id,current.user_id,'measurement.import','measurement',measurement_id,request_id)
    return measurement_id


def read_measurement(measurement_id, principal):
    store=FileStore()
    with transaction() as connection:
        current=fresh(connection,principal)
        row=authorized_measurement(connection,current,measurement_id)
        revision=connection.execute(text('SELECT * FROM measurement_revisions WHERE id=:id AND organization_id=:org'),
            dict(id=row['current_revision_id'],org=current.organization_id)).mappings().one()
        confirmation=connection.execute(text('''SELECT id,revision_id,actor_id,created_at FROM input_confirmations
            WHERE revision_id=:revision AND actor_id=:actor AND organization_id=:org AND revoked_at IS NULL'''),
            dict(revision=revision['id'],actor=current.user_id,org=current.organization_id)).mappings().first()
    parsed=parse_csv(original_bytes(store,row))
    preview=make_preview(parsed,revision['sampling_rate_hz'])
    with transaction() as connection:
        current=fresh(connection,principal)
        authorized_measurement(connection,current,measurement_id)
        if revision['asset_id'] and not asset_allowed(connection,current,revision['asset_id']):
            not_found()
    return dict(public_record(row),revision=revision_payload(revision),
                input_confirmation=dict(confirmation) if confirmation else None,
                preview=dict(preview,unit=revision['acceleration_unit']),
                warnings=['Input confirmation is not sensor calibration or engineering review'])


def list_measurements(principal,page,page_size):
    with transaction() as connection:
        current=fresh(connection,principal)
        rows=connection.execute(text('''SELECT m.id,m.original_filename,m.owner_id,m.current_revision_id,
            m.row_version,m.uploaded_at,f.sha256,f.byte_size,f.validation_summary
            FROM measurements m JOIN file_objects f ON f.id=m.file_object_id AND f.organization_id=m.organization_id
            JOIN measurement_revisions r ON r.id=m.current_revision_id AND r.organization_id=m.organization_id
            WHERE m.organization_id=:org AND m.state='ready'
            AND (m.owner_id=:user OR EXISTS(SELECT 1 FROM measurement_grants g WHERE g.measurement_id=m.id
                AND g.organization_id=m.organization_id AND g.user_id=:user AND g.can_read))
            AND (r.asset_id IS NULL OR EXISTS(SELECT 1 FROM assets a JOIN asset_grants ag
                ON ag.asset_id=a.id AND ag.organization_id=a.organization_id
                WHERE a.id=r.asset_id AND a.organization_id=m.organization_id AND a.status='active'
                AND ag.user_id=:user AND ag.can_read))
            ORDER BY m.uploaded_at DESC,m.id LIMIT :limit OFFSET :offset'''),
            dict(org=current.organization_id,user=current.user_id,limit=page_size,offset=(page-1)*page_size)).mappings().all()
    return {'items':[public_record(row) for row in rows],'page':page,'page_size':page_size}


def list_revisions(measurement_id,principal,page,page_size):
    with transaction() as connection:
        current=fresh(connection,principal)
        authorized_measurement(connection,current,measurement_id)
        rows=connection.execute(text('''SELECT r.* FROM measurement_revisions r WHERE r.measurement_id=:id
            AND r.organization_id=:org AND (r.asset_id IS NULL OR EXISTS(SELECT 1 FROM assets a JOIN asset_grants g
            ON g.asset_id=a.id AND g.organization_id=a.organization_id WHERE a.id=r.asset_id
            AND a.organization_id=r.organization_id AND a.status='active' AND g.user_id=:user AND g.can_read))
            ORDER BY r.revision_no DESC LIMIT :limit OFFSET :offset'''),
            dict(id=measurement_id,org=current.organization_id,user=current.user_id,limit=page_size,offset=(page-1)*page_size)).mappings().all()
    return {'items':[revision_payload(row) for row in rows],'page':page,'page_size':page_size}


def revise_measurement(measurement_id,payload,principal,request_id):
    with transaction() as connection:
        current=fresh(connection,principal,write=True)
        row=authorized_measurement(connection,current,measurement_id,write=True,lock=True)
        if row['current_revision_id']!=payload.expected_current_revision_id or row['row_version']!=payload.row_version:
            raise MeasurementError('REVISION_CONFLICT','Input was revised; reload before editing',status=409)
        original_bytes(FileStore(),row)
        validate_associations(connection,current,payload.metadata)
        count=row['validation_summary'].get('sample_count')
        if not count:
            raise MeasurementError('INPUT_NOT_VALIDATED','Reimport this legacy input before revising',status=409)
        validate_time_axis(count,payload.metadata.sampling_rate_hz)
        previous=connection.execute(text('SELECT revision_no FROM measurement_revisions WHERE id=:id'),
                                    dict(id=row['current_revision_id'])).scalar_one()
        identifier=uuid4()
        insert_revision(connection,principal=current,measurement_id=measurement_id,revision_id=identifier,
                        revision_no=previous+1,parent_id=row['current_revision_id'],metadata=payload.metadata,reason=payload.reason.strip())
        connection.execute(text('UPDATE measurements SET current_revision_id=:revision,row_version=row_version+1 WHERE id=:id'),
                           dict(revision=identifier,id=measurement_id))
        audit(connection,current.organization_id,current.user_id,'measurement.revise','measurement',measurement_id,request_id)
    return read_measurement(measurement_id,principal)


def confirm_input(measurement_id,revision_id,principal,request_id):
    with transaction() as connection:
        current=fresh(connection,principal,write=True)
        row=authorized_measurement(connection,current,measurement_id,write=True,lock=True)
        if row['current_revision_id']!=revision_id:
            raise MeasurementError('REVISION_CONFLICT','Only the current input revision can be confirmed',status=409)
        # Integrity verification precedes confirmation; this is still not calibration.
        original_bytes(FileStore(),row)
        existing=connection.execute(text('''SELECT id,revision_id,actor_id,created_at,revoked_at FROM input_confirmations
            WHERE organization_id=:org AND revision_id=:revision AND actor_id=:actor'''),
            dict(org=current.organization_id,revision=revision_id,actor=current.user_id)).mappings().first()
        if existing and existing['revoked_at']:
            raise MeasurementError('CONFIRMATION_REVOKED','This input confirmation was revoked',status=409)
        if existing:
            return {key:existing[key] for key in ('id','revision_id','actor_id','created_at')}
        identifier=uuid4()
        result=connection.execute(text('''INSERT INTO input_confirmations(id,organization_id,revision_id,actor_id)
            VALUES (:id,:org,:revision,:actor) RETURNING id,revision_id,actor_id,created_at'''),
            dict(id=identifier,org=current.organization_id,revision=revision_id,actor=current.user_id)).mappings().one()
        audit(connection,current.organization_id,current.user_id,'measurement.confirm_input','confirmation',identifier,request_id)
        return dict(result)


def download_original(measurement_id,principal,request_id):
    with transaction() as connection:
        current=fresh(connection,principal)
        row=authorized_measurement(connection,current,measurement_id)
    raw=original_bytes(FileStore(),row)
    with transaction() as connection:
        current=fresh(connection,principal)
        authorized_measurement(connection,current,measurement_id)
        audit(connection,current.organization_id,current.user_id,'measurement.download_original','measurement',measurement_id,request_id)
    return raw
