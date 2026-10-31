"""Frozen feedback-conditioned development comparison. Requires dedicated test DB and explicit paid-model opt-in."""
import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
import runpy
from xml.etree import ElementTree
from uuid import uuid4
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT,BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine
from vibradiag.evaluation.scoped_regression import run,PROTOCOL
from vibradiag.infrastructure.llm.formal_gateway import configuration
from dataclasses import asdict


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--allow-paid-model-calls',action='store_true');args=parser.parse_args()
    if not args.allow_paid_model_calls:raise RuntimeError('Explicit benchmark paid-model opt-in required')
    app=setting('VIBRADIAG_TEST_DATABASE_URL');migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not app or not migration or make_url(app).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:raise RuntimeError('Dedicated test database required')
    output=PROJECT_ROOT/'experiments/m3-t05c1-scoped-regression.json'
    if output.exists():raise RuntimeError('Preserve existing public receipt; use a new protocol/version for a new public run')
    for name in ('m3-t05c1-scope-tests.xml','m3-t05c1-tests.xml'):
        suite=ElementTree.parse(PROJECT_ROOT/'experiments'/name).getroot().find('testsuite')
        if any(int(suite.attrib[k]) for k in ('failures','errors','skipped')):raise RuntimeError('Passing backend verification receipts required')
    identifier=uuid4().hex;private=PROJECT_ROOT/'.runtime/agent-scoped-regression'/identifier;private.mkdir(parents=True)
    os.environ.update(VIBRADIAG_DATABASE_URL=app,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_COOKIE_SECURE='false',
        VIBRADIAG_ENABLE_PROTOTYPE='false',VIBRADIAG_AGENT_WORKER_ENABLED='false',VIBRADIAG_NUMERIC_EXECUTOR='rules',
        VIBRADIAG_NUMERIC_BACKEND='process',VIBRADIAG_FORMAL_LLM_PROVIDER='deepseek',VIBRADIAG_DATA_ROOT=str(private/'data'))
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    paths=[p for directory in ('backend/src','backend/tests','backend/scripts','backend/migrations','backend/config') for p in (PROJECT_ROOT/directory).rglob('*') if p.suffix in {'.py','.json'} and '__pycache__' not in p.parts]
    paths += [PROJECT_ROOT/p for p in ('README.md','backend/README.md','backend/.env.example','backend/requirements.lock.txt','docs/README.md','docs/03-milestones.md','docs/07-m3-agent-design.md','docs/10-agent-evaluation-design.md','docs/acceptance/M3-T05c1-verification.md','experiments/m3-t05c1-scope-tests.xml','experiments/m3-t05c1-tests.xml','experiments/m3-t05a-development-comparison.json','experiments/m3-t05b-feedback-comparison.json','docs/contracts/m3-agent-contract.json')]
    fingerprints={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    manifest=dict(frozen_at=datetime.now(timezone.utc).isoformat(),protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),configuration=asdict(configuration('deepseek',purpose='numeric_v2')),source_fingerprints=fingerprints)
    encoded=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode();(private/'frozen-manifest.json').write_bytes(encoded)
    result=run(private,lambda item:print(json.dumps(item),flush=True))
    for name,expected in fingerprints.items():
        if sha256((PROJECT_ROOT/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('Frozen source changed during benchmark')
    result.update(run_completed=True,recorded_at=datetime.now(timezone.utc).isoformat(),run_id=identifier,pre_run_manifest_sha256=sha256(encoded).hexdigest(),
        frozen_at=manifest['frozen_at'],source_fingerprints=fingerprints)
    output=PROJECT_ROOT/'experiments/m3-t05c1-scoped-regression.json'
    if output.exists():raise RuntimeError('Preserve existing public receipt; do not overwrite an observed model result')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(dict(run_completed=True,all_cases_met_expectations=result['all_cases_met_expectations'],summary=result['summary'])),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(f'Formal development comparison stopped ({type(error).__name__}); inspect private receipts; credentials hidden') from None
