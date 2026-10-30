from uuid import UUID
from fastapi import APIRouter,Depends,Header,Query,Response
from vibradiag.agents.formal_contracts import CreateAgentTask,ResumeAgentTask
from vibradiag.schemas.agent_tasks import CancelAgentTask
from vibradiag.services import agent_task_service as service
from .identity import current_principal

router=APIRouter(prefix='/agent-tasks',tags=['persistent exploratory tasks'])


@router.get('')
def listing(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),principal=Depends(current_principal)):
    return service.list_tasks(principal,page,page_size)


def accepted(response,result,status):
    response.status_code=status
    response.headers['Location']='/api/v1/agent-tasks/'+result['id']
    if status==202:response.headers['Retry-After']='2'
    return result


@router.post('')
def create(payload:CreateAgentTask,response:Response,idempotency_key:str=Header(...,alias='Idempotency-Key'),
           principal=Depends(current_principal)):
    return accepted(response,*service.create_task(payload,idempotency_key,principal))


@router.get('/{task_id}')
def read(task_id:UUID,principal=Depends(current_principal)):
    return service.read_task(task_id,principal)


@router.post('/{task_id}/resume')
def resume(task_id:UUID,payload:ResumeAgentTask,response:Response,idempotency_key:str=Header(...,alias='Idempotency-Key'),
           principal=Depends(current_principal)):
    return accepted(response,*service.resume_task(task_id,payload,idempotency_key,principal))


@router.post('/{task_id}/cancel')
def cancel(task_id:UUID,payload:CancelAgentTask,response:Response,idempotency_key:str=Header(...,alias='Idempotency-Key'),
           principal=Depends(current_principal)):
    return accepted(response,*service.cancel_task(task_id,payload.expected_version,idempotency_key,principal))
