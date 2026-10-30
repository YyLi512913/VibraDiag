from typing import Annotated, Literal
from uuid import UUID
from pydantic import Field, field_validator
from .knowledge_documents import Strict, Text, Version


class IndexBuild(Strict):
    expected_version: Version
    expected_document_version: Version
    expected_index_version: Annotated[int,Field(strict=True,ge=0)]
    reason: Text


class KnowledgeSearch(Strict):
    query: Text
    asset_id: UUID | None=None
    equipment: Literal['motor','pump','fan','rotating_generic']='motor'
    component: Literal['bearing','rotating_assembly','general']='bearing'
    limit: Annotated[int,Field(strict=True,ge=1,le=5)]=5
    include_developer: Annotated[bool,Field(strict=True)]=False

    @field_validator('query')
    @classmethod
    def plain_query(cls,value):
        if any(ord(c)<32 and c not in '\n\t' for c in value):raise ValueError('Plain query text required')
        try:value.encode('utf8')
        except UnicodeError:raise ValueError('Valid UTF-8 query required') from None
        return value
