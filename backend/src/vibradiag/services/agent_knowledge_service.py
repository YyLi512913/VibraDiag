"""Task-bound retrieval and current source resolution. No generated diagnosis or tool instructions."""
from uuid import UUID,uuid5,NAMESPACE_URL
from pydantic import ValidationError
from sqlalchemy import text
from vibradiag.agents.formal_contracts import MethodKnowledgeScope,ResolvedReference,TaskScope
from vibradiag.agents.formal_policy import PolicyStop,validate_draft_references
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.retrieval.embedding import configured_identity
from vibradiag.schemas.knowledge_indexes import KnowledgeSearch
from vibradiag.services import knowledge_index_service as index
from vibradiag.services import agent_task_service as tasks

SCOPES={str(uuid5(NAMESPACE_URL,'vibradiag/method-scope-v1/'+equipment+'/'+component)):
    dict(equipment=equipment,component=component,asset_id=None)
    for equipment,component in (('motor','bearing'),('pump','bearing'),('fan','bearing'),('rotating_generic','general'))}


def scope(row):
    value=row['payload'].get('knowledge_scope') or SCOPES.get(row['selection'].get('knowledge_scope_id'))
    return MethodKnowledgeScope.model_validate(value) if value else None


def authorize_scope(c,principal,selected):
    if selected:index._search_access(c,principal,KnowledgeSearch(query='scope check',**selected.model_dump()))


def prepare(identifier,token,principal):
    with transaction() as c:
        row=tasks._running(c,identifier,token);_,current,_=tasks._authorize(c,principal,identifier,write=True,current_only=True)
        selected=scope(row)
    if selected is None:
        choices=[dict(id=key,equipment=value['equipment'],component=value['component'],visibility='organization') for key,value in SCOPES.items()]
        tasks._wait(identifier,token,'knowledge_scope_required',choices=choices,selection_field='knowledge_scope_id')
    query=row['selection'].get('knowledge_query',row['payload']['description'])
    try:return KnowledgeSearch(query=query,**selected.model_dump())
    except ValidationError:tasks._wait(identifier,token,'knowledge_query_required',selection_field='knowledge_query')


def validate_query_answer(row,answer):
    try:KnowledgeSearch(query=answer,**scope(row).model_dump())
    except (ValidationError,AttributeError):raise MeasurementError('KNOWLEDGE_QUERY_INVALID','Supply a plain UTF-8 query of at most 2000 characters',status=422) from None


def _control(identifier,token,principal,connection=None):
    def check(c):
        row=tasks._running(c,identifier,token);tasks._authorize(c,principal,identifier,write=True,current_only=True)
        remaining=120000-tasks._active_budget(row).active_ms
        if remaining<=0:raise PolicyStop('ACTIVE_TIME_EXHAUSTED')
        return min(60,remaining/1000)
    if connection is not None:return check(connection)
    with transaction() as c:return check(c)


def search_for_call(identifier,token,call,principal):
    control=lambda c=None:_control(identifier,token,principal,c)
    control()
    with transaction() as c:
        previous=c.execute(text('SELECT id FROM knowledge_retrieval_runs WHERE agent_task_id=:task AND agent_call_id=:call'),dict(task=identifier,call=call['id'])).scalar_one_or_none()
    if previous:return resolve(previous,principal,identifier,call['id'])
    payload=KnowledgeSearch.model_validate(call['payload'])
    if payload.include_developer:raise MeasurementError('KNOWLEDGE_DEVELOPER_NOT_ALLOWED','Agent cannot use developer-verification retrieval',status=409)
    result=index.search(payload,principal,control=control,binding=dict(task_id=identifier,call_id=call['id'],lease_token=token))
    return resolve(UUID(result['id']),principal,identifier,call['id'])


def resolve(identifier,principal,task_id,call_id,connection=None,*,lock=False):
    def read(c):
        task,current,_=tasks._authorize(c,principal,task_id)
        row=c.execute(text('''SELECT r.*,a.payload AS call_payload FROM knowledge_retrieval_runs r JOIN agent_calls a
            ON a.id=r.agent_call_id AND a.task_id=r.agent_task_id WHERE r.id=:id AND r.agent_task_id=:task
            AND r.agent_call_id=:call AND r.organization_id=:org AND r.actor_id=:actor'''),
            dict(id=identifier,task=task_id,call=call_id,org=current.organization_id,actor=current.user_id)).mappings().first()
        if not row:tasks.conflict('KNOWLEDGE_TASK_REFERENCE_MISMATCH')
        selected=scope(task)
        expected=KnowledgeSearch(query=task['selection'].get('knowledge_query',task['payload']['description']),**selected.model_dump()).model_dump(mode='json') if selected else None
        if not expected or expected!=row['call_payload'] or row['filters']!=dict((k,v) for k,v in expected.items() if k!='query') or row['query']!=expected['query']:
            tasks.conflict('KNOWLEDGE_TASK_REFERENCE_MISMATCH')
        payload=KnowledgeSearch.model_validate(expected)
        if payload.include_developer:tasks.conflict('KNOWLEDGE_DEVELOPER_NOT_ALLOWED')
        try:identity=configured_identity()
        except (OSError,ValueError,TypeError):identity=None
        hits=[];compatible=bool(identity and row['identity_sha256']==identity.fingerprint)
        try:current=index._search_access(c,current,payload)
        except MeasurementError as error:
            if error.status not in {403,404}:raise
            compatible=False
        if compatible:
            for match in row['matches']:
                if lock:
                    # Same order as publication: document, revision, then active index head.
                    document=c.execute(text('''SELECT d.id,d.asset_id FROM knowledge_documents d JOIN knowledge_revisions r ON r.document_id=d.id
                        WHERE r.id=:revision AND d.organization_id=:org FOR SHARE OF d'''),dict(revision=UUID(match['revision_id']),org=current.organization_id)).mappings().one()
                    c.execute(text('SELECT id FROM knowledge_revisions WHERE id=:id FOR SHARE'),dict(id=UUID(match['revision_id'])))
                    c.execute(text('SELECT revision_id FROM knowledge_active_indexes WHERE revision_id=:id FOR SHARE'),dict(id=UUID(match['revision_id'])))
                    if document['asset_id']:
                        c.execute(text('SELECT asset_id FROM asset_grants WHERE asset_id=:asset AND user_id=:actor AND organization_id=:org FOR SHARE'),
                            dict(asset=document['asset_id'],actor=current.user_id,org=current.organization_id))
                params=index._parameters(current,payload,identity,row['runtime_version'])
                params.update(chunk=UUID(match['chunk_id']),run=UUID(match['index_id']))
                candidate=c.execute(text('SELECT * FROM ('+index.ELIGIBLE+') e WHERE e.id=:chunk AND e.index_id=:run'),params).mappings().first()
                if not candidate:continue
                hit=index._resolve(c,current,dict(candidate,distance=1-match['similarity']))
                if not hit:continue
                for field in ('text_sha256','source_sha256','line_start','line_end','char_start','char_end'):
                    if hit[field]!=match[field]:tasks.conflict('KNOWLEDGE_REFERENCE_INTEGRITY')
                hits.append(dict(hit,formal_retrieval_available=True,untrusted_content=True))
        return dict(retrieval_id=str(row['id']),status='completed',result_status='ok' if hits else 'unavailable',
            hits=hits,current_and_valid=compatible and len(hits)==len(row['matches']),original_match_count=len(row['matches']),
            embedding_called=row['model_called'],identity_sha256=row['identity_sha256'],relevance_qualified=False)
    if connection is not None:return read(connection)
    with transaction() as c:return read(c)


def validate_draft(c,row,principal,draft,calls):
    resolved=[];references=[]
    for call in calls:
        if call['resource_kind']!='knowledge_retrieval':continue
        source=resolve(call['resource_id'],principal,row['id'],call['id'],c,lock=True)
        if not source['current_and_valid']:
            if draft['observations']:tasks.conflict('KNOWLEDGE_REFERENCE_UNAVAILABLE')
            continue
        for hit in source['hits']:
            resolved.append(ResolvedReference(kind='knowledge_chunk',id=hit['id'],organization_id=row['organization_id'],
                measurement_revision_ids=(),permitted_claim='method_explanation',current_and_valid=True))
            references.append(hit)
    try:validate_draft_references(draft,TaskScope(task_id=row['id'],organization_id=row['organization_id'],measurement_revision_ids=()),resolved)
    except PolicyStop as error:raise MeasurementError(error.code,'Knowledge draft references are unavailable',status=409) from None
    return references
