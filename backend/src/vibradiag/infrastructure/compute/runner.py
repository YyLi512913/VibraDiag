"""Host-side numerical supervisor; one API capacity gate owns all child admissions."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass,field
import os
from pathlib import Path
import re
import shutil
import sys
from threading import Event,Lock,Thread
from time import monotonic,sleep
from uuid import uuid4
from sqlalchemy.engine import make_url
from vibradiag.core.config import setting
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.compute import codec
from vibradiag.infrastructure.compute.process import OwnedProcess
from vibradiag.infrastructure.persistence.file_store import FileStore

task_scope=ContextVar('numeric_task_scope',default=None)
last_execution=ContextVar('numeric_last_execution',default=None)
host_check=ContextVar('numeric_host_check',default=None)
_guard=Lock();_jobs={}


@dataclass
class Job:
    id: str
    directory: Path
    task_id: object
    process: object=None
    cancelled: Event=field(default_factory=Event)
    stopped: Event=field(default_factory=Event)


def _test_allowed():
    current=setting('VIBRADIAG_DATABASE_URL');test=setting('VIBRADIAG_TEST_DATABASE_URL')
    return bool(current and test and make_url(current)==make_url(test))


def fail(code,status=409):raise MeasurementError(code,'Numerical execution unavailable; inspect the stored run status',status=status)


@contextmanager
def bound_task(identifier,token,principal):
    from vibradiag.services import agent_task_service as service
    from vibradiag.infrastructure.persistence.database import transaction
    with transaction() as connection:
        row=service._running(connection,identifier,token)
        remaining=120000-service._active_budget(row).active_ms
    def check():
        with transaction() as connection:
            service._running(connection,identifier,token)
            service._authorize(connection,principal,identifier,write=True,current_only=True)
    flag=task_scope.set((identifier,monotonic()+remaining/1000,check))
    try:yield
    finally:task_scope.reset(flag)


def stop_task(identifier,timeout=5):
    with _guard:jobs=[j for j in _jobs.values() if j.task_id==identifier]
    for job in jobs:job.cancelled.set()
    deadline=monotonic()+timeout
    for job in jobs:
        if not job.stopped.wait(max(0,deadline-monotonic())):fail('NUMERIC_STOP_PENDING',503)


def stop_all():
    with _guard:jobs=list(_jobs.values())
    for job in jobs:job.cancelled.set()
    for job in jobs:job.stopped.wait(5)


def active_jobs():
    """Trusted local inspection only; never exposed as a public API."""
    with _guard:return list(_jobs.values())


def _environment():
    result={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP'}}
    if os.name=='nt':result['PATH']=str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32')
    result.update(OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    return result


def execution_receipt():return last_execution.get()


@contextmanager
def supervising(check):
    flag=host_check.set(check)
    try:yield
    finally:host_check.reset(flag)


def run(action,payload,inline,*,check=None):
    if action not in {'basic','envelope','frequency','matching','comparison'}:fail('NUMERIC_ACTION_INVALID')
    last_execution.set(None)
    mode=setting('VIBRADIAG_NUMERIC_BACKEND','process')
    if mode=='inline_test':
        if not _test_allowed():fail('NUMERIC_BACKEND_INVALID',503)
        result=inline();last_execution.set(dict(backend='inline_test',protocol=codec.PROTOCOL));return result
    if mode!='process':fail('NUMERIC_BACKEND_INVALID',503)
    probe=setting('VIBRADIAG_NUMERIC_TEST_SCENARIO') or None
    if probe and (probe not in {'hang','crash','bad_result'} or not _test_allowed()):fail('NUMERIC_TEST_MODE_FORBIDDEN',503)
    try:timeout_ms=int(setting('VIBRADIAG_NUMERIC_TIMEOUT_MS','120000'))
    except ValueError:fail('NUMERIC_TIMEOUT_INVALID',503)
    if not 100<=timeout_ms<=120000:fail('NUMERIC_TIMEOUT_INVALID',503)
    started=monotonic();scope=task_scope.get();deadline=min(started+timeout_ms/1000,scope[1]) if scope else started+timeout_ms/1000
    parent=FileStore().root/'compute';parent.mkdir(parents=True,exist_ok=True)
    identifier=str(uuid4());directory=parent/identifier;directory.mkdir()
    job=Job(identifier,directory,scope[0] if scope else None)
    monitor_stop=Event();monitor=None
    def guard():
        if job.cancelled.is_set():fail('NUMERIC_CANCELLED')
        if monotonic()>=deadline:fail('NUMERIC_DEADLINE_EXCEEDED')
        if scope:
            from vibradiag.services.agent_task_service import Fenced
            try:scope[2]()
            except Fenced:fail('NUMERIC_SCOPE_ENDED')
        if check:check()
        extra=host_check.get()
        if extra:extra()
    with _guard:_jobs[identifier]=job
    try:
        guard()
        codec.write(directory/'request.json',dict(protocol=codec.PROTOCOL,nonce=identifier,action=action,
            payload=codec.encode(payload,directory,'input'),probe=probe))
        guard()
        try:
            job.process=OwnedProcess([sys.executable,'-I','-m','vibradiag.infrastructure.compute.child',str(directory),str(os.getpid())],_environment(),directory)
        except OSError:fail('NUMERIC_START_FAILED',503)
        def watchdog():
            # No DB/file/authorization operations here: a slow host guard cannot extend child lifetime.
            while not monitor_stop.wait(.025):
                if job.cancelled.is_set() or monotonic()>=deadline:
                    job.process.kill();return
        monitor=Thread(target=watchdog,name='numeric-deadline',daemon=True);monitor.start()
        next_check=0
        while job.process.poll() is None:
            if job.cancelled.is_set():fail('NUMERIC_CANCELLED')
            if monotonic()>=deadline:fail('NUMERIC_DEADLINE_EXCEEDED')
            if monotonic()>=next_check:guard();next_check=monotonic()+.2
            sleep(.025)
        guard()
        if job.process.poll()!=0:fail('NUMERIC_CHILD_CRASHED')
        try:
            response=codec.read(directory/'response.json')
            if response['protocol']!=codec.PROTOCOL or response['nonce']!=identifier or response['action']!=action:raise ValueError()
            if response['status']=='error':
                code=response['code']
                if not isinstance(code,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',code):raise ValueError()
                fail(code)
            if response['status']!='ok':raise ValueError()
            result=codec.decode(response['payload'],directory,'output')
            from vibradiag.analysis.basic import BasicResult,CONFIG
            from vibradiag.domain.tool_results import NumericToolResult
            if action=='basic':
                if not isinstance(result,BasicResult) or result.summary['processing']!=CONFIG:raise ValueError()
                count=result.summary['sample_count']
                if type(count)!=int or not 1024<=count<=1_000_000 or len(result.frequency)!=count//2+1 or len(result.amplitude)!=len(result.frequency):raise ValueError()
            else:
                expected={'envelope':'analyze_envelope','frequency':'calculate_bearing_frequencies','matching':'match_frequency_evidence','comparison':'compare_measurements'}[action]
                from vibradiag.analysis import envelope,bearing_frequencies,matching,comparison
                expected_version={'envelope':envelope.VERSION,'frequency':bearing_frequencies.VERSION,'matching':matching.VERSION,'comparison':comparison.VERSION}[action]
                if (not isinstance(result,NumericToolResult) or result.tool_name!=expected or result.tool_version!=expected_version
                        or result.status not in {'ok','unavailable','failed'} or not isinstance(result.summary,dict) or not isinstance(result.arrays,dict)):raise ValueError()
            ready=codec.read(directory/'ready.json')
        except MeasurementError:raise
        except Exception:fail('NUMERIC_PROTOCOL_INVALID')
        guard()
        last_execution.set(dict(backend='process',protocol=codec.PROTOCOL,action=action,child_pid=ready['pid'],
            environment_keys=ready['environment_keys'],elapsed_seconds=monotonic()-started,deadline_ms=max(0,int((deadline-started)*1000)),configured_timeout_ms=timeout_ms,
            lifetime='windows_job_kill_on_close' if os.name=='nt' else 'linux_parent_death_signal',memory_limit_bytes=1024**3 if os.name=='nt' else None))
        return result
    finally:
        try:
            monitor_stop.set()
            if monitor:monitor.join(timeout=2)
            if job.process:job.process.close()
        finally:
            # UUID-owned private directory, checked before recursive cleanup. No other data is removed.
            if directory.name==identifier and directory.resolve().parent==parent.resolve() and not directory.is_symlink():
                try:shutil.rmtree(directory)
                except OSError:pass  # A forced parent exit can also leave private IPC files; lifecycle cleanup is separate.
            with _guard:_jobs.pop(identifier,None)
            job.stopped.set()
