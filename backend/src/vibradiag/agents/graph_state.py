"""Only a cursor binding is persisted; identities, budgets and facts remain in business tables."""
from typing import Literal,TypedDict
from uuid import UUID
from pydantic import BaseModel,ConfigDict
from contextvars import ContextVar

GRAPH_VERSION='rules-graph-v1'
EXECUTOR='rules_graph_v1'
CHECKPOINT_SCHEMA='public_agent_checkpoints'
NODES=('prepare','basic','profile','envelope','context','frequency','bound_envelope','matching','comparison')
KNOWLEDGE_GRAPH='rules-knowledge-v1'
KNOWLEDGE_EXECUTOR='rules_knowledge_v1'
MODEL_GRAPH='model-method-v1'
MODEL_EXECUTOR='model_method_v1'
MODEL_GRAPH_V2='model-method-v2'
MODEL_EXECUTOR_V2='model_method_v2'
NUMERIC_GRAPH='model-numeric-v1'
NUMERIC_EXECUTOR='model_numeric_v1'
NUMERIC_GRAPH_V2='model-numeric-v2'
NUMERIC_EXECUTOR_V2='model_numeric_v2'
NUMERIC_NODES=('prepare',)+tuple(f'decide_{i}' for i in range(6))+tuple(f'act_{i}' for i in range(5))
GRAPH_NODES={NUMERIC_GRAPH_V2:NUMERIC_NODES,NUMERIC_GRAPH:NUMERIC_NODES,GRAPH_VERSION:NODES,KNOWLEDGE_GRAPH:('prepare','knowledge'),MODEL_GRAPH:('prepare','decide','knowledge','compose'),MODEL_GRAPH_V2:('prepare','decide','knowledge','compose')}
GRAPH_EXECUTORS={NUMERIC_GRAPH_V2:NUMERIC_EXECUTOR_V2,NUMERIC_GRAPH:NUMERIC_EXECUTOR,GRAPH_VERSION:EXECUTOR,KNOWLEDGE_GRAPH:KNOWLEDGE_EXECUTOR,MODEL_GRAPH:MODEL_EXECUTOR,MODEL_GRAPH_V2:MODEL_EXECUTOR_V2}
defer_wait=ContextVar('agent_graph_defer_wait',default=False)


class Cursor(TypedDict):
    task_id: str
    graph_version: str


class CursorBinding(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    task_id: str
    graph_version: Literal['rules-graph-v1','rules-knowledge-v1','model-method-v1','model-method-v2','model-numeric-v1','model-numeric-v2']

    def check(self,task_id,version=None):
        if self.task_id!=str(task_id) or str(UUID(self.task_id))!=self.task_id:raise ValueError('Invalid graph task binding')
        if version is not None and self.graph_version!=version:raise ValueError('Invalid graph version binding')


class GraphWait(Exception):
    def __init__(self,code,choices,selection_field,status):
        super().__init__('TASK_INFORMATION_REQUIRED')
        self.code=code;self.choices=choices;self.selection_field=selection_field;self.status=status
