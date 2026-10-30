from vibradiag.infrastructure.retrieval.vector_index import KnowledgeIndex
from .execution_gate import ModelExecutionGate

class KnowledgeService:
    def __init__(self,gate:ModelExecutionGate):
        self.gate=gate

    def search(self,query:str,model:str):
        with self.gate.acquire():
            hits,timing=KnowledgeIndex(model).search(query)
            return {'hits':hits,'timing':timing,'corpus':'开发自编资料，未经专家审核'}
