from typing import Annotated,Literal
from uuid import UUID
from urllib.parse import urlsplit
from pydantic import BaseModel,ConfigDict,Field,SecretStr,model_validator

Text=Annotated[str,Field(min_length=1,max_length=2000)]
Version=Annotated[int,Field(strict=True,ge=1)]


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,str_strip_whitespace=True)


class KnowledgeMetadata(Strict):
    title: Annotated[Text,Field(max_length=300)]
    source_note: Text
    equipment_scope: Annotated[tuple[Literal['motor','pump','fan','rotating_generic','unknown'],...],Field(min_length=1,max_length=5)]
    component_scope: Literal['bearing','rotating_assembly','general','unknown']
    content_version: Annotated[Text,Field(max_length=200)]
    usage_rights: Text
    source_url: Annotated[str,Field(max_length=2000)] | None=None
    author: Annotated[str,Field(max_length=200)] | None=None
    content_origin: Literal['developer_test','public_summary','vendor_authorized','internal_note']
    visibility: Literal['organization','asset']
    asset_id: UUID | None=None
    reason: Text
    expected_document_version: Version | None=None

    @model_validator(mode='after')
    def scope(self):
        if (self.visibility=='asset')!=(self.asset_id is not None):raise ValueError('asset scope requires exactly one asset')
        if len(set(self.equipment_scope))!=len(self.equipment_scope):raise ValueError('equipment scope must be unique')
        if self.source_url:
            url=urlsplit(self.source_url)
            if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or any(ord(c)<33 for c in self.source_url):
                raise ValueError('source URL must be credential-free http(s); it is never fetched')
        return self


class KnowledgeAction(Strict):
    expected_version: Version
    expected_document_version: Version
    reason: Text


class ReviewGrant(Strict):
    password: Annotated[SecretStr,Field(min_length=1,max_length=1024)]
    user_id: UUID
    enabled: Annotated[bool,Field(strict=True)]
    expected_version: Annotated[int,Field(strict=True,ge=0)]
    reason: Text
