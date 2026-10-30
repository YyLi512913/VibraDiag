import math
from typing import Literal
from fastapi import APIRouter,Depends,UploadFile,File,Form
from fastapi.responses import Response
from vibradiag.api.dependencies import get_services,Services

router=APIRouter()

@router.get('/samples/sine.csv',tags=['samples'])
def sample():
    data='value\n'+'\n'.join(str(math.sin(2*math.pi*128*i/4096)) for i in range(4096))
    return Response(data,media_type='text/csv',headers={'Content-Disposition':'attachment; filename="synthetic-128hz.csv"'})

@router.post('/prototype/analyses',tags=['prototype analyses'])
def analyze(file:UploadFile=File(...),sampling_rate:float=Form(...),unit:Literal['g','m/s2']=Form(...),services:Services=Depends(get_services)):
    raw=file.file.read(32*1024*1024+1)
    return services.analyses.create(raw,sampling_rate,unit,file.filename or 'unknown')

@router.get('/prototype/analyses/{identifier}',tags=['prototype analyses'])
def get_analysis(identifier:str,services:Services=Depends(get_services)):
    return services.analyses.get(identifier)
