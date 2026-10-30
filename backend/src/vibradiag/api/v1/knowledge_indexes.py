from uuid import UUID
from fastapi import APIRouter,Depends,Header,Response
from vibradiag.schemas.knowledge_indexes import IndexBuild,KnowledgeSearch
from vibradiag.services import knowledge_index_service as service
from .identity import current_principal

router=APIRouter(prefix='/knowledge-indexes',tags=['authorized dense knowledge retrieval'])


@router.post('/search')
def search(payload:KnowledgeSearch,principal=Depends(current_principal)):
    return service.search(payload,principal)


@router.get('/retrievals/{retrieval_id}')
def receipt(retrieval_id:UUID,principal=Depends(current_principal)):
    return service.read_retrieval(retrieval_id,principal)


@router.post('/revisions/{revision_id}/build')
def build(revision_id:UUID,payload:IndexBuild,response:Response,key:str=Header(...,alias='Idempotency-Key'),principal=Depends(current_principal)):
    result,status=service.build(revision_id,payload,key,principal);response.status_code=status;return result


@router.get('/revisions/{revision_id}')
def versions(revision_id:UUID,principal=Depends(current_principal)):
    return service.revision_indexes(revision_id,principal)


@router.get('/runs/{run_id}')
def run(run_id:UUID,principal=Depends(current_principal)):
    return service.read_run(run_id,principal)
