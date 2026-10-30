"""Real credential-free numerical children through authenticated business APIs."""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from time import monotonic,sleep
from threading import Event
import os
import pytest
from sqlalchemy import text
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.compute import runner
from test_tool_runs import case,run as tool_run
from test_matching_runs import matching,execute as match_run
from test_comparison_runs import comparison,execute as compare_run
from test_agent_tasks import create,read,execute,manual_executor


@pytest.fixture(autouse=True)
def process_backend(numeric_test_backend,monkeypatch):
    monkeypatch.setenv('VIBRADIAG_NUMERIC_BACKEND','process')
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TIMEOUT_MS','15000')


def basic(case):
    return case['client'].post('/api/v1/analyses',json=dict(measurement_revision_id=case['revision'],confirmation_id=case['confirmation']),headers={'Idempotency-Key':str(uuid4())})


def test_actual_basic_child_result_and_scrubbed_environment(case):
    response=basic(case);assert response.status_code==201,response.text
    result=response.json();assert result['status']=='completed',result
    receipt=result['result_summary']['compute_execution']
    assert receipt['backend']=='process' and receipt['child_pid']!=os.getpid()
    assert not any(k.startswith(('VIBRADIAG','DEEPSEEK')) for k in receipt['environment_keys'])
    assert receipt['protocol']=='numeric-ipc-v1' and runner.active_jobs()==[]
    assert result['metrics']['rms']['value']>0


def test_envelope_and_frequency_children(case):
    response=tool_run(case);assert response.status_code==201,response.text
    result=response.json();assert result['status']=='completed' and result['result_status']=='ok',result
    assert result['result']['compute_execution']['action']=='envelope'
    from test_tool_runs import payload
    response=tool_run(case,payload(case,'calculate_bearing_frequencies'))
    assert response.status_code==201 and response.json()['result']['compute_execution']['action']=='frequency',response.text
    assert response.json()['result']['summary']['frequencies']['BPFO']['frequency_hz']==90


def test_real_matching_child_with_authorized_parent_products(matching):
    response=match_run(matching);assert response.status_code==201,response.text
    result=response.json();assert result['result_status']=='ok',result
    assert result['result']['compute_execution']['action']=='matching' and result['evidence_refs']


def test_real_comparison_child_with_two_authorized_inputs(comparison):
    response=compare_run(comparison);assert response.status_code==201,response.text
    result=response.json();assert result['result_status']=='ok',result
    assert result['result']['compute_execution']['action']=='comparison'
    assert result['result']['summary']['metrics']['rms']['relative_change_percent']==pytest.approx(-50.)


@pytest.mark.parametrize('scenario,code',[('crash','NUMERIC_CHILD_CRASHED'),('bad_result','NUMERIC_PROTOCOL_INVALID'),('hang','NUMERIC_DEADLINE_EXCEEDED')])
def test_failed_children_leave_failed_resources_and_release_slots(case,monkeypatch,scenario,code):
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TEST_SCENARIO',scenario)
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TIMEOUT_MS','3000' if scenario=='hang' else '15000')
    response=basic(case);assert response.status_code==201,response.text
    result=response.json();assert result['status']=='failed' and result['error_code']==code,result
    assert not result['artifacts'] and runner.active_jobs()==[]
    monkeypatch.delenv('VIBRADIAG_NUMERIC_TEST_SCENARIO')
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TIMEOUT_MS','15000')
    assert basic(case).json()['status']=='completed'


def wait_ready(count=1):
    deadline=monotonic()+20
    while monotonic()<deadline:
        jobs=runner.active_jobs()
        if len(jobs)==count and all((j.directory/'ready.json').exists() for j in jobs):return jobs
        sleep(.025)
    raise AssertionError('Owned numerical children not ready')


def test_mixed_m1_m2_share_two_slots_across_actual_children(case,monkeypatch):
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TEST_SCENARIO','hang')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(basic,case);second=pool.submit(tool_run,case)
        try:
            jobs=wait_ready(2)
            assert basic(case).status_code==429
            assert len(runner.active_jobs())==2
        finally:
            for job in runner.active_jobs():job.cancelled.set()
        assert first.result().json()['status']=='failed' and second.result().json()['status']=='failed'
    assert runner.active_jobs()==[]


def test_task_cancel_stops_owned_child_and_keeps_cancelled_terminal(case,monkeypatch):
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TEST_SCENARIO','hang')
    task=create(case).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(execute,task)
        jobs=wait_ready();assert jobs[0].task_id is not None
        current=read(case,task);started=monotonic()
        response=case['client'].post('/api/v1/agent-tasks/'+task['id']+'/cancel',json={'expected_version':current['version']},headers={'Idempotency-Key':str(uuid4())})
        assert response.status_code==200,response.text
        assert jobs[0].stopped.is_set() and monotonic()-started<5
        future.result()
    result=read(case,task)
    assert result['status']=='cancelled' and result['draft'] is None and runner.active_jobs()==[]


def test_confirmation_revocation_stops_active_numeric_child(case,monkeypatch):
    from uuid import UUID
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TEST_SCENARIO','hang')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(basic,case);wait_ready()
        with transaction() as connection:
            connection.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(case['confirmation'])))
        result=future.result(timeout=10).json()
    assert result['status']=='failed' and result['error_code']=='CONFIRMATION_REVOKED'
    assert not result['artifacts'] and runner.active_jobs()==[]


def test_test_backends_cannot_be_enabled_for_development_database(case,monkeypatch):
    monkeypatch.setattr(runner,'_test_allowed',lambda:False)
    monkeypatch.setenv('VIBRADIAG_NUMERIC_BACKEND','inline_test')
    response=basic(case)
    assert response.status_code==503 and response.json()['error']['code']=='NUMERIC_BACKEND_INVALID'
    monkeypatch.setenv('VIBRADIAG_NUMERIC_BACKEND','process')
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TEST_SCENARIO','hang')
    response=basic(case)
    assert response.status_code==503 and response.json()['error']['code']=='NUMERIC_TEST_MODE_FORBIDDEN'
    monkeypatch.setattr(runner,'_test_allowed',lambda:True)
    monkeypatch.delenv('VIBRADIAG_NUMERIC_TEST_SCENARIO')
    def denied(*args,**kwargs):raise OSError('Private process detail must not be returned')
    monkeypatch.setattr(runner,'OwnedProcess',denied)
    response=basic(case)
    assert response.status_code==503 and response.json()['error']['code']=='NUMERIC_START_FAILED'
    assert 'Private process detail' not in response.text and runner.active_jobs()==[]


def test_watchdog_terminates_even_when_host_guard_is_slow(case,monkeypatch):
    from vibradiag.domain.measurement_errors import MeasurementError
    entered=Event();release=Event()
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TEST_SCENARIO','hang')
    monkeypatch.setenv('VIBRADIAG_NUMERIC_TIMEOUT_MS','800')
    def slow():
        jobs=runner.active_jobs()
        if jobs and jobs[0].process is not None:
            entered.set();release.wait(5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(runner.run,'basic',dict(raw=case['raw'],sampling_rate=12000,unit='m/s2'),lambda:None,check=slow)
        try:
            assert entered.wait(10)
            job=runner.active_jobs()[0];deadline=monotonic()+3
            while job.process.poll() is None and monotonic()<deadline:sleep(.025)
            assert job.process.poll() is not None and not future.done()
        finally:release.set()
        with pytest.raises(MeasurementError) as caught:future.result()
        assert caught.value.code=='NUMERIC_DEADLINE_EXCEEDED'
    assert runner.active_jobs()==[]


def test_task_remaining_active_budget_bounds_child_lifetime(case):
    from uuid import UUID
    from vibradiag.services import agent_task_service as service
    task=create(case).json();claimed=service.claim_next(UUID(task['id']))
    with transaction() as connection:
        row=service._running(connection,claimed['id'],claimed['lease_token'])
        service._change(connection,row,'running',budget=dict(policy_version='agent-budget-v1',active_ms=119000,reservations=[]))
    started=monotonic();service.execute_claimed(claimed);result=read(case,task)
    assert result['status']=='timed_out' and result['budget']['active_ms']==120000,result
    assert monotonic()-started<5 and result['draft'] is None and runner.active_jobs()==[]
