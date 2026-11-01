"""A single frozen explicit-field method-v2 regression with explicit paid-model opt-in."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
import runpy
from uuid import uuid4
from xml.etree import ElementTree
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT,BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine
from vibradiag.infrastructure.llm.formal_gateway import configuration
from vibradiag.infrastructure.retrieval.embedding import LocalEmbedding,configured_identity
from vibradiag.evaluation.method_v2_regression import run,PROTOCOL


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--allow-paid-model-calls',action='store_true');args=parser.parse_args()
    if not args.allow_paid_model_calls:raise RuntimeError('Explicit paid-model opt-in required')
    app=setting('VIBRADIAG_TEST_DATABASE_URL');migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not app or not migration or make_url(app).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:raise RuntimeError('Dedicated test database required')
    target=make_url(app);migrator=make_url(migration)
    if (target.host,target.port,target.database)!=(migrator.host,migrator.port,migrator.database):raise RuntimeError('Matching test DB targets required')
    output=PROJECT_ROOT/'experiments/m3-t05c3b-method-regression.json'
    if output.exists():raise RuntimeError('Preserve existing observation')
    verification=['experiments/m3-t05c3b-method-tests.xml','experiments/m3-t05c3b-tests.xml']
    for name in verification:
        suites=list(ElementTree.parse(PROJECT_ROOT/name).getroot().iter('testsuite'))
        if not suites or sum(int(s.attrib['tests']) for s in suites)==0 or any(int(s.attrib.get(k,0)) for s in suites for k in ('failures','errors','skipped')):raise RuntimeError('Passing verification required')
    identifier=uuid4().hex;private=PROJECT_ROOT/'.runtime/method-v2-regression'/identifier;private.mkdir(parents=True)
    os.environ.update(VIBRADIAG_DATABASE_URL=app,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_COOKIE_SECURE='false',VIBRADIAG_ENABLE_PROTOTYPE='false',
        VIBRADIAG_AGENT_WORKER_ENABLED='false',VIBRADIAG_NUMERIC_EXECUTOR='rules',VIBRADIAG_METHOD_EXECUTOR='model_v2',VIBRADIAG_NUMERIC_BACKEND='process',VIBRADIAG_FORMAL_LLM_PROVIDER='deepseek',VIBRADIAG_DATA_ROOT=str(private/'data'))
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    paths=[p for directory in ('backend/src','backend/tests','backend/scripts','backend/migrations','backend/config') for p in (PROJECT_ROOT/directory).rglob('*') if p.suffix in {'.py','.json'} and '__pycache__' not in p.parts]
    paths += [PROJECT_ROOT/p for p in verification+['backend/requirements.lock.txt','backend/.env.example','docs/contracts/m3-agent-contract.json','experiments/m3-t05c2b-method-comparison.json','experiments/m3-t05c2b-outcome-review.json','experiments/m3-t05c3a-method-diagnostics.json']]
    fingerprints={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    manifest=dict(frozen_at=datetime.now(timezone.utc).isoformat(),configuration=asdict(configuration('deepseek',purpose='method_v2')),protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),embedding_identity=asdict(configured_identity()),source_fingerprints=fingerprints)
    encoded=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode();(private/'frozen-manifest.json').write_bytes(encoded)
    local=LocalEmbedding(configured_identity());inspection=local.inspect();_,warmup=local.encode(['Developer diagnostic observation warmup.'])
    print(json.dumps(dict(preflight='pinned_embedding_ready',ollama_version=inspection['ollama_version'])),flush=True)
    result=run(private,lambda value:print(json.dumps(value),flush=True))
    for name,expected in fingerprints.items():
        if sha256((PROJECT_ROOT/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('Frozen source changed during observation')
    result.update(run_completed=True,recorded_at=datetime.now(timezone.utc).isoformat(),run_id=identifier,frozen_at=manifest['frozen_at'],
                  pre_run_manifest_sha256=sha256(encoded).hexdigest(),source_fingerprints=fingerprints,embedding_preflight=inspection,embedding_warmup=warmup)
    with output.open('x',encoding='utf8',newline='\n') as stream:stream.write(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps(dict(run_completed=True,all_cases_met_expectations=result['all_cases_met_expectations'],schema_failures=result['diagnostic_schema_failures'],quality_summary=result['summary'])),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(f'Method-v2 regression stopped ({type(error).__name__}); inspect private receipts; credentials hidden') from None
