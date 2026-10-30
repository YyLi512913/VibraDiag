"""Official durable saver compatibility, isolated from public task tables/API."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import pickle
import sys
from threading import Lock
from uuid import uuid4
import pytest
import psycopg
from psycopg import sql
from langgraph.checkpoint.postgres import PostgresSaver
from vibradiag.core.config import setting
from vibradiag.core.paths import BACKEND_ROOT

spec=importlib.util.spec_from_file_location('m3_checkpoint_probe',BACKEND_ROOT/'scripts/m3_checkpoint_probe.py')
probe=importlib.util.module_from_spec(spec);sys.modules[spec.name]=probe;spec.loader.exec_module(probe)


@pytest.fixture(scope='module')
def checkpoint_schema():
    if not setting('VIBRADIAG_TEST_DATABASE_URL') or not setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL'):
        pytest.skip('Dedicated PostgreSQL test roles not configured')
    probe.test_urls(require_separate=True)
    name=probe.schema_name(uuid4());probe.install_schema(name)
    try:yield name
    finally:probe.drop_schema(name)


@pytest.mark.parametrize('value',['public','m3_checkpoint_probe_bad','m3_checkpoint_probe_'+('a'*32)+';drop schema public'])
def test_schema_validation_refuses_non_probe_targets(value):
    with pytest.raises(ValueError):probe.validated_schema(value)


def test_migration_roles_tables_and_transactional_indexes(checkpoint_schema):
    with probe.connect(checkpoint_schema) as connection:
        tables=connection.execute('SELECT tablename FROM pg_tables WHERE schemaname=%s',(checkpoint_schema,)).fetchall()
        assert {r['tablename'] for r in tables}==set(probe.TABLES)
        versions=connection.execute('SELECT v FROM checkpoint_migrations ORDER BY v').fetchall()
        assert [r['v'] for r in versions]==list(range(len(probe.migration_plan())))
        role=connection.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()
        assert not any(role.values())
        assert not connection.execute('SELECT has_schema_privilege(current_user,%s,\'CREATE\') AS allowed',(checkpoint_schema,)).fetchone()['allowed']


@pytest.mark.parametrize('statement',['CREATE TABLE forbidden(x integer)','ALTER TABLE checkpoints ADD COLUMN forbidden integer',
    'UPDATE checkpoint_migrations SET v=v','DELETE FROM checkpoints'])
def test_application_cannot_ddl_rewrite_versions_or_delete_history(checkpoint_schema,statement):
    with probe.connect(checkpoint_schema) as connection,pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute(statement)


def test_new_schema_migrations_rollback_atomically(checkpoint_schema):
    name=probe.schema_name(uuid4())
    with probe.connect(checkpoint_schema,migration=True) as connection:
        with pytest.raises(RuntimeError),connection.transaction():
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
            connection.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(name)))
            for statement in probe.migration_plan():connection.execute(statement)
            raise RuntimeError('Inject before commit')
        assert connection.execute('SELECT oid FROM pg_namespace WHERE nspname=%s',(name,)).fetchone() is None


def test_pause_resume_new_connections_reenters_node_but_computes_once(checkpoint_schema):
    thread=str(uuid4());paused=probe.run_phase(checkpoint_schema,thread,'pause')
    assert paused['paused'] and paused['next']==['analyze_and_ask'] and paused['counts']==dict(node_entries=1,compute_count=1)
    done=probe.run_phase(checkpoint_schema,thread,'resume')
    assert done['finished'] and done['confirmed'] and not done['paused']
    assert done['counts']==dict(node_entries=2,compute_count=1)
    assert done['checkpoint_id']!=paused['checkpoint_id']
    assert done['result']['rms']==pytest.approx(2**.5,abs=1e-12)
    assert done['result']['unit']=='m/s2' and done['result']['sample_count']==4096


def test_unknown_response_failure_recovers_pending_resume_without_recomputing(checkpoint_schema):
    thread=str(uuid4());probe.run_phase(checkpoint_schema,thread,'pause')
    def fail():raise RuntimeError('Injected after acknowledged resume before node result')
    with pytest.raises(RuntimeError):probe.run_phase(checkpoint_schema,thread,'resume',blocked=fail)
    done=probe.run_phase(checkpoint_schema,thread,'recover')
    assert done['finished'] and done['counts']==dict(node_entries=3,compute_count=1)


def test_concurrent_probe_ledger_serializes_actual_numerical_execution(checkpoint_schema,monkeypatch):
    original=probe.compute_basic;count=0;guard=Lock();operation=str(uuid4())
    def counted(*args):
        nonlocal count
        with guard:count+=1
        return original(*args)
    monkeypatch.setattr(probe,'compute_basic',counted)
    def run(_):
        with probe.connect(checkpoint_schema) as connection:return probe._analysis_once(connection,operation)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert count==1 and results[0]==results[1]


def test_unknown_thread_is_empty_not_an_authorization_boundary(checkpoint_schema):
    with probe.connect(checkpoint_schema) as connection:
        assert PostgresSaver(connection,serde=probe.serializer()).get_tuple({'configurable':{'thread_id':str(uuid4())}}) is None


def test_pickle_payload_never_deserialized():
    serde=probe.serializer()
    assert not serde.pickle_fallback
    with pytest.raises(NotImplementedError):serde.loads_typed(('pickle',pickle.dumps({'unexpected':'value'})))


@dataclass
class UnapprovedProbeValue:
    marker: str


def test_strict_msgpack_does_not_reconstruct_unapproved_classes():
    serde=probe.serializer();encoded=serde.dumps_typed(UnapprovedProbeValue('untrusted-constructor'))
    decoded=serde.loads_typed(encoded)
    # The vendor returns raw data for a blocked constructor. It is not semantic/integrity validation.
    assert not isinstance(decoded,UnapprovedProbeValue) and decoded=={'marker':'untrusted-constructor'}


def test_corrupt_interrupt_payload_fails_closed(checkpoint_schema):
    thread=str(uuid4());probe.run_phase(checkpoint_schema,thread,'pause')
    with probe.connect(checkpoint_schema,migration=True) as connection:
        changed=connection.execute("UPDATE checkpoint_writes SET type='pickle',blob=%s WHERE thread_id=%s AND channel='__interrupt__'",(b'not trusted',thread)).rowcount
        assert changed>=1
    with pytest.raises(NotImplementedError):probe.run_phase(checkpoint_schema,thread,'resume')


def test_wrong_probe_marker_prevents_cleanup_of_another_schema(checkpoint_schema):
    name=probe.schema_name(uuid4())
    with pytest.raises(ValueError):probe.drop_schema(name)
