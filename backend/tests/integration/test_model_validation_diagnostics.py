import json
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services import agent_model_service as models
from test_agent_models import rounds,run,retrieve
from test_agent_knowledge import knowledge,model,read
from test_tool_runs import login


def test_failed_round_retains_bounded_diagnostics_without_dispatch_or_free_retry(rounds):
    rounds['fake'].replacement=dict(action='choose_action',candidate_id=str(uuid4()),**{'PRIVATE_FIELD':'PRIVATE_VALUE'})
    with pytest.raises(MeasurementError) as caught:run(rounds)
    assert caught.value.code=='MODEL_OUTPUT_SCHEMA_INVALID'
    result=read(rounds,rounds['task']);row=result['model_rounds'][0]
    assert row['state']=='failed' and row['trace'] and result['model_called'] and not result['calls']
    data=row['validation_diagnostics'];assert any(i['kind']=='missing' and 'rationale' in i['location'] for i in data['issues'])
    assert any(i['kind']=='extra_forbidden' and 'unrecognized_field' in i['location'] for i in data['issues'])
    assert 'PRIVATE_' not in json.dumps(data)
    with transaction() as c:
        saved=c.execute(text('SELECT raw_output_sha256,validated_output,validation_diagnostics FROM agent_model_rounds WHERE task_id=:id'),dict(id=UUID(rounds['task']['id']))).mappings().one()
    assert saved['validation_diagnostics']==data and saved['validated_output'] is None and len(saved['raw_output_sha256'])==64
    with pytest.raises(MeasurementError) as closed:run(rounds)
    assert closed.value.code=='MODEL_ROUND_CLOSED_OR_UNKNOWN' and len(rounds['fake'].calls)==1
    assert len(result['budget']['reservations'])==1
    for sql in ["UPDATE agent_model_rounds SET validation_diagnostics=NULL WHERE task_id=:id",'DELETE FROM agent_model_rounds WHERE task_id=:id']:
        with pytest.raises(DBAPIError),transaction() as c:c.execute(text(sql),dict(id=UUID(rounds['task']['id'])))
    with login(rounds,'owner') as other:assert other.get('/api/v1/agent-tasks/'+rounds['task']['id']).status_code==404


def test_success_and_policy_rejection_do_not_get_schema_diagnostics(rounds):
    run(rounds)
    assert read(rounds,rounds['task'])['model_rounds'][0]['validation_diagnostics'] is None
    retrieve(rounds)
    rounds['fake'].replacement=dict(outcome='cited_context',selections=[dict(source_id=str(uuid4()),quote='PRIVATE_VALUE')])
    with pytest.raises(MeasurementError) as caught:run(rounds,'extractive_draft')
    assert caught.value.code=='MODEL_QUOTE_UNGROUNDED'
    assert all(m['validation_diagnostics'] is None for m in read(rounds,rounds['task'])['model_rounds'])


def test_cancellation_still_fences_schema_diagnostics(rounds):
    from vibradiag.services import agent_task_service as tasks
    def cancel(*args):
        current=read(rounds,rounds['task']);tasks.cancel_task(UUID(current['id']),current['version'],str(uuid4()),rounds['principal'])
    rounds['fake'].hook=cancel
    rounds['fake'].replacement=dict(action='PRIVATE_VALUE')
    with pytest.raises(MeasurementError):run(rounds)
    result=read(rounds,rounds['task'])
    assert result['status']=='cancelled' and result['model_rounds'][0]['state']=='interrupted'
    assert result['model_rounds'][0]['validation_diagnostics'] is None and result['draft'] is None


def test_database_rejects_wrong_state_and_unbounded_diagnostic_envelope(rounds):
    try:models._validate('decision','{"action":"invalid"}',{})
    except MeasurementError as error:valid=error.validation_diagnostics
    invalid=[('OTHER_ERROR',valid),(None,valid),('MODEL_OUTPUT_SCHEMA_INVALID',dict(valid,version='invalid')),
             ('MODEL_OUTPUT_SCHEMA_INVALID',dict(valid,version=None)),
             ('MODEL_OUTPUT_SCHEMA_INVALID',dict(valid,raw_value='PRIVATE_VALUE')),
             ('MODEL_OUTPUT_SCHEMA_INVALID',dict(valid,issues=valid['issues']*9)),
             ('MODEL_OUTPUT_SCHEMA_INVALID',dict(valid,known_fields=['x'*5000]))]
    def attempt(*args):
        for code,data in invalid:
            with pytest.raises(DBAPIError),transaction() as c:
                models._fence(c,rounds['claimed']['lease_token'])
                c.execute(text("UPDATE agent_model_rounds SET state='failed',finished_at=now(),error_code=:error,validation_diagnostics=CAST(:data AS jsonb) WHERE task_id=:id AND state='running'"),
                          dict(id=UUID(rounds['task']['id']),error=code,data=json.dumps(data)))
    rounds['fake'].hook=attempt
    run(rounds)
    assert read(rounds,rounds['task'])['model_rounds'][0]['validation_diagnostics'] is None
