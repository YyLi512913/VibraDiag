import json
import uuid
from pathlib import Path
from vibradiag.core.paths import RUNTIME_ROOT
from vibradiag.domain.errors import ResourceNotFound

class LocalAnalysisRepository:
    def __init__(self,root:Path|None=None):
        self.root=root or RUNTIME_ROOT
        self.analyses=self.root/'analyses'
        self.diagnoses=self.root/'diagnoses'
        self.analyses.mkdir(parents=True,exist_ok=True)
        self.diagnoses.mkdir(parents=True,exist_ok=True)

    @staticmethod
    def _write_json(path:Path,data:dict):
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        temporary.replace(path)

    def create_analysis(self,raw:bytes,result:dict,filename:str):
        identifier=uuid.uuid4().hex
        record={**result,'analysis_id':identifier,'original_filename':Path(filename or 'unknown').name,
                'data_source':'user_upload_unverified'}
        (self.analyses/(identifier+'.csv')).write_bytes(raw)
        self._write_json(self.analyses/(identifier+'.json'),record)
        return record

    def get_analysis(self,identifier:str):
        try:
            if uuid.UUID(identifier).hex!=identifier:
                raise ValueError()
        except (ValueError,AttributeError):
            raise ResourceNotFound('分析不存在') from None
        file=self.analyses/(identifier+'.json')
        if not file.exists():
            raise ResourceNotFound('分析不存在')
        return json.loads(file.read_text(encoding='utf-8'))

    def save_diagnosis(self,analysis_id:str,result:dict):
        record={**result,'diagnosis_id':uuid.uuid4().hex,'analysis_id':analysis_id}
        self._write_json(self.diagnoses/(record['diagnosis_id']+'.json'),record)
        return record
