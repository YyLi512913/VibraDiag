"""T02b1 compatibility probe only. Not imported by the production task executor."""
from contextlib import contextmanager
from hashlib import sha256
from importlib.metadata import version
import json
import re
from typing import TypedDict
from uuid import UUID

import numpy as np
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import StateGraph,START,END
from langgraph.types import interrupt,Command
from sqlalchemy.engine import make_url
from vibradiag.analysis.basic import compute_basic
from vibradiag.core.config import setting

PACKAGE_VERSION='3.2.0'
SCHEMA_PREFIX='m3_checkpoint_probe_'
TABLES=('checkpoint_migrations','checkpoints','checkpoint_blobs','checkpoint_writes','probe_ledger')


def schema_name(run_id):
    return SCHEMA_PREFIX+UUID(str(run_id)).hex


def validated_schema(name):
    if not re.fullmatch(SCHEMA_PREFIX+r'[a-f0-9]{32}',name):raise ValueError('Invalid probe schema')
    return name


def test_urls(*,require_separate=False):
    app=make_url(setting('VIBRADIAG_TEST_DATABASE_URL'));owner=make_url(setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL'))
    if app.drivername!='postgresql+psycopg' or owner.drivername!='postgresql+psycopg':raise ValueError('PostgreSQL test URLs required')
    if (app.host,app.port,app.database)!=(owner.host,owner.port,owner.database) or app.username==owner.username:
        raise ValueError('Separate application/migration roles on one test database required')
    if require_separate and app.database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:
        raise ValueError('Dedicated test database required')
    return app,owner


@contextmanager
def connect(name,*,migration=False):
    validated_schema(name);app,owner=test_urls();url=owner if migration else app
    # Passwords are passed as parameters, never command arguments, metadata or output.
    with psycopg.connect(host=url.host,port=url.port or 5432,dbname=url.database,user=url.username,password=url.password,
                         autocommit=True,row_factory=dict_row,connect_timeout=5) as connection:
        connection.execute(sql.SQL('SET search_path TO {},pg_catalog').format(sql.Identifier(name)))
        connection.execute("SET statement_timeout='10s'")
        connection.execute("SET lock_timeout='5s'")
        yield connection


def serializer():
    # Explicit restricted mode: no permissive default, additional constructors or pickle fallback.
    return JsonPlusSerializer(pickle_fallback=False,allowed_json_modules=(),allowed_msgpack_modules=())


def migration_plan():
    if version('langgraph-checkpoint-postgres')!=PACKAGE_VERSION:raise ValueError('Unreviewed checkpoint package')
    # On new empty tables these indexes can be created transactionally. Do not use this
    # transformation as an online upgrade policy for populated production tables.
    return tuple(statement.replace('CREATE INDEX CONCURRENTLY','CREATE INDEX') for statement in PostgresSaver.MIGRATIONS)


def install_schema(name):
    name=validated_schema(name);app,_=test_urls()
    with connect(name,migration=True) as connection,connection.transaction():
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
        connection.execute(sql.SQL('COMMENT ON SCHEMA {} IS {}').format(sql.Identifier(name),sql.Literal('VibraDiag T02b1 isolated probe '+name)))
        for number,statement in enumerate(migration_plan()):
            connection.execute(statement)
            connection.execute('INSERT INTO checkpoint_migrations(v) VALUES (%s)',(number,))
        connection.execute('''CREATE TABLE probe_ledger (
            operation_key text PRIMARY KEY, source_sha256 text NOT NULL, result jsonb NOT NULL,
            node_entries integer NOT NULL CHECK(node_entries>=1),compute_count integer NOT NULL CHECK(compute_count=1))''')
        role=sql.Identifier(app.username)
        connection.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(name),role))
        for table in TABLES:
            permissions=sql.SQL('SELECT') if table=='checkpoint_migrations' else sql.SQL('SELECT,INSERT,UPDATE')
            connection.execute(sql.SQL('GRANT {} ON {} TO {}').format(permissions,sql.Identifier(table),role))


def drop_schema(name):
    """Delete only a UUID-named, marked schema created by this probe in the test database."""
    name=validated_schema(name)
    with connect(name,migration=True) as connection:
        marker=connection.execute("SELECT obj_description(oid,'pg_namespace') AS marker FROM pg_namespace WHERE nspname=%s",(name,)).fetchone()
        if not marker or marker['marker']!='VibraDiag T02b1 isolated probe '+name:raise ValueError('Probe schema ownership marker missing')
        connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))


def _analysis_once(connection,operation):
    UUID(operation)
    # A small deterministic sine fixture, not a bearing fault or a registered business source.
    values=2*np.sin(2*np.pi*128*np.arange(4096)/4096)
    digest=sha256(values.astype('<f8').tobytes()).hexdigest()
    with connection.transaction():
        connection.execute('SELECT pg_advisory_xact_lock(%s)',(int.from_bytes(sha256(operation.encode()).digest()[:8],'big',signed=True),))
        existing=connection.execute('SELECT * FROM probe_ledger WHERE operation_key=%s FOR UPDATE',(operation,)).fetchone()
        if existing:
            if existing['source_sha256']!=digest:raise ValueError('Probe source changed')
            connection.execute('UPDATE probe_ledger SET node_entries=node_entries+1 WHERE operation_key=%s',(operation,))
            return existing['result']
        summary=compute_basic(values,4096.,'m/s2').summary
        result={'rms':summary['metrics']['rms']['value'],'unit':'m/s2','sample_count':4096,'source_sha256':digest}
        connection.execute('''INSERT INTO probe_ledger VALUES (%s,%s,%s,1,1)''',(operation,digest,psycopg.types.json.Jsonb(result)))
        return result


class ProbeState(TypedDict,total=False):
    operation: str
    confirmed: bool
    result: dict
    finished: bool


def compile_graph(saver,connection,*,blocked=None):
    def analyze_and_ask(state):
        result=_analysis_once(connection,state['operation'])
        accepted=interrupt({'code':'probe_continue','source_sha256':result['source_sha256']})
        if accepted is not True:raise ValueError('Explicit probe acknowledgement required')
        if blocked:blocked()
        return {'result':result,'confirmed':True}
    def finish(state):
        if not state['confirmed']:raise ValueError('Probe not acknowledged')
        return {'finished':True}
    builder=StateGraph(ProbeState)
    builder.add_node('analyze_and_ask',analyze_and_ask);builder.add_node('finish',finish)
    builder.add_edge(START,'analyze_and_ask');builder.add_edge('analyze_and_ask','finish');builder.add_edge('finish',END)
    return builder.compile(checkpointer=saver)


def run_phase(name,thread_id,phase,*,blocked=None):
    thread_id=str(UUID(thread_id));config={'configurable':{'thread_id':thread_id},'recursion_limit':8}
    with connect(name) as connection,connect(name) as checkpoint_connection:
        saver=PostgresSaver(checkpoint_connection,serde=serializer())
        graph=compile_graph(saver,connection,blocked=blocked)
        if phase=='pause':value={'operation':thread_id}
        elif phase=='resume':value=Command(resume=True)
        elif phase=='recover':value=None
        else:raise ValueError('Unknown probe phase')
        graph.invoke(value,config,durability='sync')
        snapshot=graph.get_state(config)
        counts=connection.execute('SELECT node_entries,compute_count FROM probe_ledger WHERE operation_key=%s',(thread_id,)).fetchone()
        return {'paused':bool(snapshot.next),'next':list(snapshot.next),'checkpoint_id':snapshot.config['configurable']['checkpoint_id'],
            'confirmed':snapshot.values.get('confirmed',False),'finished':snapshot.values.get('finished',False),
            'result':snapshot.values.get('result'),'counts':counts}
