"""Internal-only official saver with task authorization, cursor limits and transactional lease fencing."""
from contextlib import contextmanager
import json
import psycopg
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import text
from vibradiag.agents.graph_state import GRAPH_VERSION,CHECKPOINT_SCHEMA,NODES,CursorBinding
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import database_url,transaction

MAX_BYTES=65536


def invalid():raise MeasurementError('CHECKPOINT_STATE_INVALID','Stored graph cursor is unavailable',status=409)


class RestrictedSerializer(JsonPlusSerializer):
    def __init__(self):super().__init__(pickle_fallback=False,allowed_json_modules=(),allowed_msgpack_modules=())

    def loads_typed(self,data):
        if data[0] not in {'msgpack','null'} or len(data[1])>MAX_BYTES:invalid()
        return super().loads_typed(data)


class TaskSaver(PostgresSaver):
    def __init__(self,connection,task,principal,binding):
        super().__init__(connection,serde=RestrictedSerializer())
        self.task_id=task['id'];self.token=task['lease_token'];self.principal=principal;self.binding=binding
        self.graph_executed=False

    def _config(self,config):
        c=config.get('configurable',{})
        if c.get('thread_id')!=self.binding['thread_id'] or c.get('checkpoint_ns','')!='':invalid()
        if c.get('checkpoint_id') is not None and (not isinstance(c['checkpoint_id'],str) or len(c['checkpoint_id'])>64):invalid()

    def _validate(self,checkpoint,pending=()):
        try:
            if len(json.dumps(checkpoint,allow_nan=False).encode())>MAX_BYTES:invalid()
            values=checkpoint['channel_values']
            for key,value in values.items():
                if key in {'task_id','graph_version'}:
                    expected=str(self.task_id) if key=='task_id' else GRAPH_VERSION
                    if value!=expected:invalid()
                elif key=='__start__':CursorBinding.model_validate(value).check(self.task_id)
                elif key.startswith('branch:to:') and key[len('branch:to:'):] in NODES:
                    if value is not None:invalid()
                else:invalid()
            if ('task_id' in values)!=('graph_version' in values):invalid()
            for _,key,value in pending:
                if key in {'task_id','graph_version'}:
                    if value!=(str(self.task_id) if key=='task_id' else GRAPH_VERSION):invalid()
                elif key=='__error__':
                    if not isinstance(value,str) or len(value)>512:invalid()
                elif key.startswith('branch:to:') and key[len('branch:to:'):] in NODES:
                    if value is not None:invalid()
                else:invalid()
        except MeasurementError:raise
        except Exception:invalid()

    def get_tuple(self,config):
        self._config(config)
        try:
            result=super().get_tuple(config)
            if result:self._validate(result.checkpoint,result.pending_writes)
            return result
        except (MeasurementError,):raise
        except psycopg.Error:raise MeasurementError('CHECKPOINT_UNAVAILABLE','Graph storage unavailable',status=503) from None
        except Exception as error:
            from vibradiag.services.agent_task_service import Fenced
            if isinstance(error,Fenced):raise
            invalid()

    def put(self,config,checkpoint,metadata,new_versions):
        self._config(config);self._validate(checkpoint)
        result=super().put(config,checkpoint,metadata,new_versions);self.graph_executed=True;return result

    def put_writes(self,config,writes,task_id,task_path=''):
        self._config(config)
        self._validate({'channel_values':{}},[(task_id,k,v) for k,v in writes])
        return super().put_writes(config,writes,task_id,task_path)

    @contextmanager
    def _cursor(self,*,pipeline=False):
        from vibradiag.services import agent_task_service as service
        # Read/write scope is reloaded for every saver operation. It is not a stored role snapshot.
        with transaction() as connection:
            row,_,_=service._authorize(connection,self.principal,self.task_id,write=True,current_only=True)
            if row['status']!='running' or row['lease_token']!=self.token:raise service.Fenced()
            binding=connection.execute(text('SELECT * FROM agent_graph_bindings WHERE task_id=:id'),dict(id=self.task_id)).mappings().one()
            if binding['graph_version']!=GRAPH_VERSION or binding['thread_id']!=self.binding['thread_id'] or binding['input_fingerprint']!=row['input_fingerprint']:invalid()
        try:
            with self.lock,self.conn.transaction():
                with self.conn.cursor(row_factory=dict_row) as cursor:
                    cursor.execute('SELECT status,lease_token FROM public.agent_tasks WHERE id=%s FOR SHARE',(self.task_id,))
                    live=cursor.fetchone()
                    if not live or live['status']!='running' or live['lease_token']!=self.token:raise service.Fenced()
                    cursor.execute("SELECT set_config('vibradiag.graph_lease',%s,true)",(str(self.token),))
                    yield cursor
        except psycopg.Error:raise MeasurementError('CHECKPOINT_UNAVAILABLE','Graph storage unavailable',status=503) from None

    def setup(self):raise RuntimeError('Checkpoint DDL only through migrations')
    def list(self,*args,**kwargs):raise RuntimeError('Raw checkpoint listing disabled')
    def delete_thread(self,*args,**kwargs):raise RuntimeError('Checkpoint deletion disabled')
    def prune(self,*args,**kwargs):raise RuntimeError('Checkpoint pruning disabled')


@contextmanager
def task_saver(task,principal):
    from vibradiag.services import agent_task_service as service
    with transaction() as connection:
        row,_,_=service._authorize(connection,principal,task['id'],write=True,current_only=True)
        if row['status']!='running' or row['lease_token']!=task['lease_token']:raise service.Fenced()
        binding=connection.execute(text('SELECT * FROM agent_graph_bindings WHERE task_id=:id'),dict(id=task['id'])).mappings().one()
        if binding['graph_version']!=GRAPH_VERSION or binding['input_fingerprint']!=row['input_fingerprint']:invalid()
    url=database_url().set(drivername='postgresql').render_as_string(hide_password=False)
    try:
        with psycopg.connect(url,autocommit=True,row_factory=dict_row,connect_timeout=5) as connection:
            connection.execute(f'SET search_path TO {CHECKPOINT_SCHEMA},public')
            connection.execute("SET statement_timeout='10s'");connection.execute("SET lock_timeout='5s'")
            yield TaskSaver(connection,task,principal,dict(binding))
    except psycopg.Error:raise MeasurementError('CHECKPOINT_UNAVAILABLE','Graph storage unavailable',status=503) from None
