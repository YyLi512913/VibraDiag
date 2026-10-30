from typing import Literal
from pydantic import BaseModel,Field,ConfigDict
from vibradiag.core.config import default_model

class SearchRequest(BaseModel):
    query:str=Field(min_length=1,max_length=200)
    embedding_model:Literal['bge-m3:567m','qwen3-embedding:0.6b']='bge-m3:567m'

class DiagnoseRequest(BaseModel):
    model_config=ConfigDict(allow_inf_nan=False)
    analysis_id:str
    description:str=Field(min_length=1,max_length=1000)
    rpm:float|None=Field(default=None,gt=0)
    model:Literal['deepseek/deepseek-flash','deepseek/deepseek-v4-pro','qwen3.5:2b','qwen3.5:0.8b']=Field(default_factory=default_model)
    embedding_model:Literal['bge-m3:567m','qwen3-embedding:0.6b']='bge-m3:567m'
