"""Real provider calls against synthetic, authorized method context. Not graph autonomy or model qualification."""
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
import runpy
from uuid import UUID,uuid4
from xml.etree import ElementTree
from sqlalchemy import text
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT,BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine,transaction
from vibradiag.services.analysis_runtime import runtime
from vibradiag.services.identity_service import password_hasher,login,authenticate
from vibradiag.services import agent_task_service as tasks,agent_model_service as models,agent_knowledge_service as knowledge
from vibradiag.services import knowledge_document_service as documents,knowledge_index_service as indexes
from vibradiag.agents.formal_contracts import CreateAgentTask
from vibradiag.schemas.knowledge_documents import KnowledgeMetadata,KnowledgeAction,ReviewGrant
from vibradiag.schemas.knowledge_indexes import IndexBuild


def main():
    test=setting('VIBRADIAG_TEST_DATABASE_URL');migration=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not test or not migration or make_url(test).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:raise RuntimeError('Dedicated test database required')
    os.environ.update(VIBRADIAG_DATABASE_URL=test,VIBRADIAG_MIGRATION_DATABASE_URL=migration,VIBRADIAG_AGENT_WORKER_ENABLED='false')
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    run_id=uuid4().hex;org=uuid4();password=str(uuid4())+str(uuid4());actor=uuid4();admin=uuid4()
    with transaction() as c:
        c.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic model gateway '+run_id))
        for identifier,name,role in ((actor,'author','engineer'),(admin,'admin','admin')):
            c.execute(text('INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,:name,:hash)'),dict(id=identifier,org=org,name=name,hash=password_hasher.hash(password)))
            c.execute(text('INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,:role,:id)'),dict(id=identifier,org=org,role=role))
        token,_=login(c,org,'author',password,uuid4());principal=authenticate(c,token)
        admin_token,_=login(c,org,'admin',password,uuid4());administrator=authenticate(c,admin_token)
    runtime.start()
    try:
        documents.set_review_grant(ReviewGrant(user_id=actor,password=password,enabled=True,expected_version=0,reason='Synthetic explicit review capability'),str(uuid4()),administrator)
        raw=('# Method\nBearing envelope interpretation requires a valid speed source.\n'
             '# Limit\nMissing sampling conditions prevent a definite fault conclusion.\n'+run_id).encode()
        metadata=KnowledgeMetadata(title='Synthetic method context',source_note='Self-authored English compatibility fixture; no expert or field claim',
            equipment_scope=('motor',),component_scope='bearing',content_version='fixture-v1',usage_rights='Developer authored for compatibility testing',
            content_origin='internal_note',visibility='organization',reason='Synthetic model gateway verification')
        revision,_=documents.import_document(raw,'model-fixture.md',metadata,str(uuid4()),principal)
        for op in ('submit','publish'):
            revision,_=documents.transition(UUID(revision['id']),op,KnowledgeAction(expected_version=revision['row_version'],expected_document_version=revision['document_version'],reason='Synthetic '+op),str(uuid4()),principal)
        build,_=indexes.build(UUID(revision['id']),IndexBuild(expected_version=revision['row_version'],expected_document_version=revision['document_version'],expected_index_version=0,reason='Synthetic source embedding'),str(uuid4()),principal)
        assert build['status']=='ready'
        outcomes=[]
        for provider in ('deepseek','ollama'):
            task,_=tasks.create_task(CreateAgentTask(goal='explain_method',description='What condition does bearing envelope interpretation require?',knowledge_scope=dict(equipment='motor',component='bearing')),str(uuid4()),principal)
            identifier=UUID(task['id']);claimed=tasks.claim_next(identifier);token=claimed['lease_token'];phases=[]
            try:
                try:
                    decision=models.run_round(identifier,token,principal,'decision',provider=provider)
                    replay=models.run_round(identifier,token,principal,'decision',provider=provider)
                    assert replay['reused'] and replay['id']==decision['id']
                    phases.append(dict(phase='decision',passed=True,output=decision['output'],trace=decision['trace'],replay_reused=True))
                except Exception as error:phases.append(dict(phase='decision',passed=False,error_code=getattr(error,'code',type(error).__name__)))
                # Independently prepare an authorized retrieval; the decision is not executed by this preflight.
                payload=knowledge.prepare(identifier,token,principal)
                tasks._dispatch(identifier,token,'search_published_knowledge',payload.model_dump(mode='json'),principal)
                try:
                    draft=models.run_round(identifier,token,principal,'extractive_draft',provider=provider)
                    phases.append(dict(phase='extractive_draft',passed=True,output=draft['output'],trace=draft['trace'],published_draft=draft['published_draft']))
                except Exception as error:phases.append(dict(phase='extractive_draft',passed=False,error_code=getattr(error,'code',type(error).__name__)))
                state=tasks.read_task(identifier,principal)
                assert state['draft'] is None and all(not c['action'].startswith('write') for c in state['calls'])
                with transaction() as c:
                    rounds=c.execute(text('SELECT state,phase,context_sha256,configuration_sha256 FROM agent_model_rounds WHERE task_id=:id ORDER BY round_no'),dict(id=identifier)).mappings().all()
                outcomes.append(dict(provider=provider,phases=phases,model_rounds=[dict(r) for r in rounds],budget=state['budget'],task_draft_published=False))
                print(json.dumps(dict(provider=provider,phases=[dict(phase=p['phase'],passed=p['passed'],error_code=p.get('error_code')) for p in phases])),flush=True)
            finally:
                with transaction() as c:
                    row=c.execute(text('SELECT * FROM agent_tasks WHERE id=:id'),dict(id=identifier)).mappings().one()
                    if row['status'] not in tasks.TERMINAL:tasks._change(c,row,'cancelled',error='PREFLIGHT_FINISHED')
        full=PROJECT_ROOT/'experiments/m3-t04a-tests.xml';focused=PROJECT_ROOT/'experiments/m3-t04a-model-tests.xml';boundary=PROJECT_ROOT/'experiments/m3-t04a-boundary-tests.xml'
        suites=[ElementTree.parse(path).getroot().find('testsuite') for path in (full,focused,boundary)]
        assert all(int(s.attrib[k])==0 for s in suites for k in ('failures','errors','skipped'))
        cloud=all(p['passed'] for p in outcomes[0]['phases']);local=all(p['passed'] for p in outcomes[1]['phases'])
        def fixture_passed(outcome):
            return (all(p['passed'] for p in outcome['phases']) and outcome['phases'][0]['output'].get('action')=='choose_action'
                and outcome['phases'][1]['output'].get('outcome')=='cited_context' and bool(outcome['phases'][1]['output'].get('selections')))
        cloud_fixture=fixture_passed(outcomes[0]);local_fixture=fixture_passed(outcomes[1])
        paths=[p for directory in ('backend/src','backend/tests','backend/scripts','backend/migrations','backend/config') for p in (PROJECT_ROOT/directory).rglob('*') if p.suffix in {'.py','.json'} and '__pycache__' not in p.parts]
        paths += [full,focused,boundary]+[PROJECT_ROOT/p for p in ('backend/requirements.lock.txt','backend/.env.example','README.md','backend/README.md','docs/README.md','docs/03-milestones.md',
            'docs/07-m3-agent-design.md','docs/acceptance/M3-T04a-verification.md')]
        report=dict(passed=cloud and cloud_fixture,recorded_at=datetime.now(timezone.utc).isoformat(),run_id=run_id,scope='T04a bounded model gateway and round preflight only',
            cloud_compatibility_passed=cloud,local_compatibility_passed=local,cloud_fixture_passed=cloud_fixture,local_fixture_passed=local_fixture,
            providers=outcomes,source_sha256=sha256(raw).hexdigest(),
            full_backend_tests={k:suites[0].attrib[k] for k in ('tests','failures','errors','skipped','time')},model_tests={k:suites[1].attrib[k] for k in ('tests','failures','errors','skipped','time')},
            boundary_tests={k:suites[2].attrib[k] for k in ('tests','failures','errors','skipped','time')},
            production_graph_model_driven=False,model_quality_qualified=False,generated_diagnosis=False,automatic_provider_fallback=False,
            limitations=['Two synthetic English compatibility phases per provider are not a held-out model ranking or industrial quality qualification',
                'Decisions are validated but not executed; quotes are exact extracts, not generated factual explanations or engineering approval',
                'Local failure is retained without fallback; cloud failure prevents this preflight from passing',
                'A completed persisted round replays locally; failed/unknown rounds are not freely retried in T04a',
                'Cloud weights cannot be cryptographically pinned; requested and served model identities and token usage are recorded'],
            source_fingerprints={p.relative_to(PROJECT_ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})
        (PROJECT_ROOT/'experiments/m3-t04a-models.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
        print(json.dumps(dict(passed=cloud and cloud_fixture,cloud_compatibility_passed=cloud,local_compatibility_passed=local,cloud_fixture_passed=cloud_fixture,local_fixture_passed=local_fixture)))
        if not cloud or not cloud_fixture:raise RuntimeError('Cloud compatibility/fixture preflight did not pass; see redacted receipt')
    finally:runtime.stop()


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit(f'Formal model verification failed ({type(error).__name__}); credentials hidden') from None
