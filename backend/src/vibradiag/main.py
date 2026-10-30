from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from vibradiag.api.router import router
from vibradiag.api.errors import application_error,request_validation_error
from vibradiag.domain.errors import ApplicationError
from vibradiag.infrastructure.llm.ollama import call
from vibradiag.core.config import default_model,external_enabled,setting
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from uuid import uuid4
from vibradiag.api.upload_limits import MeasurementBodyLimit
from vibradiag.domain.measurement_errors import MeasurementError
from contextlib import asynccontextmanager
from vibradiag.services.analysis_runtime import runtime
from vibradiag.services.agent_runtime import agent_runtime

@asynccontextmanager
async def lifespan(app):
    runtime.start()
    hosted=False
    try:
        hosted=agent_runtime.start()
        yield
    finally:
        try:
            if hosted:agent_runtime.stop()
        finally:
            runtime.stop()

app=FastAPI(title='VibraDiag API',version='0.1.0',lifespan=lifespan)
app.add_exception_handler(RequestValidationError,request_validation_error)
app.add_exception_handler(ApplicationError,application_error)
app.include_router(router)
app.add_middleware(MeasurementBodyLimit)

@app.exception_handler(MeasurementError)
async def measurement_failure(request, exception):
    error={'code':exception.code,'message':exception.message}
    if exception.field is not None:
        error['field']=exception.field
    if exception.row is not None:
        error['row']=exception.row
    return JSONResponse(status_code=exception.status,content={'error':error,'request_id':str(request.state.request_id)},
                        headers={'Retry-After':'2'} if exception.status==429 else None)

@app.exception_handler(OSError)
async def storage_failure(request, exception):
    return JSONResponse(status_code=503,content={'error':{'code':'STORAGE_UNAVAILABLE',
        'message':'File storage unavailable'},'request_id':str(request.state.request_id)})

@app.middleware('http')
async def request_boundary(request, call_next):
    request.state.request_id=uuid4()
    path=request.url.path
    experimental=any(path==prefix or path.startswith(prefix+'/') for prefix in
                     ('/api/v1/prototype/analyses','/api/v1/diagnoses','/api/v1/knowledge','/api/v1/samples'))
    if experimental and setting('VIBRADIAG_ENABLE_PROTOTYPE','false').lower()!='true':
        response=JSONResponse(status_code=503,content={'detail':'Prototype endpoints disabled; M1 data API pending'})
    else:
        response=await call_next(request)
    response.headers['X-Request-ID']=str(request.state.request_id)
    response.headers['Cache-Control']='no-store'
    return response

@app.exception_handler(SQLAlchemyError)
async def database_failure(request, exception):
    return JSONResponse(status_code=503,content={'error':{'code':'DATABASE_UNAVAILABLE',
        'message':'Database operation unavailable'},'request_id':str(request.state.request_id)})

@app.get('/')
def index():
    return {'service':'VibraDiag API','docs':'/docs','frontend_dev_url':'http://127.0.0.1:5173'}

@app.get('/health')
def health():
    try:
        available=[item['name'] for item in call('/api/tags')['models']]
        status='ok'
    except Exception:
        available=[];status='degraded'
    return {'status':status,'ollama_models':available,'mode':'local_development_only',
            'default_agent_model':default_model(),'external_llm_enabled':external_enabled(),
            'deepseek_key_configured':bool(setting('DEEPSEEK_API_KEY'))}
