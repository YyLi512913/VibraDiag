from vibradiag.agents.graph import run_agent
from .ports import AnalysisRepository
from .execution_gate import ModelExecutionGate

class DiagnosisService:
    def __init__(self,repository:AnalysisRepository,gate:ModelExecutionGate):
        self.repository=repository
        self.gate=gate

    def run(self,analysis_id:str,description:str,rpm:float|None,model:str,embedding_model:str):
        analysis=self.repository.get_analysis(analysis_id)
        with self.gate.acquire():
            result=run_agent(analysis,description,rpm,model,embedding_model)
            return self.repository.save_diagnosis(analysis_id,result)
