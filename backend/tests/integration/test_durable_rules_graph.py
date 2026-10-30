"""Business-bound checkpoints, current authority and crash/cancel commit windows."""
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.graph_store import task_saver,TaskSaver
from vibradiag.agents.graph_state import GRAPH_VERSION
from vibradiag.agents import rules_graph
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.services import agent_task_service as service,persistent_analysis_service as basic
from vibradiag.services.identity_service import authenticate
from test_tool_runs import case,login
from test_agent_tasks import create,read,resume,execute,manual_executor


def claim(case):
    task=create(case).json();claimed=service.claim_next(UUID(task['id']))
    assert claimed and claimed['input_snapshot']['executor']=='rules_graph_v1'
    return task,claimed


def config(saver):return {'configurable':{'thread_id':saver.binding['thread_id']}}


def test_binding_is_server_owned_immutable_and_private(case):
    task,claimed=claim(case)
    with task_saver(claimed,case['principal']) as saver:
        assert saver.binding['task_id']==UUID(task['id']) and saver.binding['graph_version']==GRAPH_VERSION
        assert 'thread_id' not in read(case,task)['graph']
    for statement in ["UPDATE agent_graph_bindings SET thread_id=:thread WHERE task_id=:id",
                      'DELETE FROM agent_graph_bindings WHERE task_id=:id']:
        with pytest.raises(DBAPIError),transaction() as connection:
            connection.execute(text(statement),dict(id=UUID(task['id']),thread=str(uuid4())))
    service.execute_claimed(claimed)
    assert read(case,task)['formal_graph_executed']


@pytest.mark.parametrize('user,org',[('owner','org'),('admin','org'),('outsider','other_org')])
def test_valid_thread_does_not_authorize_another_actor(case,user,org):
    task,claimed=claim(case)
    with login(case,user,org) as other:
        with transaction() as connection:principal=authenticate(connection,other.cookies.get('vibradiag_session'))
        with pytest.raises(MeasurementError) as caught:
            with task_saver(claimed,principal):pass
        assert caught.value.status==(403 if user=='admin' else 404)
    service.execute_claimed(claimed)


def test_saver_rechecks_session_after_construction(case):
    task,claimed=claim(case)
    with task_saver(claimed,case['principal']) as saver:
        with transaction() as connection:
            connection.execute(text('UPDATE auth_sessions SET revoked_at=now() WHERE id=:id'),dict(id=case['principal'].session_id))
        with pytest.raises(MeasurementError) as caught:saver.get_tuple(config(saver))
        assert caught.value.status==401
    service.execute_claimed(claimed)
    with login(case) as newer:
        case['client']=newer
        assert read(case,task)['question']['code']=='session_reauthentication_required'


def test_saver_rechecks_revoked_asset_scope(case):
    task,claimed=claim(case)
    with task_saver(claimed,case['principal']) as saver:
        with transaction() as connection:
            connection.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer AND asset_id=:asset'),case)
        with pytest.raises(MeasurementError) as caught:saver.get_tuple(config(saver))
        assert caught.value.status==404
    service.execute_claimed(claimed)


def test_config_cannot_switch_thread_or_namespace_and_raw_store_actions_disabled(case):
    task,claimed=claim(case)
    with task_saver(claimed,case['principal']) as saver:
        for forged in [{'thread_id':str(uuid4())},{'thread_id':saver.binding['thread_id'],'checkpoint_ns':'foreign'}]:
            with pytest.raises(MeasurementError) as caught:saver.get_tuple({'configurable':forged})
            assert caught.value.code=='CHECKPOINT_STATE_INVALID'
        for method in (saver.setup,saver.list,saver.delete_thread,saver.prune):
            with pytest.raises(RuntimeError):method()
    service.execute_claimed(claimed)


def test_checkpoint_insert_requires_current_lease_even_with_valid_thread(case):
    task,claimed=claim(case)
    with transaction() as connection:
        thread=connection.execute(text('SELECT thread_id FROM agent_graph_bindings WHERE task_id=:id'),dict(id=UUID(task['id']))).scalar_one()
    statement='''INSERT INTO public_agent_checkpoints.checkpoints(thread_id,checkpoint_id,checkpoint)
        VALUES (:thread,:checkpoint,'{}')'''
    for token in (None,str(uuid4())):
        with pytest.raises(DBAPIError),transaction() as connection:
            if token:connection.execute(text("SELECT set_config('vibradiag.graph_lease',:token,true)"),dict(token=token))
            connection.execute(text(statement),dict(thread=thread,checkpoint=str(uuid4())))
    service.execute_claimed(claimed)


def test_cancel_and_recovery_fence_an_already_constructed_saver(case):
    task,claimed=claim(case)
    with task_saver(claimed,case['principal']) as saver:
        result,_=service.cancel_task(UUID(task['id']),read(case,task)['version'],str(uuid4()),case['principal'])
        assert result['status']=='cancelled'
        with pytest.raises(service.Fenced):saver.get_tuple(config(saver))
    task,claimed=claim(case)
    with task_saver(claimed,case['principal']) as old:
        service.recover_interrupted()
        paused=read(case,task);assert resume(case,paused).status_code==202
        newer=service.claim_next(UUID(task['id']));assert newer['lease_token']!=claimed['lease_token']
        with pytest.raises(service.Fenced):old.get_tuple(config(old))
    service.execute_claimed(newer);assert read(case,task)['status']=='completed'


def test_end_checkpoint_before_business_commit_recovers_without_rerunning_nodes(case,monkeypatch):
    task,claimed=claim(case);original=rules_graph.RuleExecution.finish
    def crash(self):
        service.recover_interrupted()
        return original(self)
    monkeypatch.setattr(rules_graph.RuleExecution,'finish',crash)
    service.execute_claimed(claimed);paused=read(case,task)
    assert paused['status']=='interrupted' and paused['formal_graph_executed']
    completed_nodes=[e for e in paused['events'] if e['kind']=='graph.node']
    assert [e['details']['node'] for e in completed_nodes]==['prepare','basic']
    monkeypatch.setattr(rules_graph.RuleExecution,'finish',original)
    def fail(*args,**kwargs):raise AssertionError('Tool must not be called after END checkpoint')
    monkeypatch.setattr(basic,'create_analysis',fail)
    assert resume(case,paused).status_code==202
    execute(task);result=read(case,task)
    assert result['status']=='completed' and len(result['calls'])==1
    assert len([e for e in result['events'] if e['kind']=='graph.node'])==len(completed_nodes)


def test_call_commit_before_checkpoint_save_replays_ledger_instead_of_compute(case,monkeypatch):
    task,claimed=claim(case);original=TaskSaver.put;crashed=False
    def crash(self,configuration,checkpoint,metadata,new_versions):
        nonlocal crashed
        if not crashed:
            with transaction() as connection:
                complete=connection.execute(text("SELECT count(*) FROM agent_calls WHERE task_id=:id AND state='completed'"),dict(id=claimed['id'])).scalar_one()
            if complete:
                crashed=True;service.recover_interrupted()
        return original(self,configuration,checkpoint,metadata,new_versions)
    monkeypatch.setattr(TaskSaver,'put',crash)
    service.execute_claimed(claimed);paused=read(case,task)
    assert crashed and paused['status']=='interrupted' and paused['calls'][0]['state']=='completed'
    monkeypatch.setattr(TaskSaver,'put',original)
    def fail(*args,**kwargs):raise AssertionError('Completed call must be loaded, not created again')
    monkeypatch.setattr(basic,'create_analysis',fail)
    assert resume(case,paused).status_code==202
    execute(task);result=read(case,task)
    assert result['status']=='completed' and len(result['budget']['reservations'])==1


def test_corrupt_cursor_binding_is_rejected_and_never_becomes_a_draft(case,monkeypatch):
    task,claimed=claim(case)
    # Retain a running task after durable END to inject a bad stored cursor under its valid lease.
    monkeypatch.setattr(rules_graph.RuleExecution,'finish',lambda self:None)
    service.execute_claimed(claimed)
    with transaction() as connection:
        connection.execute(text("SELECT set_config('vibradiag.graph_lease',:token,true)"),dict(token=str(claimed['lease_token'])))
        changed=connection.execute(text('''UPDATE public_agent_checkpoints.checkpoints SET checkpoint=jsonb_set(
            checkpoint,'{channel_values,task_id}',to_jsonb(CAST(:wrong AS text))) WHERE thread_id=(
            SELECT thread_id FROM agent_graph_bindings WHERE task_id=:id)'''),dict(wrong=str(uuid4()),id=claimed['id'])).rowcount
        assert changed>0
    service.execute_claimed(claimed);result=read(case,task)
    assert result['status']=='failed' and result['error_code']=='CHECKPOINT_STATE_INVALID' and result['draft'] is None


def test_revocation_between_node_and_checkpoint_blocks_next_step(case,monkeypatch):
    task,claimed=claim(case);original=service._event
    def revoked(connection,row,kind,details=None):
        original(connection,row,kind,details)
        if kind=='graph.node' and details['node']=='basic':
            connection.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer AND asset_id=:asset'),case)
    monkeypatch.setattr(service,'_event',revoked)
    service.execute_claimed(claimed)
    assert case['client'].get('/api/v1/agent-tasks/'+task['id']).status_code==404
    with transaction() as connection:
        row=connection.execute(text('SELECT status,draft FROM agent_tasks WHERE id=:id'),dict(id=claimed['id'])).mappings().one()
        assert row['status']=='failed' and row['draft'] is None


def test_legacy_scaffold_task_keeps_its_original_executor(case):
    template=create(case).json();identifier=uuid4()
    with transaction() as connection:
        row=connection.execute(text('SELECT * FROM agent_tasks WHERE id=:id'),dict(id=UUID(template['id']))).mappings().one()
        snapshot=dict(row['input_snapshot'],executor='rules_scaffold_v1')
        connection.execute(text('''INSERT INTO agent_tasks(id,organization_id,requested_by,session_id,measurement_revision_id,
            goal,payload,payload_hash,input_snapshot,input_fingerprint)
            VALUES (:id,:org,:actor,:session,:revision,:goal,CAST(:payload AS jsonb),:hash,CAST(:snapshot AS jsonb),:fingerprint)'''),
            dict(id=identifier,org=row['organization_id'],actor=row['requested_by'],session=row['session_id'],revision=row['measurement_revision_id'],
                goal=row['goal'],payload=service.json_bytes(row['payload']).decode(),hash=row['payload_hash'],
                snapshot=service.json_bytes(snapshot).decode(),fingerprint=service.fingerprint(snapshot)))
    claimed=service.claim_next(identifier);service.execute_claimed(claimed)
    result=read(case,dict(id=str(identifier)))
    assert result['status']=='completed' and result['executor']=='rules_scaffold_v1'
    assert result['graph'] is None and not result['formal_graph_executed']
    service.cancel_task(UUID(template['id']),template['version'],str(uuid4()),case['principal'])
