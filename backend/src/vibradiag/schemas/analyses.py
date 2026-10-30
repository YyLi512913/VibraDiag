from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class AnalysisRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    measurement_revision_id: UUID
    confirmation_id: UUID
    config_profile: Literal['basic-v1']='basic-v1'
    retry_of: UUID | None=None
