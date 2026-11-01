"""Opt-in durable model method graph; candidates are proposals, authority stays server-side."""
from langgraph.graph import StateGraph,START,END
from sqlalchemy import text
from vibradiag.agents.graph_state import Cursor,CursorBinding,MODEL_GRAPH,MODEL_GRAPH_V2,MODEL_EXECUTOR,MODEL_EXECUTOR_V2,defer_wait
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.graph_store import task_saver,invalid
from vibradiag.services import agent_task_service as tasks,agent_knowledge_service as knowledge,agent_model_service as models
from vibradiag.services.model_method_service import finish_task


def execute(task,principal):
    identifier,token=task['id'],task['lease_token']
    executor=task['input_snapshot']['executor']
    if executor not in {MODEL_EXECUTOR,MODEL_EXECUTOR_V2}:raise MeasurementError('TASK_EXECUTOR_UNSUPPORTED','Unsupported method graph',status=409)
    version=MODEL_GRAPH_V2 if executor==MODEL_EXECUTOR_V2 else MODEL_GRAPH
    decision_phase,extract_phase=models.method_phases(task)
    def model(phase,read_only=False):return models.run_round(identifier,token,principal,phase,read_only=read_only)
    def decision():
        try:return model(decision_phase,True)['output']['action']
        except MeasurementError as error:
            if error.status in {401,403}:raise
            return 'unavailable'
    def source():
        with transaction() as c:
            tasks._running(c,identifier,token);tasks._authorize(c,principal,identifier,write=True,current_only=True)
            call=c.execute(text("SELECT * FROM agent_calls WHERE task_id=:id AND action_name='search_published_knowledge' AND state IN ('completed','unavailable') ORDER BY call_no DESC LIMIT 1"),dict(id=identifier)).mappings().first()
        return tasks._read_source(call,principal) if call else None
    def ready():
        result=source()
        return bool(result and result['current_and_valid'] and result['hits'])
    def node(name):
        def run(state):
            CursorBinding.model_validate(state).check(identifier,version)
            flag=defer_wait.set(True)
            try:
                payload=knowledge.prepare(identifier,token,principal)
                if name in {'decide','compose'}:
                    try:model(decision_phase if name=='decide' else extract_phase)
                    except MeasurementError as error:
                        if error.status in {401,403}:raise
                        with transaction() as c:
                            row=tasks._running(c,identifier,token);tasks._event(c,row,'model_node_unavailable',dict(node=name,error_code=error.code))
                elif name=='knowledge':
                    if decision()!='choose_action':tasks.conflict('MODEL_DECISION_NOT_EXECUTABLE')
                    # Re-reading the decision regenerated/validated its registered candidate; payload remains server-owned.
                    tasks._dispatch(identifier,token,'search_published_knowledge',payload.model_dump(mode='json'),principal)
                with transaction() as c:
                    row=tasks._running(c,identifier,token);tasks._event(c,row,'graph.node',dict(node=name,graph_version=version))
                return dict(task_id=str(identifier),graph_version=version)
            finally:defer_wait.reset(flag)
        return run
    with task_saver(task,principal) as saver:
        builder=StateGraph(Cursor)
        for name in ('prepare','decide','knowledge','compose'):builder.add_node(name,node(name))
        builder.add_edge(START,'prepare');builder.add_edge('prepare','decide')
        builder.add_conditional_edges('decide',lambda _:'knowledge' if decision()=='choose_action' else END)
        builder.add_conditional_edges('knowledge',lambda _:'compose' if ready() else END)
        builder.add_edge('compose',END)
        graph=builder.compile(checkpointer=saver);config=dict(configurable=dict(thread_id=saver.binding['thread_id']),recursion_limit=12)
        graph.invoke(None if saver.get_tuple(config) else dict(task_id=str(identifier),graph_version=version),config,durability='sync')
        if graph.get_state(config).next:invalid()
    finish_task(identifier,token,principal)
