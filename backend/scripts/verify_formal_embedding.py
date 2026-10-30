"""Real local BGE-M3 compatibility check and read-only DB preflight, not RAG evaluation."""
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import platform
from uuid import uuid4
from xml.etree import ElementTree

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from vibradiag.core.config import setting
from vibradiag.core.paths import BACKEND_ROOT, PROJECT_ROOT
from vibradiag.domain.knowledge_text import parse_knowledge
from vibradiag.infrastructure.persistence.database import database_url
from vibradiag.infrastructure.retrieval.embedding import EmbeddingIdentity, LocalEmbedding
from vibradiag.infrastructure.retrieval.vector_readiness import inspect_vector


def main():
    identity=EmbeddingIdentity(**json.loads((BACKEND_ROOT/'config/embedding-bge-m3.json').read_text(encoding='utf8')))
    service=LocalEmbedding(identity)
    metadata=service.inspect()
    raw=('# 开发验证方法\r\n轴承包络谱需要结合转速、几何参数和采集条件解释。\r\n'
         '## 开发验证边界\r\n振动信号无法单独证明润滑状况，缺少条件时应拒绝确定性定类。\r\n').encode()
    parsed=parse_knowledge(raw,'embedding-fixture.md')
    query='缺少转速时，轴承诊断需要哪些条件？'
    # These are developer-authored compatibility inputs, with no retrieval-quality labels.
    inputs=[c.text for c in parsed.chunks]+[query]
    vectors,timing=service.encode(inputs)
    repeat,repeat_timing=service.encode([query])
    left,right=vectors[-1].astype(np.float64),repeat[0].astype(np.float64)
    similarity=float(np.clip(np.dot(left,right)/(np.linalg.norm(left)*np.linalg.norm(right)),-1,1))
    assert vectors.shape==(3,1024) and np.isfinite(vectors).all()
    assert np.allclose(np.linalg.norm(vectors,axis=1),1,atol=1e-5)
    assert similarity>=0.9999
    # A real wrong digest must be rejected, without an embedding request or automatic pull.
    wrong=EmbeddingIdentity(**{**asdict(identity),'digest':'0'*64})
    from vibradiag.domain.measurement_errors import MeasurementError
    try:LocalEmbedding(wrong).encode([query])
    except MeasurementError as error:assert error.code=='EMBEDDING_IDENTITY_MISMATCH'
    else:raise AssertionError('Wrong model pin accepted')
    after=service.inspect();assert after==metadata
    db={}
    urls={'development_application':database_url(),'development_migration':database_url(migration=True)}
    for label,key in [('test_application','VIBRADIAG_TEST_DATABASE_URL'),('test_migration','VIBRADIAG_TEST_MIGRATION_DATABASE_URL')]:
        value=setting(key)
        if not value:raise RuntimeError('Dedicated test database configuration required')
        urls[label]=make_url(value)
    assert urls['test_application'].database!=urls['development_application'].database
    assert urls['test_migration'].database==urls['test_application'].database
    for label,url in urls.items():
        dbengine=create_engine(url,hide_parameters=True,connect_args={'connect_timeout':5})
        try:
            with dbengine.connect() as connection:
                connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                db[label]=inspect_vector(connection)
                assert not db[label]['superuser'] and not db[label]['create_database'] and not db[label]['create_role']
                if label.endswith('application'):assert not db[label]['public_ddl']
        finally:dbengine.dispose()
    run_id=uuid4().hex;private=PROJECT_ROOT/'.runtime/formal-embedding'/run_id;private.mkdir(parents=True)
    np.savez_compressed(private/'vectors.npz',vectors=vectors,repeat=repeat)
    focused=PROJECT_ROOT/'experiments/m3-t03b1-embedding-tests.xml'
    suite=ElementTree.parse(focused).getroot().find('testsuite')
    assert all(int(suite.attrib[k])==0 for k in ('failures','errors','skipped'))
    full=PROJECT_ROOT/'experiments/m3-t03b1-tests.xml'
    regression=ElementTree.parse(full).getroot().find('testsuite')
    assert all(int(regression.attrib[k])==0 for k in ('failures','errors','skipped'))
    assert len([c for c in regression.findall('testcase') if c.attrib['classname'].endswith('test_formal_embedding')])==43
    paths=[BACKEND_ROOT/p for p in ('src/vibradiag/infrastructure/retrieval/embedding.py',
        'src/vibradiag/infrastructure/retrieval/vector_readiness.py','src/vibradiag/domain/knowledge_text.py',
        'scripts/verify_formal_embedding.py','tests/unit/test_formal_embedding.py','tests/integration/test_vector_readiness.py',
        'config/embedding-bge-m3.json','requirements.lock.txt')]
    paths += [focused,full]+[PROJECT_ROOT/p for p in ('README.md','backend/README.md','docs/README.md',
        'docs/03-milestones.md','docs/08-knowledge-design.md','docs/acceptance/M3-T03b1-verification.md')]
    report=dict(passed=True,scope='M3-T03b1 pinned dense embedding adapter and read-only database preflight',
        run_id=run_id,recorded_at=datetime.now(timezone.utc).isoformat(),python=platform.python_version(),platform=platform.platform(),
        provider=metadata,embedding=dict(real_model_called=True,batches=2,source_chunk_count=len(parsed.chunks),query_count=1,
            source_sha256=sha256(raw).hexdigest(),normalized_text_sha256=parsed.text_sha256,
            input_sha256=[sha256(t.encode()).hexdigest() for t in inputs],vector_sha256=sha256(vectors.astype('<f4').tobytes()).hexdigest(),
            vector_dtype='float32',vector_shape=list(vectors.shape),finite=True,l2_normalized=True,
            repeat_query_cosine=similarity,timing=timing,repeat_timing=repeat_timing,
            incorrect_pin_rejected=True,identity_stable_before_after=True),
        database_preflight=db,vector_index_integrated=False,agent_knowledge_integrated=False,
        database_modified=False,embedding_called_from_formal_api=False,
        tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},
        full_backend_tests={k:regression.attrib[k] for k in ('tests','failures','errors','skipped','time')},
        limitations=['Synthetic developer text; no engineering or retrieval quality qualification',
            'No PostgreSQL vector extension installation, schema/index mutation or privilege escalation',
            'No persisted formal index, scoped semantic search, Agent citation or frontend integration',
            'Tag checks detect observed drift, not cryptographic attestation of a trusted local Ollama server',
            'HTTP timeouts and stream deadline checks do not terminate server-side inference; single process slot only',
            'CPU was requested through Ollama; no independent hardware execution attestation'],
        source_sha256={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in paths})
    destination=PROJECT_ROOT/'experiments/m3-t03b1-embedding.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({'passed':True,'model':identity.model,'dimensions':identity.dimensions,
        'real_embedding_batches':2,'repeat_query_cosine':similarity,
        'database_vector_installed':{k:bool(v['installed_version']) for k,v in db.items()},
        'vector_index_integrated':False,'tests':report['tests']}))


if __name__=='__main__':
    try:main()
    except Exception as error:
        raise SystemExit(f'Embedding verification failed ({type(error).__name__}); credentials and input hidden') from None
