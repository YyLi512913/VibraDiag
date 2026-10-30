from vibradiag.analysis.signal import analyze_csv
from vibradiag.domain.errors import InvalidInput,PayloadTooLarge
from .ports import AnalysisRepository

class AnalysisService:
    def __init__(self,repository:AnalysisRepository):
        self.repository=repository

    def create(self,raw:bytes,sampling_rate:float,unit:str,filename:str):
        if len(raw)>32*1024*1024:
            raise PayloadTooLarge('文件超过32 MiB')
        try:
            result=analyze_csv(raw,sampling_rate,unit)
        except ValueError as exc:
            raise InvalidInput(str(exc)) from None
        return self.repository.create_analysis(raw,result,filename)

    def get(self,identifier:str):
        return self.repository.get_analysis(identifier)
