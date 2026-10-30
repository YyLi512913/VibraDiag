from fastapi import APIRouter,Depends
from vibradiag.api.dependencies import get_services,Services
from vibradiag.schemas.requests import DiagnoseRequest

router=APIRouter()

@router.post('/diagnoses',tags=['diagnoses'])
def diagnose(request:DiagnoseRequest,services:Services=Depends(get_services)):
    return services.diagnoses.run(**request.model_dump())
