"""Read-only authenticated model discovery. Never prints secrets or response headers."""
import json
import sys
from pathlib import Path
from vibradiag.core.paths import PROJECT_ROOT as ROOT
from vibradiag.infrastructure.llm.deepseek import models
from vibradiag.core.config import setting
try:
    available=models()
    requested=setting('VIBRADIAG_DEEPSEEK_MODEL','deepseek-flash')
    result={'status':'ok','configured_model':requested,'models':available,'configured_available':any(m['id']==requested for m in available)}
except Exception as exc:
    result={'status':'failed','error':str(exc)}
file=ROOT/'experiments/deepseek-connection.json'
file.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
