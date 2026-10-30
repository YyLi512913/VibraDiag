import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from vibradiag.core.paths import PROJECT_ROOT as ROOT,BACKEND_ROOT
from vibradiag.analysis.signal import analyze_csv
from vibradiag.agents.graph import run_agent
from vibradiag.infrastructure.llm.ollama import metadata,call

CASES=[
 {'id':'A01','description':'只查看这份信号的RMS和主频，不讨论轴承故障类型。','rpm':1480,'expected_tool':'inspect_spectrum'},
 {'id':'A02','description':'我怀疑内圈或外圈故障，但不知道本次采集转速，请先检查诊断必要条件。','rpm':None,'expected_tool':'check_bearing_preconditions'},
 {'id':'A03','description':'先检索知识资料：为什么需要做包络分析？我想了解分析方法，不要先算故障频率。','rpm':1480,'expected_tool':'search_knowledge'}]
parser=argparse.ArgumentParser();parser.add_argument('--models',nargs='+',default=['qwen3.5:2b','qwen3.5:0.8b']);args=parser.parse_args()
x=np.sin(2*np.pi*128*np.arange(4096)/4096)
raw=('value\n'+'\n'.join(map(str,x))).encode()
analysis=analyze_csv(raw,4096,'m/s2')
output=ROOT/'experiments/agent-dev-results.json';output.parent.mkdir(exist_ok=True)
report={'created_at':datetime.now(timezone.utc).isoformat(),'purpose':'3 authored tool-routing and output-validation smoke cases per model; not fault accuracy or held-out benchmark',
 'protocol':{'thinking':False,'temperature':0,'seed':42,'context':4096,'max_output_tokens':512,'max_decision_rounds':2,'repetitions':1,
 'embedding_model':'bge-m3:567m','model_order':args.models,'warmup':False,'signal_sha256':hashlib.sha256(raw).hexdigest(),
 'output_contract':'round3: native JSON schema plus evidence enum and at most one validation repair; same configuration for both models',
 'source_sha256':{name:hashlib.sha256((BACKEND_ROOT/'src/vibradiag'/name).read_bytes()).hexdigest() for name in ['agents/graph.py','infrastructure/llm/ollama.py']},
 'limitation':'latency includes model loading; no warm/cold performance conclusion from three cases'},'ollama':call('/api/version'),'models':[]}
for model in args.models:
    row={'metadata':metadata(model),'cases':[]};report['models'].append(row)
    for case in CASES:
        print('Starting',model,case['id'],flush=True)
        result=run_agent(analysis,case['description'],case['rpm'],model)
        tools=[t['name'] for t in result.get('trace',[]) if t['stage']=='tool']
        validations=[t for t in result.get('trace',[]) if t['stage']=='draft_validation']
        attempts=validations[0].get('attempts',[]) if validations else []
        record={**case,'tool_route_pass':case['expected_tool'] in tools,
                'first_output_validation_pass':bool(attempts and attempts[0]['passed']),
                'repair_count':max(0,len(attempts)-1),
                'draft_validation_pass':result.get('draft') is not None,'result':result}
        row['cases'].append(record)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(model,case['id'],'tools=',tools,'valid=',record['draft_validation_pass'],'seconds=',round(result['wall_seconds'],2),flush=True)
    row['tool_route_pass_count']=sum(c['tool_route_pass'] for c in row['cases'])
    row['draft_validation_pass_count']=sum(c['draft_validation_pass'] for c in row['cases'])
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(output,flush=True)
