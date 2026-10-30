from fastapi import APIRouter,Depends
from vibradiag.api.dependencies import get_services,Services
from vibradiag.schemas.requests import SearchRequest

router=APIRouter()

@router.post('/knowledge/search',tags=['knowledge'])
def search(request:SearchRequest,services:Services=Depends(get_services)):
    return services.knowledge.search(request.query,request.embedding_model)
