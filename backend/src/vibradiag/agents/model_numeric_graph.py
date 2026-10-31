"""Bounded numerical planner. Node names encode the step; checkpoints contain no model output."""
from langgraph.graph import StateGraph,START,END
from vibradiag.agents.graph_state import Cursor,CursorBinding,NUMERIC_GRAPH,NUMERIC_GRAPH_V2,NUMERIC_NODES,defer_wait
from vibradiag.infrastructure.persistence.graph_store import task_saver,invalid
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services import agent_task_service as tasks,agent_model_service as models,agent_numeric_service as numeric


def execute(task,principal):
    identifier,token=task['id'],task['lease_token']
    version=NUMERIC_GRAPH_V2 if task['input_snapshot']['executor']=='model_numeric_v2' else NUMERIC_GRAPH
    phase=numeric.phase(task)
    def decision(step):return models.run_round(identifier,token,principal,phase,step=step,read_only=True)['output']
    def node(name):
        def run(state):
            CursorBinding.model_validate(state).check(identifier,version);flag=defer_wait.set(True)
            try:
                if name=='prepare':numeric.prepare_step(task,principal,0)
                else:
                    step=int(name.rsplit('_',1)[1]);numeric.prepare_step(task,principal,step)
                    if name.startswith('decide_'):models.run_round(identifier,token,principal,phase,step=step)
                    else:numeric.dispatch_step(task,principal,step)
                with transaction() as c:
                    row=tasks._running(c,identifier,token);tasks._event(c,row,'graph.node',dict(node=name,graph_version=version))
                return dict(task_id=str(identifier),graph_version=version)
            finally:defer_wait.reset(flag)
        return run
    with task_saver(task,principal) as saver:
        builder=StateGraph(Cursor)
        for name in NUMERIC_NODES:builder.add_node(name,node(name))
        builder.add_edge(START,'prepare');builder.add_edge('prepare','decide_0')
        for step in range(6):
            def route(_,step=step):
                output=decision(step)
                if output['action']=='finish':return END
                if step>=5:tasks.conflict('NUMERIC_STEP_LIMIT')
                return f'act_{step}'
            builder.add_conditional_edges(f'decide_{step}',route)
            if step<5:builder.add_edge(f'act_{step}',f'decide_{step+1}')
        graph=builder.compile(checkpointer=saver);config=dict(configurable=dict(thread_id=saver.binding['thread_id']),recursion_limit=16)
        graph.invoke(None if saver.get_tuple(config) else dict(task_id=str(identifier),graph_version=version),config,durability='sync')
        if graph.get_state(config).next:invalid()
    numeric.finish(task,principal)
