from dataclasses import dataclass
from functools import lru_cache
from vibradiag.infrastructure.persistence.local_repository import LocalAnalysisRepository
from vibradiag.services.analysis_service import AnalysisService
from vibradiag.services.diagnosis_service import DiagnosisService
from vibradiag.services.knowledge_service import KnowledgeService
from vibradiag.services.execution_gate import ModelExecutionGate

@dataclass
class Services:
    analyses:AnalysisService
    diagnoses:DiagnosisService
    knowledge:KnowledgeService

@lru_cache
def get_services():
    repository=LocalAnalysisRepository()
    gate=ModelExecutionGate()
    return Services(AnalysisService(repository),DiagnosisService(repository,gate),KnowledgeService(gate))
