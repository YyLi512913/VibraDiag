from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request, Response

from vibradiag.infrastructure.persistence.analysis_store import KINDS
from vibradiag.schemas.analyses import AnalysisRequest
from vibradiag.services import persistent_analysis_service as service
from .identity import current_principal

router=APIRouter(prefix='/analyses',tags=['formal analyses'])


@router.get('')
def listing(measurement_id:UUID,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),
            principal=Depends(current_principal)):
    return service.list_analyses(measurement_id,principal,page,page_size)


@router.post('')
def create(payload:AnalysisRequest,request:Request,response:Response,
           idempotency_key:str=Header(...,alias='Idempotency-Key'),principal=Depends(current_principal)):
    result,status=service.create_analysis(payload,idempotency_key,principal,request.state.request_id)
    response.status_code=status
    response.headers['Location']='/api/v1/analyses/'+result['analysis_id']
    if status==202:
        response.headers['Retry-After']='2'
    return result


@router.get('/{analysis_id}')
def read(analysis_id:UUID,principal=Depends(current_principal)):
    return service.read_analysis(analysis_id,principal)


@router.get('/{analysis_id}/series')
def read_series(analysis_id:UUID,kind:Literal['waveform','spectrum']=Query('waveform'),principal=Depends(current_principal)):
    return service.series(analysis_id,kind,principal)


@router.get('/{analysis_id}/artifacts/{kind}')
def download(analysis_id:UUID,kind:Literal['result','waveform_preview','amplitude_spectrum','manifest'],request:Request,
             principal=Depends(current_principal)):
    payload=service.artifact(analysis_id,kind,principal,request.state.request_id)
    return Response(payload,media_type='application/octet-stream' if kind=='amplitude_spectrum' else 'application/json',
                    headers={'Content-Disposition':f'attachment; filename="{KINDS[kind]}"','X-Content-Type-Options':'nosniff'})
