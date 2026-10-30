"""T02b1 isolated Postgres graph/process probe, with no production task integration."""
import argparse
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from time import monotonic,sleep
from uuid import uuid4
from xml.etree import ElementTree
from psycopg import sql
from vibradiag.core.paths import PROJECT_ROOT
import m3_checkpoint_probe as probe


def write_json(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    temporary.replace(path)


def child(phase,job_path):
    job_path=Path(job_path).resolve()
    expected=(PROJECT_ROOT/'.runtime/m3-checkpoint-probe').resolve()
    if not job_path.is_relative_to(expected) or job_path.name!='job.json':raise ValueError('Private probe job required')
    data=json.loads(job_path.read_text(encoding='utf8'))
    if set(data)!={'schema','thread_id'}:raise ValueError('Unexpected probe job fields')
    write_json(job_path.parent/f'{phase}-pid.json',{'pid':os.getpid()})
    def blocked():
        write_json(job_path.parent/'hang-ready.json',{'pid':os.getpid(),'after_resume_before_node_commit':True})
        while True:sleep(.1)
    result=probe.run_phase(data['schema'],data['thread_id'],'resume' if phase=='hang' else phase,
        blocked=blocked if phase=='hang' else None)
    write_json(job_path.parent/f'{phase}-result.json',dict(result,pid=os.getpid()))


def terminate_owned(process,actual_pid):
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(actual_pid),'/F'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    else:os.kill(actual_pid,9)
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def main(receipt_path,tests_report):
    probe.test_urls(require_separate=True)
    run_id=uuid4();name=probe.schema_name(run_id);thread=str(uuid4())
    private=PROJECT_ROOT/'.runtime/m3-checkpoint-probe'/run_id.hex;private.mkdir(parents=True)
    job=private/'job.json';write_json(job,{'schema':name,'thread_id':thread})
    probe.install_schema(name);cleaned=False;process=None;actual_pid=None
    def launch(phase):
        log=(private/f'{phase}.log').open('w',encoding='utf8')
        try:
            result=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',phase,'--job',str(job)],
                cwd=PROJECT_ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        finally:log.close()
        return result
    try:
        process=launch('pause');process.wait(timeout=30)
        if process.returncode:raise RuntimeError('Pause child failed')
        paused=json.loads((private/'pause-result.json').read_text(encoding='utf8'))
        assert paused['paused'] and paused['counts']==dict(node_entries=1,compute_count=1)
        process=launch('hang');ready=private/'hang-ready.json';deadline=monotonic()+30
        while not ready.exists():
            if process.poll() is not None or monotonic()>deadline:raise RuntimeError('Blocked child did not become ready')
            sleep(.02)
        actual_pid=json.loads(ready.read_text(encoding='utf8'))['pid']
        timed_out=False;started=monotonic()
        try:process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            timed_out=True;terminate_owned(process,actual_pid)
        assert timed_out and process.poll() is not None
        termination_seconds=monotonic()-started;actual_pid=None
        process=launch('recover');process.wait(timeout=30)
        if process.returncode:raise RuntimeError('Recovery child failed')
        recovered=json.loads((private/'recover-result.json').read_text(encoding='utf8'))
        assert recovered['pid']!=paused['pid'] and recovered['finished'] and recovered['confirmed']
        assert recovered['counts']==dict(node_entries=3,compute_count=1)
        assert abs(recovered['result']['rms']-2**.5)<1e-12
        with probe.connect(name) as connection:
            checkpoints=connection.execute('SELECT count(*) AS count FROM checkpoints WHERE thread_id=%s',(thread,)).fetchone()['count']
            runtime_versions=connection.execute('SELECT v FROM checkpoint_migrations ORDER BY v').fetchall()
            ddl_denied=not connection.execute("SELECT has_schema_privilege(current_user,%s,'CREATE') AS allowed",(name,)).fetchone()['allowed']
            stored_types=[r['type'] for r in connection.execute('SELECT DISTINCT type FROM checkpoint_writes').fetchall()]
            server=connection.execute('SHOW server_version').fetchone()['server_version']
        assert checkpoints>=3 and ddl_denied
        probe.drop_schema(name);cleaned=True
        with probe.connect(name,migration=True) as connection:
            assert connection.execute('SELECT oid FROM pg_namespace WHERE nspname=%s',(name,)).fetchone() is None
            public_tables=connection.execute("SELECT count(*) AS count FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'").fetchone()['count']
            head=connection.execute('SELECT version_num FROM public.alembic_version').fetchone()['version_num']
        suite=ElementTree.parse(PROJECT_ROOT/tests_report).getroot().find('testsuite')
        assert all(int(suite.attrib[k])==0 for k in ('failures','errors','skipped'))
        focused=[case for case in suite.findall('testcase') if case.attrib['classname'].endswith('test_checkpoint_probe')]
        assert len(focused)==17
        names=['backend/scripts/m3_checkpoint_probe.py','backend/scripts/verify_m3_checkpoints.py',
            'backend/tests/integration/test_checkpoint_probe.py','backend/requirements.lock.txt','backend/pyproject.toml',tests_report,
            'docs/07-m3-agent-design.md','docs/acceptance/M3-T02b1-verification.md','docs/README.md','docs/03-milestones.md',
            'README.md','backend/README.md']
        vendor_plan=json.dumps(probe.PostgresSaver.MIGRATIONS,separators=(',',':')).encode()
        report=dict(passed=True,scope='M3-T02b1 isolated compatibility probe only',run_id=run_id.hex,
            versions={p:version(p) for p in ('langgraph','langgraph-checkpoint','langgraph-checkpoint-postgres','psycopg','psycopg-pool')},
            environment=dict(python=platform.python_version(),platform=platform.platform(),postgresql=server),
            business_migration_head=head,business_tables=public_tables,production_task_graph_integrated=False,
            production_hard_cancel_implemented=False,model_called=False,factory_data_used=False,
            checkpoint_tables=4,isolated_probe_ledger_tables=1,probe_schema_removed=cleaned,
            vendor_migrations_sha256=sha256(vendor_plan).hexdigest(),migration_versions=[r['v'] for r in runtime_versions],
            migration_transactional_on_empty_tables=True,application_ddl_denied=ddl_denied,checkpoint_count=checkpoints,
            serializer=dict(restricted_msgpack=True,pickle_fallback=False,stored_types=stored_types,
                blocked_constructor_returns_raw_data=True,semantic_integrity_validation=False),
            process_recovery=dict(separate_processes=True,interrupted_node_reentered=True,counts=recovered['counts'],
                result=recovered['result'],timeout_seconds=.5,timeout_excludes_child_startup=True,
                actual_timeout_and_termination_seconds=termination_seconds,owned_child_terminated=True),
            full_backend_tests={k:suite.attrib[k] for k in ('tests','failures','errors','skipped','time')},new_probe_tests=len(focused),
            limitations=['Probe schemas only; no production checkpointer setup or API graph execution',
                'No tenant/actor/lease fencing in raw PostgresSaver; must add controlled task binding before integration',
                'Process termination demonstrated only for a trusted probe child, not M1/M2 API numerical calls',
                'Cross-process shared capacity, delegated identity and task-cancel reconciliation remain T02b2+',
                'Strict serializer blocks constructor revival but can return raw data; persisted business state still needs validation'],
            sources={'postgres_saver':'https://pypi.org/project/langgraph-checkpoint-postgres/',
                'interrupts':'https://docs.langchain.com/oss/python/langgraph/interrupts',
                'persistence':'https://docs.langchain.com/oss/python/langgraph/persistence'},
            source_sha256={p:sha256((PROJECT_ROOT/p).read_bytes()).hexdigest() for p in names})
        write_json(PROJECT_ROOT/receipt_path,report)
        print(json.dumps({k:report[k] for k in ('passed','new_probe_tests','full_backend_tests','business_tables','production_task_graph_integrated')}))
    finally:
        if process is not None and process.poll() is None:
            # Every process here is our own child; /T handles the Windows Python launcher.
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            else:process.kill()
            process.wait(timeout=5)
        if not cleaned:probe.drop_schema(name)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child',choices=('pause','hang','recover'))
    parser.add_argument('--job')
    parser.add_argument('--receipt',default='experiments/m3-t02b1-checkpoints.json')
    parser.add_argument('--tests-report',default='experiments/m3-t02b1-tests.xml')
    args=parser.parse_args()
    try:
        if args.child:child(args.child,args.job)
        else:main(args.receipt,args.tests_report)
    except Exception as error:
        raise SystemExit(f'Checkpoint probe failed ({type(error).__name__}); inspect private probe logs; credentials hidden') from None
