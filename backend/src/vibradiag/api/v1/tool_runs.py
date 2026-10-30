from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from vibradiag.schemas.tool_runs import ToolRunRequest
from vibradiag.services import tool_run_service as service
from vibradiag.infrastructure.persistence.tool_store import KINDS
from .identity import current_principal
from vibradiag.services.tool_options_service import options
from vibradiag.services.comparison_service import options as comparison_options

router = APIRouter(prefix='/tool-runs', tags=['formal numerical tools'])


@router.get('/options/{measurement_id}')
def available(measurement_id:UUID,principal=Depends(current_principal)):
    return options(measurement_id,principal)


@router.get('/comparison-options/{measurement_id}')
def comparisons(measurement_id:UUID,principal=Depends(current_principal)):
    return comparison_options(measurement_id,principal)


@router.post('')
def create(payload:ToolRunRequest, request:Request, response:Response,
           idempotency_key:str=Header(...,alias='Idempotency-Key'), principal=Depends(current_principal)):
    result, status = service.create_run(payload, idempotency_key, principal, request.state.request_id)
    response.status_code = status
    response.headers['Location'] = '/api/v1/tool-runs/' + result['tool_execution_id']
    if status == 202:
        response.headers['Retry-After'] = '2'
    return result


@router.get('')
def listing(measurement_id:UUID, page:int=Query(1,ge=1), page_size:int=Query(20,ge=1,le=100), principal=Depends(current_principal)):
    return service.list_runs(measurement_id, principal, page, page_size)


@router.get('/{identifier}')
def read(identifier:UUID, principal=Depends(current_principal)):
    return service.read_run(identifier, principal)


@router.get('/{identifier}/series')
def series(identifier:UUID, kind:Literal['envelope','spectrum']=Query('envelope'), principal=Depends(current_principal)):
    return service.series(identifier, kind, principal)


@router.get('/{identifier}/artifacts/{kind}')
def artifact(identifier:UUID, kind:Literal['result','arrays','manifest'], request:Request, principal=Depends(current_principal)):
    return Response(service.artifact(identifier,kind,principal,request.state.request_id),
        media_type='application/octet-stream' if kind=='arrays' else 'application/json',
        headers={'Content-Disposition':f'attachment; filename="{KINDS[kind]}"','X-Content-Type-Options':'nosniff'})
