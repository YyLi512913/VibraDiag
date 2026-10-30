from threading import BoundedSemaphore
from uuid import UUID
from fastapi import APIRouter,Depends,Header,Query,Request,Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from vibradiag.domain.knowledge_text import MAX_BYTES
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.schemas.knowledge_documents import KnowledgeMetadata,KnowledgeAction,ReviewGrant
from vibradiag.services import knowledge_document_service as service
from .identity import current_principal,check_login_limit

router=APIRouter(prefix='/knowledge-documents',tags=['versioned knowledge publication'])
_imports=BoundedSemaphore(2)


@router.post('/review-grants')
def grant(payload:ReviewGrant,request:Request,response:Response,key:str=Header(...,alias='Idempotency-Key'),principal=Depends(current_principal)):
    check_login_limit(request)
    result,status=service.set_review_grant(payload,key,principal);response.status_code=status;return result


@router.get('/chunks/{chunk_id}')
def chunk(chunk_id:UUID,principal=Depends(current_principal)):return service.read_chunk(chunk_id,principal)


@router.get('/revisions/{revision_id}')
def revision(revision_id:UUID,principal=Depends(current_principal)):return service.read_revision(revision_id,principal)


@router.get('/revisions/{revision_id}/chunks')
def chunk_list(revision_id:UUID,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),principal=Depends(current_principal)):
    return service.chunks(revision_id,principal,page,page_size)


@router.get('/revisions/{revision_id}/original')
def original(revision_id:UUID,principal=Depends(current_principal)):
    raw,format=service.original(revision_id,principal)
    return Response(raw,media_type='text/plain',headers={'Content-Disposition':f'attachment; filename="knowledge-{revision_id}.{format}"','X-Content-Type-Options':'nosniff'})


async def upload_form(request,response,key,principal,document_id=None):
    if 'engineer' not in principal.roles:service.denied()
    if not _imports.acquire(blocking=False):raise MeasurementError('KNOWLEDGE_IMPORT_CAPACITY','Knowledge import capacity is full',status=429)
    try:
        try:
            async with request.form(max_files=1,max_fields=1,max_part_size=16*1024) as form:
                if set(form.keys())!={'file','metadata'} or len(form.multi_items())!=2:raise MeasurementError('KNOWLEDGE_UPLOAD_FIELDS','Supply exactly file and metadata',status=422)
                file=form['file'];metadata=form['metadata']
                if not isinstance(file,UploadFile) or not isinstance(metadata,str):raise MeasurementError('KNOWLEDGE_UPLOAD_FIELDS','Expected file and metadata JSON',status=422)
                try:payload=KnowledgeMetadata.model_validate_json(metadata)
                except ValidationError:raise MeasurementError('KNOWLEDGE_METADATA_INVALID','Invalid knowledge source, scope or version metadata',status=422) from None
                raw=await file.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:raise MeasurementError('KNOWLEDGE_FILE_TOO_LARGE','Knowledge file exceeds 5 MiB',status=413)
                result,status=await run_in_threadpool(service.import_document,raw,file.filename,payload,key,principal,document_id)
        except HTTPException as error:
            large=error.detail=='knowledge multipart body exceeds limit'
            raise MeasurementError('KNOWLEDGE_FILE_TOO_LARGE' if large else 'MULTIPART_INVALID','Knowledge multipart body exceeds limit' if large else 'Invalid multipart upload',status=413 if large else 422) from None
        response.status_code=status;response.headers['Location']='/api/v1/knowledge-documents/revisions/'+result['id'];return result
    finally:_imports.release()


@router.post('')
async def create(request:Request,response:Response,key:str=Header(...,alias='Idempotency-Key'),principal=Depends(current_principal)):
    return await upload_form(request,response,key,principal)


@router.post('/{document_id}/revisions')
async def revise(document_id:UUID,request:Request,response:Response,key:str=Header(...,alias='Idempotency-Key'),principal=Depends(current_principal)):
    return await upload_form(request,response,key,principal,document_id)


@router.post('/revisions/{revision_id}/{action}')
def transition(revision_id:UUID,action:str,payload:KnowledgeAction,response:Response,key:str=Header(...,alias='Idempotency-Key'),principal=Depends(current_principal)):
    if action not in {'submit','reject','publish','withdraw'}:service.not_found()
    result,status=service.transition(revision_id,action,payload,key,principal);response.status_code=status;return result


@router.get('')
def listing(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),principal=Depends(current_principal)):
    return service.list_documents(principal,page,page_size)


@router.get('/{document_id}')
def read(document_id:UUID,principal=Depends(current_principal)):return service.read_document(document_id,principal)
