"""Development-only selection benchmark; not held-out industrial validation."""
import argparse
import hashlib
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

from vibradiag.core.paths import PROJECT_ROOT as ROOT
from vibradiag.infrastructure.retrieval.vector_index import KnowledgeIndex
from vibradiag.infrastructure.llm.ollama import call

parser=argparse.ArgumentParser()
parser.add_argument('--models', nargs='+', default=['bge-m3:567m','qwen3-embedding:0.6b'])
args=parser.parse_args()
dataset_path=ROOT/'data/evaluation/retrieval-dev.json'
dataset=json.loads(dataset_path.read_text(encoding='utf-8'))
report={'created_at':datetime.now(timezone.utc).isoformat(), 'purpose':'development smoke comparison, authored corpus and queries; NOT held-out or factory validation',
        'dataset_sha256':hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        'environment':{'python':platform.python_version(),'platform':platform.platform(),'ollama':call('/api/version')},
        'protocol':{'embedding_device':'CPU requested via num_gpu=0','threads':8,'top_k':5,'query_batch_size':1,
                    'repetitions':1,'warmup_queries':1,'order':args.models,'prefix':'Qwen uses official-style task instruction; BGE-M3 uses raw query'},'models':[]}
destination=ROOT/'experiments/retrieval-dev-results.json'
destination.parent.mkdir(exist_ok=True)
for model in args.models:
    print('Starting',model,flush=True)
    started=time.perf_counter()
    try:
        index=KnowledgeIndex(model,cache=False)
        index_seconds=time.perf_counter()-started
        index.search('振动数据的采样率与单位')
        rows=[]
        for item in dataset:
            hits,timing=index.search(item['query'])
            ids=[hit['id'] for hit in hits]
            rank=ids.index(item['expected'])+1 if item['expected'] in ids else None
            rows.append({**item,'ranking':ids,'rank':rank,'top_score':hits[0]['score'],**timing})
        durations=[row['wall_seconds'] for row in rows]
        outcome={'metadata':index.metadata,'corpus_sha256':index.corpus_sha256,'dimension':index.vectors.shape[1],
            'index_wall_seconds':index_seconds,'embedding_index_timing':index.index_timing,
            'top1_accuracy':sum(row['rank']==1 for row in rows)/len(rows),
            'recall_at_5':sum(row['rank'] is not None for row in rows)/len(rows),
            'mrr_at_5':sum(1/row['rank'] if row['rank'] else 0 for row in rows)/len(rows),
            'warm_query_wall_p50_seconds':float(np.percentile(durations,50)),
            'warm_query_wall_p95_seconds':float(np.percentile(durations,95)), 'queries':rows}
        report['models'].append(outcome)
        print(model,'Top1=',outcome['top1_accuracy'],'R@5=',outcome['recall_at_5'],'P95=',outcome['warm_query_wall_p95_seconds'],flush=True)
    except Exception as exc:
        report['models'].append({'model':model,'error':str(exc),'elapsed_seconds':time.perf_counter()-started})
        print('FAILED',model,type(exc).__name__,flush=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(destination,flush=True)
