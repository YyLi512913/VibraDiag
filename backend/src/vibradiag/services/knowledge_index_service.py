"""Synchronous bounded immutable generations and ACL-first exact dense retrieval."""
from dataclasses import asdict
from hashlib import sha256
import json
from threading import BoundedSemaphore
from time import monotonic
from uuid import uuid4

import numpy as np
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from vibradiag.core.config import setting
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.retrieval.embedding import LocalEmbedding,configured_identity,validate_vectors
from vibradiag.services import knowledge_document_service as documents
from vibradiag.services.identity_service import asset_allowed,audit

_BUILDS=BoundedSemaphore(1)
BUILD_SECONDS=120
MAX_CHUNKS=128
MAX_ELIGIBLE=10000


def provider(identity,timeout=60):
    return LocalEmbedding(identity,base_url=setting('VIBRADIAG_OLLAMA_URL','http://127.0.0.1:11434'),timeout=min(timeout,60))


def _key(key):
    if not key or len(key)>128 or any(ord(c)<33 or ord(c)>126 for c in key):
        raise MeasurementError('IDEMPOTENCY_KEY_INVALID','Visible ASCII idempotency key required',status=422)
    return sha256(key.encode()).hexdigest()


def _manage(c,principal,revision_id,*,lock=False):
    current=documents.business(c,principal,engineer=True)
    if not documents.reviewer(c,current,lock=lock):documents.denied()
    revision,document=documents._revision(c,current,revision_id,lock=lock)
    if revision['status']!='published' or document['published_revision_id']!=revision['id']:
        documents.conflict('KNOWLEDGE_INDEX_REQUIRES_PUBLICATION')
    return current,revision,document


def _head(c,revision_id):
    return c.execute(text('SELECT * FROM knowledge_active_indexes WHERE revision_id=:id'),dict(id=revision_id)).mappings().first()


def _snapshot(c,revision):
    chunks=c.execute(text('SELECT * FROM knowledge_chunks WHERE revision_id=:id ORDER BY chunk_no'),dict(id=revision['id'])).mappings().all()
    if len(chunks)!=revision['chunk_count']:documents.conflict('KNOWLEDGE_INTEGRITY')
    for chunk in chunks:documents._verify_chunk(revision,chunk)
    manifest=documents.digest([dict(id=str(row['id']),text_sha256=row['text_sha256'],chunk_no=row['chunk_no']) for row in chunks])
    return chunks,manifest


def _replay(c,current,key,request):
    row=c.execute(text('SELECT * FROM knowledge_index_runs WHERE organization_id=:org AND created_by=:actor AND key_hash=:key'),
        dict(org=current.organization_id,actor=current.user_id,key=key)).mappings().first()
    if row and row['request_hash']!=request:documents.conflict('IDEMPOTENCY_CONFLICT')
    return row


def _summary(row,head):
    return dict(id=str(row['id']),revision_id=str(row['revision_id']),status=row['status'],row_version=row['row_version'],
        active_index_id=str(head['run_id']) if head else None,index_version=head['row_version'] if head else 0,
        is_active=bool(head and head['run_id']==row['id']),identity_sha256=row['identity_sha256'],identity=row['identity'],
        runtime_version=row['runtime_version'],chunk_count=row['chunk_count'],vector_count=row['vector_count'],
        source_sha256=row['source_sha256'],chunk_manifest_sha256=row['chunk_manifest_sha256'],vector_sha256=row['vector_sha256'],
        created_at=row['created_at'],expires_at=row['expires_at'],completed_at=row['completed_at'],error_code=row['error_code'])


def read_run(identifier,principal):
    with transaction() as c:
        current=documents.business(c,principal)
        row=c.execute(text('SELECT * FROM knowledge_index_runs WHERE id=:id AND organization_id=:org'),dict(id=identifier,org=current.organization_id)).mappings().first()
        if not row:documents.not_found()
        documents._revision(c,current,row['revision_id'])
        return _summary(row,_head(c,row['revision_id']))


def revision_indexes(identifier,principal):
    with transaction() as c:
        current=documents.business(c,principal);documents._revision(c,current,identifier)
        rows=c.execute(text('SELECT * FROM knowledge_index_runs WHERE revision_id=:id AND organization_id=:org ORDER BY created_at DESC,id LIMIT 20'),dict(id=identifier,org=current.organization_id)).mappings().all()
        head=_head(c,identifier)
        return dict(active_index_id=str(head['run_id']) if head else None,index_version=head['row_version'] if head else 0,items=[_summary(row,head) for row in rows])


def _failed(identifier,principal,code):
    with transaction() as c:
        row=c.execute(text("UPDATE knowledge_index_runs SET status='failed',row_version=row_version+1,completed_at=clock_timestamp(),error_code=:code WHERE id=:id AND status='processing' RETURNING revision_id"),dict(id=identifier,code=code)).first()
        if row:documents._event(c,principal,'index_failed','Index generation failed; prior active generation retained',revision=row.revision_id,details=dict(run_id=str(identifier),error_code=code))


def build(identifier,payload,key,principal):
    identity=configured_identity();key_hash=_key(key)
    request=documents.digest(dict(revision_id=str(identifier),identity=identity.fingerprint,**payload.model_dump(mode='json')))
    with transaction() as c:
        current,_,_=_manage(c,principal,identifier)
        previous=_replay(c,current,key_hash,request)
        if previous:return read_run(previous['id'],principal),200
    if not _BUILDS.acquire(blocking=False):
        raise MeasurementError('KNOWLEDGE_INDEX_BUSY','One local index build is already running',status=429)
    run_id=None
    try:
        with transaction() as c:
            current,revision,document=_manage(c,principal,identifier,lock=True)
            previous=_replay(c,current,key_hash,request)
            if previous:return read_run(previous['id'],principal),200
            head=_head(c,identifier)
            if (revision['row_version']!=payload.expected_version or document['row_version']!=payload.expected_document_version
                    or (head['row_version'] if head else 0)!=payload.expected_index_version):documents.conflict()
            expired=c.execute(text("UPDATE knowledge_index_runs SET status='failed',row_version=row_version+1,completed_at=clock_timestamp(),error_code='KNOWLEDGE_INDEX_INTERRUPTED' WHERE revision_id=:id AND status='processing' AND expires_at<=clock_timestamp() RETURNING id"),dict(id=identifier)).scalars().all()
            for old in expired:documents._event(c,current,'index_interrupted',payload.reason,document['id'],identifier,dict(run_id=str(old)))
            if c.execute(text("SELECT 1 FROM knowledge_index_runs WHERE revision_id=:id AND status='processing'"),dict(id=identifier)).first():documents.conflict('KNOWLEDGE_INDEX_PROCESSING')
            if revision['chunk_count']>MAX_CHUNKS:
                raise MeasurementError('KNOWLEDGE_INDEX_CHUNK_LIMIT','This index stage supports at most 128 chunks per revision',status=422)
            chunks,manifest=_snapshot(c,revision);run_id=uuid4()
            c.execute(text('''INSERT INTO knowledge_index_runs(id,organization_id,revision_id,created_by,key_hash,request_hash,identity,identity_sha256,
                source_sha256,text_sha256,chunk_manifest_sha256,chunk_policy,chunk_count,expected_document_version,expected_revision_version,expected_index_version,reason)
                VALUES (:id,:org,:revision,:actor,:key,:request,CAST(:identity AS jsonb),:sha,:source,:text,:manifest,:policy,:count,:docversion,:revversion,:indexversion,:reason)'''),
                dict(id=run_id,org=current.organization_id,revision=identifier,actor=current.user_id,key=key_hash,request=request,
                    identity=json.dumps(asdict(identity)),sha=identity.fingerprint,source=revision['source_sha256'],text=revision['text_sha256'],manifest=manifest,
                    policy=revision['chunk_policy'],count=len(chunks),docversion=payload.expected_document_version,revversion=payload.expected_version,indexversion=payload.expected_index_version,reason=payload.reason))
            documents._event(c,current,'index_started',payload.reason,document['id'],identifier,dict(run_id=str(run_id),identity_sha256=identity.fingerprint))
        deadline=monotonic()+BUILD_SECONDS
        def local():
            remaining=deadline-monotonic()
            if remaining<=0:raise MeasurementError('KNOWLEDGE_INDEX_DEADLINE','Index build deadline exceeded',status=409)
            return provider(identity,min(60,remaining))
        try:
            runtime=local().inspect()['ollama_version'];batches=[]
            for start in range(0,len(chunks),8):
                # Recheck live access/publication before each outbound batch; no connection held during inference.
                with transaction() as c:_manage(c,principal,identifier)
                batch=chunks[start:start+8]
                vectors,_=local().encode([row['text_content'] for row in batch])
                batches.append(validate_vectors(vectors.tolist(),len(batch)))
            vectors=np.concatenate(batches)
            if local().inspect()['ollama_version']!=runtime:documents.conflict('KNOWLEDGE_EMBEDDING_RUNTIME_CHANGED')
            if configured_identity()!=identity:documents.conflict('KNOWLEDGE_EMBEDDING_IDENTITY_CHANGED')
            with transaction() as c:
                current,live,document=_manage(c,principal,identifier,lock=True)
                row=c.execute(text('SELECT * FROM knowledge_index_runs WHERE id=:id FOR UPDATE'),dict(id=run_id)).mappings().one()
                head=_head(c,identifier)
                if (row['status']!='processing' or live['row_version']!=payload.expected_version
                        or document['row_version']!=payload.expected_document_version or (head['row_version'] if head else 0)!=payload.expected_index_version):documents.conflict()
                _,now_manifest=_snapshot(c,live)
                if now_manifest!=manifest:documents.conflict('KNOWLEDGE_INTEGRITY')
                if monotonic()>=deadline:raise MeasurementError('KNOWLEDGE_INDEX_DEADLINE','Index build deadline exceeded',status=409)
                for chunk,vector in zip(chunks,vectors,strict=True):
                    c.execute(text('''INSERT INTO knowledge_chunk_vectors(run_id,organization_id,revision_id,chunk_id,text_sha256,input_sha256,embedding)
                        VALUES (:run,:org,:revision,:chunk,:sha,:sha,CAST(:vector AS vibradiag_extensions.vector))'''),
                        dict(run=run_id,org=current.organization_id,revision=identifier,chunk=chunk['id'],sha=chunk['text_sha256'],vector=json.dumps(vector.tolist(),allow_nan=False)))
                c.execute(text("UPDATE knowledge_index_runs SET status='ready',row_version=row_version+1,completed_at=clock_timestamp(),runtime_version=:runtime,vector_count=chunk_count,vector_sha256=:sha WHERE id=:id"),
                    dict(id=run_id,runtime=runtime,sha=sha256(vectors.astype('<f4').tobytes()).hexdigest()))
                c.execute(text('''INSERT INTO knowledge_active_indexes(revision_id,organization_id,run_id,row_version,changed_by)
                    VALUES (:revision,:org,:run,1,:actor) ON CONFLICT(revision_id) DO UPDATE SET run_id=EXCLUDED.run_id,
                    row_version=knowledge_active_indexes.row_version+1,changed_by=EXCLUDED.changed_by,changed_at=clock_timestamp()'''),
                    dict(revision=identifier,org=current.organization_id,run=run_id,actor=current.user_id))
                documents._event(c,current,'index_ready',payload.reason,document['id'],identifier,dict(run_id=str(run_id),vector_count=len(chunks)))
        except Exception as error:
            _failed(run_id,principal,error.code if isinstance(error,MeasurementError) else 'KNOWLEDGE_INDEX_FAILED')
        return read_run(run_id,principal),201
    finally:_BUILDS.release()


def _search_access(c,principal,payload):
    c.exec_driver_sql("SET LOCAL statement_timeout='5s'")
    c.exec_driver_sql("SET LOCAL lock_timeout='2s'")
    current=documents.business(c,principal)
    if payload.include_developer and not documents.reviewer(c,current):documents.denied()
    if payload.asset_id:
        if not asset_allowed(c,current,payload.asset_id):documents.not_found()
        equipment=c.execute(text('SELECT equipment_type FROM assets WHERE id=:id'),dict(id=payload.asset_id)).scalar_one()
        if payload.equipment not in {equipment,'rotating_generic'}:
            raise MeasurementError('KNOWLEDGE_EQUIPMENT_MISMATCH','Selected equipment does not match the authorized asset',status=422)
    return current


ELIGIBLE='''SELECT c.id,c.text_sha256,v.embedding,i.id AS index_id,r.id AS revision_id,d.id AS document_id
 FROM knowledge_chunk_vectors v JOIN knowledge_chunks c ON c.id=v.chunk_id AND c.revision_id=v.revision_id
 JOIN knowledge_revisions r ON r.id=c.revision_id JOIN knowledge_documents d ON d.id=r.document_id
 JOIN knowledge_active_indexes h ON h.revision_id=r.id AND h.run_id=v.run_id
 JOIN knowledge_index_runs i ON i.id=h.run_id AND i.organization_id=d.organization_id
 WHERE d.organization_id=:org AND r.status='published' AND d.published_revision_id=r.id
 AND i.status='ready' AND i.identity_sha256=:identity AND i.source_sha256=r.source_sha256
 AND i.text_sha256=r.text_sha256 AND i.chunk_count=r.chunk_count AND i.chunk_policy=r.chunk_policy
 AND c.text_sha256=v.text_sha256 AND (CAST(:runtime AS text) IS NULL OR i.runtime_version=:runtime)
 AND (d.asset_id IS NULL OR (d.asset_id=CAST(:asset AS uuid) AND EXISTS(SELECT 1 FROM asset_grants g JOIN assets a
 ON a.id=g.asset_id AND a.organization_id=g.organization_id WHERE g.user_id=:actor AND g.organization_id=:org
 AND g.asset_id=d.asset_id AND g.can_read AND a.status='active')))
 AND ((r.metadata->'equipment_scope') ? :equipment OR (r.metadata->'equipment_scope') ? 'rotating_generic')
 AND r.metadata->>'component_scope' IN (:component,'general')
 AND (:developer OR r.metadata->>'content_origin'<>'developer_test')'''


def _parameters(current,payload,identity,runtime=None):
    return dict(org=current.organization_id,actor=current.user_id,asset=payload.asset_id,equipment=payload.equipment,
        component=payload.component,developer=payload.include_developer,identity=identity.fingerprint,runtime=runtime)


def _resolve(c,current,row):
    chunk=c.execute(text('SELECT * FROM knowledge_chunks WHERE id=:id AND organization_id=:org'),dict(id=row['id'],org=current.organization_id)).mappings().one()
    revision,document=documents._revision(c,current,chunk['revision_id'])
    head=_head(c,revision['id'])
    if revision['status']!='published' or document['published_revision_id']!=revision['id'] or not head or head['run_id']!=row['index_id']:return None
    return dict(**documents._chunk_response(revision,chunk),metadata=revision['metadata'],index_id=str(row['index_id']),
        similarity=float(np.clip(1-row['distance'],-1,1)),relevance_qualified=False,diagnostic_probability=False)


def _search(payload,principal):
    started=monotonic();identity=configured_identity();timing={};runtime=None
    with transaction() as c:
        current=_search_access(c,principal,payload)
        eligible=c.execute(text('SELECT count(*) FROM ('+ELIGIBLE+') candidates'),_parameters(current,payload,identity)).scalar_one()
        if eligible>MAX_ELIGIBLE:raise MeasurementError('KNOWLEDGE_SEARCH_CAPACITY','Authorized corpus exceeds the current exact-search cap',status=422)
    ranked=[]
    if eligible:
        def local():
            remaining=60-(monotonic()-started)
            if remaining<=0:raise MeasurementError('KNOWLEDGE_SEARCH_DEADLINE','Search deadline exceeded',status=409)
            return provider(identity,min(remaining,60))
        runtime=local().inspect()['ollama_version'];vector,timing=local().encode([payload.query])
        vector=validate_vectors(vector.tolist(),1)
        if local().inspect()['ollama_version']!=runtime:documents.conflict('KNOWLEDGE_EMBEDDING_RUNTIME_CHANGED')
        if configured_identity()!=identity:documents.conflict('KNOWLEDGE_EMBEDDING_IDENTITY_CHANGED')
        with transaction() as c:
            current=_search_access(c,principal,payload);params=_parameters(current,payload,identity,runtime)
            count=c.execute(text('SELECT count(*) FROM ('+ELIGIBLE+') candidates'),params).scalar_one()
            if count>MAX_ELIGIBLE:raise MeasurementError('KNOWLEDGE_SEARCH_CAPACITY','Authorized corpus exceeds the current exact-search cap',status=422)
            eligible=count;params.update(vector=json.dumps(vector[0].tolist(),allow_nan=False),limit=payload.limit)
            # MATERIALIZED prevents top-k/ANN before authorization. Dedup only within authorized results.
            ranked=c.execute(text('''WITH eligible AS MATERIALIZED ('''+ELIGIBLE+'''), ranked AS MATERIALIZED (
              SELECT id,text_sha256,index_id,revision_id,document_id,
              embedding OPERATOR(vibradiag_extensions.<=>) CAST(:vector AS vibradiag_extensions.vector) AS distance FROM eligible),
              unique_content AS (SELECT DISTINCT ON(text_sha256) * FROM ranked ORDER BY text_sha256,distance,id)
              SELECT * FROM unique_content ORDER BY distance,id LIMIT :limit'''),params).mappings().all()
    with transaction() as c:
        current=_search_access(c,principal,payload);hits=[];matches=[]
        for row in ranked:
            # Requery eligible IDs after inference/ranking, so observed withdrawal or regrant takes effect.
            params=_parameters(current,payload,identity,runtime);params['chunk']=row['id'];params['run']=row['index_id']
            if not c.execute(text('SELECT 1 FROM ('+ELIGIBLE+') e WHERE e.id=:chunk AND e.index_id=:run'),params).first():continue
            hit=_resolve(c,current,row)
            if hit:
                hit['formal_retrieval_available']=not payload.include_developer
                hits.append(hit);matches.append(dict(chunk_id=hit['id'],revision_id=hit['revision_id'],index_id=hit['index_id'],text_sha256=hit['text_sha256'],similarity=hit['similarity']))
        identifier=uuid4();timing=dict(timing,wall_seconds=monotonic()-started)
        c.execute(text('''INSERT INTO knowledge_retrieval_runs(id,organization_id,actor_id,query,query_sha256,filters,identity_sha256,runtime_version,model_called,matches,eligible_count,timing)
            VALUES (:id,:org,:actor,:query,:sha,CAST(:filters AS jsonb),:identity,:runtime,:called,CAST(:matches AS jsonb),:count,CAST(:timing AS jsonb))'''),
            dict(id=identifier,org=current.organization_id,actor=current.user_id,query=payload.query,sha=sha256(payload.query.encode()).hexdigest(),
                filters=json.dumps(payload.model_dump(mode='json',exclude={'query'})),identity=identity.fingerprint,runtime=runtime,called=bool(runtime),
                matches=json.dumps(matches),count=eligible,timing=json.dumps(timing)))
        audit(c,current.organization_id,current.user_id,'knowledge.search','knowledge_retrieval',identifier)
        return dict(id=str(identifier),hits=hits,identity_sha256=identity.fingerprint,model_called=bool(runtime),timing=timing,
            retrieval_mode='developer_verification' if payload.include_developer else 'published_dense_exact',
            relevance_qualified=False,diagnostic_probability=False,agent_knowledge_integrated=False)


def search(payload,principal):
    try:return _search(payload,principal)
    except (MeasurementError,DBAPIError) as error:
        code=error.code if isinstance(error,MeasurementError) else 'KNOWLEDGE_SEARCH_DATABASE_UNAVAILABLE'
        try:
            with transaction() as c:
                current=documents.business(c,principal)
                documents._event(c,current,'search_failed','Knowledge retrieval did not complete',details=dict(query_sha256=sha256(payload.query.encode()).hexdigest(),error_code=code))
        except Exception:pass  # Failure of secondary audit cannot expose a raw DB/model exception.
        if isinstance(error,MeasurementError):raise
        raise MeasurementError(code,'Knowledge retrieval could not complete',status=503) from None


def read_retrieval(identifier,principal):
    with transaction() as c:
        current=documents.business(c,principal)
        row=c.execute(text('SELECT * FROM knowledge_retrieval_runs WHERE id=:id AND organization_id=:org AND actor_id=:actor'),dict(id=identifier,org=current.organization_id,actor=current.user_id)).mappings().first()
        if not row:documents.not_found()
        # Receipt is an immutable historical ledger, not a new retrieval or a source-body endpoint.
        return dict(id=str(row['id']),query=row['query'],filters=row['filters'],identity_sha256=row['identity_sha256'],
            matches=row['matches'],model_called=row['model_called'],runtime_version=row['runtime_version'],created_at=row['created_at'],
            historical_receipt_only=True,requires_current_source_authorization=True)
