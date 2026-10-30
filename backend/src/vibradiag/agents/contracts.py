from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Observation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(max_length=160)
    evidence_ids:list[str]=Field(min_length=1)

class Draft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Literal['analysis_only','needs_information','insufficient_evidence']
    summary:str=Field(max_length=160)
    observations:list[Observation]=Field(min_length=1,max_length=3)
    missing_information:list[str]=Field(max_length=4)
    next_steps:list[str]=Field(max_length=3)
