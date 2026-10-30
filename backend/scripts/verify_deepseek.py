"""Real API integration smoke tests on synthetic, non-sensitive inputs."""
import hashlib
import json
import sys
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from vibradiag.core.paths import PROJECT_ROOT as ROOT,BACKEND_ROOT
from vibradiag.agents.graph import run_agent
from vibradiag.analysis.signal import analyze_csv
from vibradiag.core.config import default_model
from vibradiag.infrastructure.llm.deepseek import models

cases=[
 {'id':'DS01','description':'只查看这份信号的RMS和主频，不讨论轴承故障类型。','rpm':1480,'expected_tool':'inspect_spectrum'},
 {'id':'DS02','description':'我怀疑内圈或外圈故障，但不知道本次采集转速，请先检查诊断必要条件。','rpm':None,'expected_tool':'check_bearing_preconditions'},
 {'id':'DS03','description':'先检索知识资料：为什么需要做包络分析？我想了解分析方法，不要先算故障频率。','rpm':1480,'expected_tool':'search_knowledge'}]
model=default_model()
available=models()
if not any('deepseek/'+item['id']==model for item in available):
    raise SystemExit('配置模型未在DeepSeek模型列表中发现，不自动切换或回退')
x=np.sin(2*np.pi*128*np.arange(4096)/4096)
raw=('value\n'+'\n'.join(map(str,x))).encode()
analysis=analyze_csv(raw,4096,'m/s2')
report={'created_at':datetime.now(timezone.utc).isoformat(),'model':model,'available_models':available,
        'purpose':'real API connectivity, tool IDs, evidence and JSON validation smoke tests; not fault accuracy',
        'external_data':'synthetic signal features, user test question, metadata and authored development knowledge; no raw waveform',
        'protocol':{'thinking':False,'temperature':0,'max_tokens':1024,'max_tool_decisions':2,'max_output_repairs':1,
                    'schema_enforcement':'JSON object mode plus local Pydantic validation, unlike Ollama native schema decoding',
                    'comparability':'not a controlled quality/latency comparison with the 512-token local runs'},
        'source_sha256':{name:hashlib.sha256((BACKEND_ROOT/'src/vibradiag'/name).read_bytes()).hexdigest() for name in ['agents/graph.py','infrastructure/llm/deepseek.py','infrastructure/llm/router.py']},
        'cases':[]}
path=ROOT/'experiments/deepseek-api-smoke.json'
for case in cases:
    print('Starting',case['id'],flush=True)
    result=run_agent(analysis,case['description'],case['rpm'],model)
    tools=[t['name'] for t in result.get('trace',[]) if t['stage']=='tool']
    passed=result.get('draft') is not None and case['expected_tool'] in tools
    if case['rpm'] is None:
        passed=passed and result['draft']['status']=='needs_information'
    report['cases'].append({**case,'passed':passed,'result':result})
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(case['id'],'passed=',passed,'tools=',tools,'seconds=',round(result['wall_seconds'],2),flush=True)
report['passed_count']=sum(item['passed'] for item in report['cases'])
report['total_tokens']=sum(m.get('total_tokens',0) or 0 for item in report['cases'] for m in item['result'].get('metrics',[]))
path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(path,flush=True)
