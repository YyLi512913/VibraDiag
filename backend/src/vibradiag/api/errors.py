from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from vibradiag.domain.errors import ApplicationError,InvalidInput,ResourceNotFound,ResourceBusy,PayloadTooLarge

async def request_validation_error(request,exception:RequestValidationError):
    errors=[{'location':list(error['loc']),'type':error['type'],'message':error['msg']} for error in exception.errors()]
    if request.url.path.startswith(('/api/v1/measurements','/api/v1/analyses','/api/v1/tool-runs','/api/v1/knowledge-documents','/api/v1/knowledge-indexes')):
        return JSONResponse(status_code=422,content={'error':{'code':'REQUEST_INVALID','message':'Invalid request fields',
            'fields':errors},'request_id':str(request.state.request_id)})
    return JSONResponse(status_code=422,content={'detail':errors})

async def application_error(request,exception:ApplicationError):
    codes={InvalidInput:422,ResourceNotFound:404,ResourceBusy:409,PayloadTooLarge:413}
    return JSONResponse(status_code=codes.get(type(exception),400),content={'detail':str(exception)})
