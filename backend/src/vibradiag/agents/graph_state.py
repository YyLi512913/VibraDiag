"""Only a cursor binding is persisted; identities, budgets and facts remain in business tables."""
from typing import Literal,TypedDict
from uuid import UUID
from pydantic import BaseModel,ConfigDict
from contextvars import ContextVar

GRAPH_VERSION='rules-graph-v1'
EXECUTOR='rules_graph_v1'
CHECKPOINT_SCHEMA='public_agent_checkpoints'
NODES=('prepare','basic','profile','envelope','context','frequency','bound_envelope','matching','comparison')
defer_wait=ContextVar('agent_graph_defer_wait',default=False)


class Cursor(TypedDict):
    task_id: str
    graph_version: str


class CursorBinding(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    task_id: str
    graph_version: Literal['rules-graph-v1']

    def check(self,task_id):
        if self.task_id!=str(task_id) or str(UUID(self.task_id))!=self.task_id:raise ValueError('Invalid graph task binding')


class GraphWait(Exception):
    def __init__(self,code,choices,selection_field,status):
        super().__init__('TASK_INFORMATION_REQUIRED')
        self.code=code;self.choices=choices;self.selection_field=selection_field;self.status=status
