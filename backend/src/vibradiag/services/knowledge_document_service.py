"""Authorized immutable text publication; vector readiness is distinct from publication."""
from hashlib import sha256
import json
from uuid import uuid4
from sqlalchemy import text
from vibradiag.domain.knowledge_text import parse_knowledge,POLICY
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.analysis_store import json_bytes
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services.identity_service import asset_allowed,audit,password_hasher
from vibradiag.services.measurement_service import fresh,not_found


def digest(value):return sha256(json_bytes(value)).hexdigest()
def conflict(code='KNOWLEDGE_VERSION_CONFLICT'):raise MeasurementError(code,'Knowledge state changed; reload the current version',status=409)
def denied():raise MeasurementError('KNOWLEDGE_PERMISSION_REQUIRED','Explicit knowledge permission is required',status=403)


def business(connection,principal,*,engineer=False):
    current=fresh(connection,principal)
    if engineer and 'engineer' not in current.roles:denied()
    if not current.roles.intersection({'engineer','inspector','supervisor'}):denied()
    return current


def reviewer(connection,current,*,lock=False):
    if 'engineer' not in current.roles:return False
    return bool(connection.execute(text('SELECT 1 FROM knowledge_review_grants WHERE user_id=:user AND organization_id=:org AND enabled'
        +(' FOR SHARE' if lock else '')),dict(user=current.user_id,org=current.organization_id)).first())


def _scope(connection,current,row):
    if row['asset_id'] and not asset_allowed(connection,current,row['asset_id']):not_found()


def _document(connection,current,identifier,*,lock=False):
    row=connection.execute(text('SELECT * FROM knowledge_documents WHERE id=:id AND organization_id=:org'+(' FOR UPDATE' if lock else '')),
        dict(id=identifier,org=current.organization_id)).mappings().first()
    if not row:not_found()
    _scope(connection,current,row);return row


def _revision(connection,current,identifier,*,lock=False,author=False,acknowledge=False):
    candidate=connection.execute(text('SELECT document_id FROM knowledge_revisions WHERE id=:id AND organization_id=:org'),dict(id=identifier,org=current.organization_id)).mappings().first()
    if not candidate:not_found()
    document=_document(connection,current,candidate['document_id'],lock=lock)
    row=connection.execute(text('SELECT * FROM knowledge_revisions WHERE id=:id AND organization_id=:org'+(' FOR UPDATE' if lock else '')),
        dict(id=identifier,org=current.organization_id)).mappings().one()
    owns=document['owner_id']==current.user_id and 'engineer' in current.roles
    if author and not owns:not_found()
    if not (owns or row['published_at'] is not None or (row['status']=='pending_review' or acknowledge) and reviewer(connection,current)):not_found()
    _verify(row)
    return row,document


def _verify(row):
    raw=bytes(row['original_bytes'])
    try:normalized=raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
    except UnicodeDecodeError:raise MeasurementError('KNOWLEDGE_INTEGRITY','Knowledge source encoding is unavailable',status=409) from None
    if (sha256(raw).hexdigest()!=row['source_sha256'] or normalized!=row['text_content']
            or sha256(normalized.encode()).hexdigest()!=row['text_sha256'] or digest(row['metadata'])!=row['metadata_sha256']):
        raise MeasurementError('KNOWLEDGE_INTEGRITY','Knowledge content does not match its immutable source',status=409)


def _event(connection,current,action,reason,document=None,revision=None,details=None):
    connection.execute(text('''INSERT INTO knowledge_events(id,organization_id,actor_id,document_id,revision_id,action,reason,details)
        VALUES (:id,:org,:actor,:doc,:revision,:action,:reason,CAST(:details AS jsonb))'''),
        dict(id=uuid4(),org=current.organization_id,actor=current.user_id,doc=document,revision=revision,action=action,reason=reason,details=json_bytes(details or {}).decode()))
    audit(connection,current.organization_id,current.user_id,'knowledge.'+action,'knowledge_revision' if revision else 'knowledge_document',revision or document)


def _command(connection,current,key,operation,payload):
    if not key or len(key)>128 or any(ord(c)<33 or ord(c)>126 for c in key):
        raise MeasurementError('IDEMPOTENCY_KEY_INVALID','Visible ASCII idempotency key required',status=422)
    hashed=sha256(key.encode()).hexdigest();request=digest(dict(operation=operation,payload=payload))
    lock=int.from_bytes(sha256(f'knowledge/{current.organization_id}/{current.user_id}/{hashed}'.encode()).digest()[:8],'big',signed=True)
    connection.execute(text('SELECT pg_advisory_xact_lock(:lock)'),dict(lock=lock))
    existing=connection.execute(text('SELECT * FROM knowledge_commands WHERE organization_id=:org AND actor_id=:actor AND key_hash=:key'),
        dict(org=current.organization_id,actor=current.user_id,key=hashed)).mappings().first()
    if existing and existing['request_hash']!=request:conflict('IDEMPOTENCY_CONFLICT')
    return existing,hashed,request


def _save(connection,current,operation,key,request,document=None,revision=None,reviewer_id=None):
    connection.execute(text('''INSERT INTO knowledge_commands(id,organization_id,actor_id,operation,key_hash,request_hash,document_id,revision_id,reviewer_id)
        VALUES (:id,:org,:actor,:op,:key,:request,:doc,:rev,:reviewer)'''),
        dict(id=uuid4(),org=current.organization_id,actor=current.user_id,op=operation,key=key,request=request,doc=document,rev=revision,reviewer=reviewer_id))


def set_review_grant(payload,key,principal):
    with transaction() as connection:
        current=fresh(connection,principal)
        if 'admin' not in current.roles:denied()
        hashed_password=connection.execute(text('SELECT password_hash FROM users WHERE id=:user AND organization_id=:org'),dict(user=current.user_id,org=current.organization_id)).scalar_one()
        try:password_hasher.verify(hashed_password,payload.password.get_secret_value())
        except Exception:raise MeasurementError('KNOWLEDGE_REAUTHENTICATION_REQUIRED','Administrator password verification required',status=403) from None
        current=fresh(connection,principal)
        if 'admin' not in current.roles:denied()
        existing,hashed,request=_command(connection,current,key,'review_grant',payload.model_dump(mode='json',exclude={'password'}))
        target=connection.execute(text('SELECT * FROM users WHERE id=:user AND organization_id=:org FOR UPDATE'),dict(user=payload.user_id,org=current.organization_id)).mappings().first()
        if not target:not_found()
        grant=connection.execute(text('SELECT * FROM knowledge_review_grants WHERE user_id=:user'),dict(user=payload.user_id)).mappings().first()
        if not existing:
            if (grant['row_version'] if grant else 0)!=payload.expected_version:conflict()
            if payload.enabled and (target['status']!='active' or not connection.execute(text("SELECT 1 FROM role_assignments WHERE user_id=:user AND organization_id=:org AND role='engineer'"),dict(user=payload.user_id,org=current.organization_id)).first()):
                conflict('KNOWLEDGE_REVIEWER_NOT_ENGINEER')
            connection.execute(text('''INSERT INTO knowledge_review_grants(user_id,organization_id,enabled,row_version,changed_by,reason)
                VALUES (:user,:org,:enabled,1,:actor,:reason) ON CONFLICT(user_id) DO UPDATE SET enabled=EXCLUDED.enabled,
                row_version=knowledge_review_grants.row_version+1,changed_by=EXCLUDED.changed_by,changed_at=now(),reason=EXCLUDED.reason'''),
                dict(user=payload.user_id,org=current.organization_id,enabled=payload.enabled,actor=current.user_id,reason=payload.reason))
            _event(connection,current,'review_grant',payload.reason,details=dict(user_id=str(payload.user_id),enabled=payload.enabled))
            _save(connection,current,'review_grant',hashed,request,reviewer_id=payload.user_id)
        row=connection.execute(text('SELECT user_id,enabled,row_version,changed_at FROM knowledge_review_grants WHERE user_id=:user'),dict(user=payload.user_id)).mappings().one()
        return dict(row),200 if existing else 201


def import_document(raw,filename,metadata,key,principal,document_id=None):
    parsed=parse_knowledge(raw,filename);data=metadata.model_dump(mode='json');operation='revise' if document_id else 'create'
    if (document_id is not None)!=(metadata.expected_document_version is not None):
        raise MeasurementError('KNOWLEDGE_VERSION_REQUIRED','Only a revision upload requires expected_document_version',status=422)
    with transaction() as connection:
        current=business(connection,principal,engineer=True)
        existing,hashed,request=_command(connection,current,key,operation,dict(document_id=str(document_id) if document_id else None,metadata=data,filename=parsed.filename,source_sha256=parsed.source_sha256))
        if existing:
            revision_id=existing['revision_id'];_revision(connection,current,revision_id,author=True)
        else:
            if document_id:
                document=_document(connection,current,document_id,lock=True)
                if document['owner_id']!=current.user_id:not_found()
                if document['row_version']!=metadata.expected_document_version:conflict()
                if document['visibility']!=metadata.visibility or document['asset_id']!=metadata.asset_id:conflict('KNOWLEDGE_SCOPE_IMMUTABLE')
                old,_=_revision(connection,current,document['current_revision_id'],author=True)
                number=old['revision_no']+1
            else:
                if metadata.asset_id and not asset_allowed(connection,current,metadata.asset_id):not_found()
                duplicate=connection.execute(text('''SELECT 1 FROM knowledge_revisions r JOIN knowledge_documents d ON d.id=r.document_id
                    WHERE d.organization_id=:org AND d.owner_id=:actor AND d.visibility=:visibility
                    AND d.asset_id IS NOT DISTINCT FROM CAST(:asset AS uuid) AND r.text_sha256=:sha LIMIT 1'''),
                    dict(org=current.organization_id,actor=current.user_id,visibility=metadata.visibility,asset=metadata.asset_id,sha=parsed.text_sha256)).first()
                if duplicate:conflict('KNOWLEDGE_DUPLICATE_CONTENT')
                document_id=uuid4();number=1
                connection.execute(text('INSERT INTO knowledge_documents(id,organization_id,owner_id,visibility,asset_id) VALUES (:id,:org,:owner,:visibility,:asset)'),
                    dict(id=document_id,org=current.organization_id,owner=current.user_id,visibility=metadata.visibility,asset=metadata.asset_id))
            snapshot={k:v for k,v in data.items() if k not in {'reason','expected_document_version'}}
            if (number>1 and old['source_sha256']==parsed.source_sha256 and old['metadata']==snapshot
                    and old['format']==parsed.format and old['original_filename']==parsed.filename):conflict('KNOWLEDGE_NO_CHANGE')
            revision_id=uuid4()
            connection.execute(text('''INSERT INTO knowledge_revisions(id,organization_id,document_id,revision_no,created_by,original_filename,format,
                original_bytes,source_sha256,text_content,text_sha256,metadata,metadata_sha256,chunk_policy,chunk_count,reason)
                VALUES (:id,:org,:doc,:number,:actor,:filename,:format,:raw,:source_sha,:body,:text_sha,CAST(:metadata AS jsonb),:metadata_sha,:policy,:count,:reason)'''),
                dict(id=revision_id,org=current.organization_id,doc=document_id,number=number,actor=current.user_id,filename=parsed.filename,format=parsed.format,
                    raw=parsed.raw,source_sha=parsed.source_sha256,body=parsed.text,text_sha=parsed.text_sha256,metadata=json_bytes(snapshot).decode(),metadata_sha=digest(snapshot),policy=POLICY,count=len(parsed.chunks),reason=metadata.reason))
            for index,chunk in enumerate(parsed.chunks,1):
                connection.execute(text('''INSERT INTO knowledge_chunks(id,organization_id,revision_id,chunk_no,text_content,text_sha256,heading_path,line_start,line_end,char_start,char_end)
                    VALUES (:id,:org,:rev,:number,:body,:sha,CAST(:headings AS jsonb),:start_line,:end_line,:start,:end)'''),
                    dict(id=uuid4(),org=current.organization_id,rev=revision_id,number=index,body=chunk.text,sha=sha256(chunk.text.encode()).hexdigest(),headings=json.dumps(chunk.heading_path),
                        start_line=chunk.line_start,end_line=chunk.line_end,start=chunk.char_start,end=chunk.char_end))
            if number>1 and old['status']=='pending_review':
                connection.execute(text("UPDATE knowledge_revisions SET status='draft',row_version=row_version+1 WHERE id=:id"),dict(id=old['id']))
                _event(connection,current,'superseded_submission',metadata.reason,document_id,old['id'])
            connection.execute(text('UPDATE knowledge_documents SET current_revision_id=:rev,row_version=row_version+1,updated_at=now() WHERE id=:id'),dict(rev=revision_id,id=document_id))
            _event(connection,current,operation,metadata.reason,document_id,revision_id)
            _save(connection,current,operation,hashed,request,document_id,revision_id)
    return read_revision(revision_id,principal),200 if existing else 201


def transition(identifier,action,payload,key,principal):
    if action not in {'submit','reject','publish','withdraw'}:raise ValueError('Unsupported knowledge action')
    with transaction() as connection:
        current=business(connection,principal,engineer=True)
        existing,hashed,request=_command(connection,current,key,action,dict(id=str(identifier),**payload.model_dump(mode='json')))
        if action!='submit' and not reviewer(connection,current,lock=True):denied()
        row,document=_revision(connection,current,identifier,lock=True,author=action=='submit',acknowledge=bool(existing and action=='reject'))
        if not existing:
            if row['row_version']!=payload.expected_version or document['row_version']!=payload.expected_document_version:conflict()
            expected={'submit':'draft','reject':'pending_review','publish':'pending_review','withdraw':'published'}[action]
            if row['status']!=expected:conflict('KNOWLEDGE_TRANSITION_INVALID')
            if action!='withdraw' and row['id']!=document['current_revision_id']:conflict('KNOWLEDGE_STALE_REVISION')
            if connection.execute(text('SELECT count(*) FROM knowledge_chunks WHERE revision_id=:id'),dict(id=identifier)).scalar_one()!=row['chunk_count']:conflict('KNOWLEDGE_INTEGRITY')
            for chunk in connection.execute(text('SELECT * FROM knowledge_chunks WHERE revision_id=:id ORDER BY chunk_no'),dict(id=identifier)).mappings():_verify_chunk(row,chunk)
            current=business(connection,principal,engineer=True)
            _scope(connection,current,document)
            if action!='submit' and not reviewer(connection,current,lock=True):denied()
            status={'submit':'pending_review','reject':'draft','publish':'published','withdraw':'withdrawn'}[action]
            if action=='publish':
                old=document['published_revision_id']
                if old:
                    connection.execute(text("UPDATE knowledge_revisions SET status='withdrawn',row_version=row_version+1,withdrawn_at=now(),withdrawn_by=:actor WHERE id=:id"),dict(actor=current.user_id,id=old))
                    _event(connection,current,'superseded_publication',payload.reason,document['id'],old)
                connection.execute(text("UPDATE knowledge_revisions SET status='published',row_version=row_version+1,published_at=now(),published_by=:actor WHERE id=:id"),dict(id=identifier,actor=current.user_id))
                published=identifier
            elif action=='withdraw':
                connection.execute(text("UPDATE knowledge_revisions SET status='withdrawn',row_version=row_version+1,withdrawn_at=now(),withdrawn_by=:actor WHERE id=:id"),dict(id=identifier,actor=current.user_id))
                published=None if document['published_revision_id']==identifier else document['published_revision_id']
            else:
                connection.execute(text('UPDATE knowledge_revisions SET status=:status,row_version=row_version+1 WHERE id=:id'),dict(status=status,id=identifier))
                published=document['published_revision_id']
            connection.execute(text('UPDATE knowledge_documents SET published_revision_id=:published,row_version=row_version+1,updated_at=now() WHERE id=:id'),dict(published=published,id=document['id']))
            _event(connection,current,action,payload.reason,document['id'],identifier,dict(self_review=current.user_id==row['created_by']) if action=='publish' else None)
            _save(connection,current,action,hashed,request,document['id'],identifier)
    return read_revision(identifier,principal,acknowledge=action=='reject'),200


def _verify_chunk(revision,chunk):
    expected=revision['text_content'][chunk['char_start']:chunk['char_end']]
    if expected!=chunk['text_content'] or sha256(expected.encode()).hexdigest()!=chunk['text_sha256']:
        conflict('KNOWLEDGE_INTEGRITY')
    if revision['text_content'][:chunk['char_start']].count('\n')+1!=chunk['line_start'] or revision['text_content'][:chunk['char_end']-1].count('\n')+1!=chunk['line_end']:
        conflict('KNOWLEDGE_INTEGRITY')


def read_revision(identifier,principal,*,acknowledge=False):
    with transaction() as connection:
        current=business(connection,principal);row,document=_revision(connection,current,identifier,acknowledge=acknowledge)
        if acknowledge:return dict(id=str(row['id']),document_id=str(row['document_id']),status=row['status'],row_version=row['row_version'],document_version=document['row_version'],formal_retrieval_available=False)
        can_manage=document['owner_id']==current.user_id and 'engineer' in current.roles or reviewer(connection,current)
        return dict(id=str(row['id']),document_id=str(row['document_id']),revision_no=row['revision_no'],status=row['status'],row_version=row['row_version'],
            document_version=document['row_version'] if can_manage else None,is_current=row['id']==document['current_revision_id'],
            is_active_publication=row['status']=='published' and row['id']==document['published_revision_id'],metadata=row['metadata'],
            original_filename=row['original_filename'],format=row['format'],source_sha256=row['source_sha256'],text_sha256=row['text_sha256'],
            chunk_policy=row['chunk_policy'],chunk_count=row['chunk_count'],published_at=row['published_at'],withdrawn_at=row['withdrawn_at'],
            created_by=str(row['created_by']),published_by=str(row['published_by']) if row['published_by'] else None,withdrawn_by=str(row['withdrawn_by']) if row['withdrawn_by'] else None,
            **_index_state(connection,row))


def _index_state(connection,row):
    from vibradiag.infrastructure.retrieval.embedding import configured_identity
    head=connection.execute(text('''SELECT h.run_id,h.row_version,i.identity_sha256,i.status,i.runtime_version FROM knowledge_active_indexes h
        JOIN knowledge_index_runs i ON i.id=h.run_id WHERE h.revision_id=:id'''),dict(id=row['id'])).mappings().first()
    latest=connection.execute(text('SELECT status FROM knowledge_index_runs WHERE revision_id=:id ORDER BY created_at DESC,id LIMIT 1'),dict(id=row['id'])).scalar_one_or_none()
    try:identity=configured_identity().fingerprint if head else None
    except (OSError,ValueError,TypeError):identity=None  # Immutable source history remains readable without a valid model configuration.
    compatible=bool(head and head['status']=='ready' and head['identity_sha256']==identity)
    return dict(vector_index='ready' if compatible else 'identity_mismatch' if head else latest or 'not_indexed',latest_index_status=latest,
        active_index_id=str(head['run_id']) if head else None,index_version=head['row_version'] if head else 0,
        index_runtime_version=head['runtime_version'] if head else None,model_availability_checked=False,
        formal_retrieval_available=compatible and row['status']=='published' and row['metadata']['content_origin']!='developer_test')


def read_document(identifier,principal):
    with transaction() as connection:
        current=business(connection,principal);document=_document(connection,current,identifier)
        latest=connection.execute(text('SELECT status FROM knowledge_revisions WHERE id=:id'),dict(id=document['current_revision_id'])).scalar_one()
        if document['owner_id']==current.user_id and 'engineer' in current.roles or latest=='pending_review' and reviewer(connection,current):selected=document['current_revision_id']
        else:selected=document['published_revision_id']
        if not selected:not_found()
    return read_revision(selected,principal)


def list_documents(principal,page,page_size):
    with transaction() as connection:
        current=business(connection,principal);reviews=reviewer(connection,current)
        rows=connection.execute(text('''SELECT d.id FROM knowledge_documents d JOIN knowledge_revisions r ON r.id=d.current_revision_id
            WHERE d.organization_id=:org AND (d.asset_id IS NULL OR EXISTS(SELECT 1 FROM asset_grants g JOIN assets a ON a.id=g.asset_id
                AND a.organization_id=g.organization_id WHERE g.user_id=:actor AND g.organization_id=:org AND g.asset_id=d.asset_id AND g.can_read AND a.status='active'))
            AND ((d.owner_id=:actor AND :engineer) OR (r.status='pending_review' AND :review) OR d.published_revision_id IS NOT NULL)
            ORDER BY d.created_at DESC,d.id LIMIT :limit OFFSET :offset'''),dict(org=current.organization_id,actor=current.user_id,engineer='engineer' in current.roles,
                review=reviews,limit=page_size,offset=(page-1)*page_size)).mappings().all()
    return dict(items=[read_document(r['id'],principal) for r in rows],page=page,page_size=page_size)


def chunks(identifier,principal,page,page_size):
    with transaction() as connection:
        current=business(connection,principal);row,_=_revision(connection,current,identifier)
        records=connection.execute(text('SELECT * FROM knowledge_chunks WHERE revision_id=:id ORDER BY chunk_no LIMIT :limit OFFSET :offset'),dict(id=identifier,limit=page_size,offset=(page-1)*page_size)).mappings().all()
        state=_index_state(connection,row)
        return dict(items=[_chunk_response(row,c,index=state) for c in records],page=page,page_size=page_size)


def _chunk_response(row,chunk,*,index=None):
    _verify_chunk(row,chunk)
    return dict(id=str(chunk['id']),revision_id=str(row['id']),document_id=str(row['document_id']),chunk_no=chunk['chunk_no'],text=chunk['text_content'],
        text_sha256=chunk['text_sha256'],source_sha256=row['source_sha256'],heading_path=chunk['heading_path'],line_start=chunk['line_start'],line_end=chunk['line_end'],
        char_start=chunk['char_start'],char_end=chunk['char_end'],status=row['status'],content_origin=row['metadata']['content_origin'],**(index or dict(formal_retrieval_available=False)))


def read_chunk(identifier,principal):
    with transaction() as connection:
        current=business(connection,principal)
        chunk=connection.execute(text('SELECT * FROM knowledge_chunks WHERE id=:id AND organization_id=:org'),dict(id=identifier,org=current.organization_id)).mappings().first()
        if not chunk:not_found()
        row,_=_revision(connection,current,chunk['revision_id']);return _chunk_response(row,chunk,index=_index_state(connection,row))


def original(identifier,principal):
    with transaction() as connection:
        current=business(connection,principal);row,_=_revision(connection,current,identifier)
        audit(connection,current.organization_id,current.user_id,'knowledge.download','knowledge_revision',identifier)
        return bytes(row['original_bytes']),row['format']


def published_candidates(principal,*,asset_id=None,equipment='motor',component='bearing',include_developer=False,limit=5):
    """Publication/ACL prefilter for T03b, not vector ranking; no public search endpoint."""
    with transaction() as connection:
        current=business(connection,principal)
        if asset_id and not asset_allowed(connection,current,asset_id):not_found()
        records=connection.execute(text('''SELECT c.id FROM knowledge_chunks c JOIN knowledge_revisions r ON r.id=c.revision_id
            JOIN knowledge_documents d ON d.id=r.document_id AND d.published_revision_id=r.id
            WHERE d.organization_id=:org AND r.status='published'
              AND (d.asset_id IS NULL OR (d.asset_id=CAST(:asset AS uuid) AND EXISTS(SELECT 1 FROM asset_grants g JOIN assets a ON a.id=g.asset_id
                AND a.organization_id=g.organization_id WHERE g.user_id=:actor AND g.organization_id=:org AND g.asset_id=d.asset_id AND g.can_read AND a.status='active')))
              AND ((r.metadata->'equipment_scope') ? :equipment OR (r.metadata->'equipment_scope') ? 'rotating_generic')
              AND r.metadata->>'component_scope' IN (:component,'general')
              AND (:developer OR r.metadata->>'content_origin'<>'developer_test')
            ORDER BY d.id,c.chunk_no LIMIT :limit'''),dict(org=current.organization_id,actor=current.user_id,asset=asset_id,equipment=equipment,component=component,developer=include_developer,limit=min(max(limit,1),5))).scalars().all()
    items=[read_chunk(identifier,principal) for identifier in records]
    return [dict(item,formal_retrieval_available=False) for item in items if item['status']=='published']
