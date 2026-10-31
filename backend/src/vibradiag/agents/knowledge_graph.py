"""Method-only durable retrieval graph. Returns cited context, not a generated answer."""
from langgraph.graph import StateGraph,START,END
from sqlalchemy import text
from vibradiag.agents.graph_state import Cursor,CursorBinding,KNOWLEDGE_GRAPH,defer_wait
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.graph_store import task_saver,invalid
from vibradiag.services import agent_task_service as tasks
from vibradiag.services import agent_knowledge_service as knowledge


def execute(task,principal):
    identifier,token=task['id'],task['lease_token']
    def node(name):
        def run(state):
            CursorBinding.model_validate(state).check(identifier,KNOWLEDGE_GRAPH)
            flag=defer_wait.set(True)
            try:
                payload=knowledge.prepare(identifier,token,principal)
                if name=='knowledge':tasks._dispatch(identifier,token,'search_published_knowledge',payload.model_dump(mode='json'),principal)
                with transaction() as c:
                    row=tasks._running(c,identifier,token);tasks._event(c,row,'graph.node',dict(node=name,graph_version=KNOWLEDGE_GRAPH))
                return dict(task_id=str(identifier),graph_version=KNOWLEDGE_GRAPH)
            finally:defer_wait.reset(flag)
        return run
    with task_saver(task,principal) as saver:
        builder=StateGraph(Cursor);builder.add_node('prepare',node('prepare'));builder.add_node('knowledge',node('knowledge'))
        builder.add_edge(START,'prepare');builder.add_edge('prepare','knowledge');builder.add_edge('knowledge',END)
        graph=builder.compile(checkpointer=saver)
        config=dict(configurable=dict(thread_id=saver.binding['thread_id']),recursion_limit=8)
        graph.invoke(None if saver.get_tuple(config) else dict(task_id=str(identifier),graph_version=KNOWLEDGE_GRAPH),config,durability='sync')
        if graph.get_state(config).next:invalid()
    with transaction() as c:
        tasks._running(c,identifier,token)
        call=c.execute(text("SELECT * FROM agent_calls WHERE task_id=:id AND action_name='search_published_knowledge' AND state IN ('completed','unavailable')"),dict(id=identifier)).mappings().one()
    source=tasks._read_source(call,principal)
    hits=source['hits'] if source['current_and_valid'] else []
    missing=('knowledge_relevance_not_qualified',) if hits else ('published_knowledge_not_found_or_no_longer_available',)
    observations=[tasks._observation(hit['id'],'knowledge_chunk','method_explanation',
        '已定位一段已发布方法资料；这是待复核引用，不表示其相关性已验证，也不形成设备结论。') for hit in hits]
    tasks._finish(identifier,token,principal,'completed_degraded' if hits else 'refused',observations,missing,error=None if hits else 'PUBLISHED_KNOWLEDGE_UNAVAILABLE')
