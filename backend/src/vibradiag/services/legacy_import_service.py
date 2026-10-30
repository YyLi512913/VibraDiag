"""Trusted local operation. Never accepts prototype features or diagnosis as validated facts."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
from uuid import uuid4

from sqlalchemy import text
from pydantic import ValidationError

from vibradiag.analysis.csv_input import MAX_FILE_BYTES, parse_csv, validate_time_axis
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.analysis_store import json_bytes
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.schemas.measurements import MeasurementMetadata
from vibradiag.services.identity_service import Principal, audit, digest
from vibradiag.services.measurement_service import authorized_measurement, fresh, persist_measurement, validate_associations


@contextmanager
def local_actor(actor_id):
    with transaction() as connection:
        user=connection.execute(text("SELECT * FROM users WHERE id=:id AND status='active'"),dict(id=actor_id)).mappings().one()
        roles=frozenset(connection.execute(text('SELECT role FROM role_assignments WHERE user_id=:id'),dict(id=actor_id)).scalars())
        if 'engineer' not in roles:
            raise MeasurementError('ROLE_REQUIRED','Legacy imports require an explicitly assigned engineer',status=403)
        session_id=uuid4();csrf=digest(secrets.token_urlsafe(32))
        connection.execute(text('''INSERT INTO auth_sessions(id,user_id,organization_id,token_hash,csrf_hash,expires_at)
            VALUES (:id,:actor,:org,:token,:csrf,:expires)'''),dict(id=session_id,actor=actor_id,org=user['organization_id'],
            token=digest(secrets.token_urlsafe(32)),csrf=csrf,expires=datetime.now(timezone.utc)+timedelta(hours=1)))
        audit(connection,user['organization_id'],actor_id,'legacy.local_session','session',session_id)
    try:
        yield Principal(actor_id,user['organization_id'],session_id,roles,csrf)
    finally:
        with transaction() as connection:
            connection.execute(text('UPDATE auth_sessions SET revoked_at=now() WHERE id=:id'),dict(id=session_id))


def inspect_legacy(path,overrides):
    if not re.fullmatch('[a-f0-9]{32}',path.stem) or path.is_symlink() or path.stat().st_size>128*1024*1024:
        raise MeasurementError('LEGACY_RECORD_INVALID','Invalid legacy record reference')
    record=json.loads(path.read_text(encoding='utf8'))
    if not isinstance(record,dict) or not isinstance(record.get('original_filename','legacy.csv'),str):
        raise MeasurementError('LEGACY_RECORD_INVALID','Legacy record must be an object with a valid filename')
    if record.get('analysis_id')!=path.stem:
        raise MeasurementError('LEGACY_ID_MISMATCH','Legacy ID does not match filename')
    csv=path.with_suffix('.csv')
    if csv.is_symlink() or not csv.is_file() or csv.stat().st_size>MAX_FILE_BYTES:
        raise MeasurementError('LEGACY_FILE_INVALID','Legacy CSV missing or exceeds limit')
    raw=csv.read_bytes();checksum=sha256(raw).hexdigest()
    if record.get('sha256')!=checksum:
        raise MeasurementError('LEGACY_HASH_MISMATCH','Legacy original checksum mismatch')
    metadata={'sampling_rate_hz':record.get('sampling_rate_hz'),'acceleration_unit':record.get('input_unit'),
              'source_type':'unknown','source_note':f'Legacy prototype {path.stem}; original source unverified'}
    metadata.update(overrides)
    try:
        model=MeasurementMetadata.model_validate(metadata)
    except ValidationError:
        raise MeasurementError('LEGACY_METADATA_INVALID','Reliable sampling rate/unit required; supply explicit manifest metadata') from None
    parsed=parse_csv(raw);validate_time_axis(len(parsed.values),model.sampling_rate_hz)
    sources={name:{'origin':'user_input' if name in overrides else 'legacy_unverified',
        'raw_reference':f'legacy:{path.stem}','confirmed_by':None,'confirmed_at':None} for name in model.model_dump()}
    return record,raw,model,parsed.summary,checksum,sources


def import_directory(directory,principal,batch_id,*,apply=False,overrides=None):
    root=Path(directory).resolve()
    if not root.is_dir() or not 1<=len(batch_id)<=128:
        raise ValueError('Existing source directory and bounded batch identifier required')
    scope=sha256(os.path.normcase(str(root)).encode()).hexdigest()
    outcomes=[];overrides=overrides or {}
    if not isinstance(overrides,dict) or any(not isinstance(value,dict) for value in overrides.values()):
        raise ValueError('Manifest must map legacy IDs to metadata objects')
    for path in sorted(root.glob('*.json')):
        identifier=path.stem
        staged=None
        try:
            record,raw,metadata,summary,checksum,sources=inspect_legacy(path,overrides.get(identifier,{}))
            metadata_hash=sha256(json_bytes(metadata.model_dump(mode='json'))).hexdigest()
            with transaction() as connection:
                current=fresh(connection,principal,write=True)
                if 'engineer' not in current.roles:
                    raise MeasurementError('ROLE_REQUIRED','Engineer role required',status=403)
                connection.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                    dict(key=int.from_bytes(sha256(f'{current.organization_id}/{scope}/{identifier}'.encode()).digest()[:8],'big',signed=True)))
                existing=connection.execute(text('''SELECT * FROM legacy_imports WHERE organization_id=:org
                    AND source_scope=:scope AND legacy_id=:legacy'''),dict(org=current.organization_id,scope=scope,legacy=identifier)).mappings().first()
                if existing:
                    authorized_measurement(connection,current,existing['measurement_id'])
                    if existing['raw_sha256']!=checksum or existing['metadata_sha256']!=metadata_hash:
                        raise MeasurementError('LEGACY_IMPORT_CONFLICT','Original or metadata differs from prior import',status=409)
                    outcomes.append({'legacy_id':identifier,'status':'already_imported','measurement_id':str(existing['measurement_id'])})
                    continue
                validate_associations(connection,current,metadata)
                if not apply:
                    outcomes.append({'legacy_id':identifier,'status':'eligible','sha256':checksum})
                    continue
                staged=FileStore().new_staging()
                with staged.open('xb') as destination:
                    destination.write(raw);destination.flush();os.fsync(destination.fileno())
                measurement_id=persist_measurement(connection,staged,record.get('original_filename','legacy.csv'),metadata,
                    summary,checksum,len(raw),current,uuid4(),parameter_sources=sources)
                connection.execute(text('''INSERT INTO legacy_imports
                    (id,organization_id,actor_id,batch_id,source_scope,legacy_id,raw_sha256,metadata_sha256,measurement_id)
                    VALUES (:id,:org,:actor,:batch,:scope,:legacy,:raw,:metadata,:measurement)'''),
                    dict(id=uuid4(),org=current.organization_id,actor=current.user_id,batch=batch_id,scope=scope,legacy=identifier,
                         raw=checksum,metadata=metadata_hash,measurement=measurement_id))
                audit(connection,current.organization_id,current.user_id,'legacy.import','measurement',measurement_id)
            outcomes.append({'legacy_id':identifier,'status':'imported_unconfirmed','measurement_id':str(measurement_id)})
        except (MeasurementError,ValueError,OSError) as error:
            outcomes.append({'legacy_id':identifier,'status':'rejected','code':error.code if isinstance(error,MeasurementError) else 'LEGACY_INVALID'})
        finally:
            if staged:
                staged.unlink(missing_ok=True)
    return {'mode':'apply' if apply else 'dry_run','batch_id':batch_id,'source_scope':scope,'records':outcomes,
            'rejected_count':sum(row['status']=='rejected' for row in outcomes),'old_results_accepted':False,'source_files_deleted':0}
