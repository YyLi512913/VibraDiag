"""Versioned per-step durable rules graph; model decisions and knowledge are not integrated."""
from langgraph.graph import StateGraph,START,END
from sqlalchemy import text
from vibradiag.agents.graph_state import Cursor,CursorBinding,GRAPH_VERSION,GraphWait,defer_wait
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.agents.formal_policy import PolicyStop
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.graph_store import task_saver,invalid
from vibradiag.services import agent_task_service as service
from vibradiag.services import comparison_service as comparisons
from vibradiag.services.tool_options_service import options


class RuleExecution:
    def __init__(self,task,principal):
        self.task=task;self.id=task['id'];self.token=task['lease_token'];self.principal=principal

    def scope(self):
        with transaction() as connection:
            row=service._running(connection,self.id,self.token)
            service._authorize(connection,self.principal,self.id,write=True,current_only=True)
        return row

    def base(self):
        row=self.scope();confirmation=service._confirmation(row['measurement_revision_id'],self.principal)
        if not confirmation:service._wait(self.id,self.token,'input_confirmation_required')
        return dict(measurement_revision_id=str(row['measurement_revision_id']),confirmation_id=str(confirmation))

    def select(self,field):
        row=self.scope()
        with transaction() as connection:
            _,record=service.input_revision(connection,self.principal,row['measurement_revision_id'])
        available=options(record['id'],self.principal)
        items=available['profiles'] if field=='profile_id' else [c for c in available['contexts'] if c['is_current_bearing_revision']]
        return service._selection(self.id,self.token,field,items)

    def source(self,action,*,bound=None):
        self.scope()
        with transaction() as connection:
            calls=connection.execute(text("SELECT * FROM agent_calls WHERE task_id=:id AND action_name=:action AND state IN ('completed','unavailable') ORDER BY call_no DESC"),
                dict(id=self.id,action=action)).mappings().all()
        call=next((c for c in calls if bound is None or bool(c['payload'].get('context_id'))==bound),None)
        if not call:invalid()
        result=service._read_source(call,self.principal)
        if result['status']!='completed' or not result.get('input_confirmation_active',True):service.conflict('INPUT_CONFIRMATION_REVOKED')
        if result.get('is_current_bearing_revision') is False:service.conflict('STALE_TASK_BINDING')
        return result

    def dispatch(self,action,**payload):
        return service._dispatch(self.id,self.token,action,dict(self.base(),**payload),self.principal)

    def prepare(self):
        if self.scope()['goal']!='explain_method':self.base()

    def basic(self):self.dispatch('analyze_basic')
    def profile(self):self.select('profile_id')
    def context(self):self.select('context_id')
    def envelope(self):self.dispatch('analyze_envelope',tool_name='analyze_envelope',profile_id=self.select('profile_id'),mode='exploratory')
    def frequency(self):self.dispatch('calculate_bearing_frequencies',tool_name='calculate_bearing_frequencies',context_id=self.select('context_id'))
    def bound_envelope(self):self.dispatch('analyze_envelope',tool_name='analyze_envelope',profile_id=self.select('profile_id'),context_id=self.select('context_id'),mode='exploratory')

    def matching(self):
        basic=self.source('analyze_basic');envelope=self.source('analyze_envelope',bound=True);frequency=self.source('calculate_bearing_frequencies')
        self.dispatch('match_frequency_evidence',tool_name='match_frequency_evidence',context_id=self.select('context_id'),
            envelope_run_id=envelope['tool_execution_id'],frequency_run_id=frequency['tool_execution_id'],basic_analysis_id=basic['analysis_id'])

    def comparison(self):
        row=self.scope()
        with transaction() as connection:
            declaration,_,_=comparisons.resolve_declaration(connection,self.principal,row['payload']['comparison_declaration_id'],write=True,current_only=True)
        before=service._confirmation(declaration['before_revision_id'],self.principal)
        if not before:service._wait(self.id,self.token,'before_input_confirmation_required')
        self.dispatch('compare_measurements',tool_name='compare_measurements',declaration_id=str(declaration['id']),before_confirmation_id=str(before))

    def match_ready(self):
        envelope=self.source('analyze_envelope',bound=False);frequency=self.source('calculate_bearing_frequencies')
        return envelope['result_status']=='ok' and frequency['result_status']=='ok' and bool(frequency['result']['summary'].get('uncertainty_complete'))

    def node(self,name):
        def execute(state):
            try:
                CursorBinding.model_validate(state).check(self.id,GRAPH_VERSION)
                flag=defer_wait.set(True)
                try:
                    self.scope();getattr(self,name)()
                    with transaction() as connection:
                        row=service._running(connection,self.id,self.token)
                        service._event(connection,row,'graph.node',{'node':name,'graph_version':GRAPH_VERSION})
                finally:defer_wait.reset(flag)
                return {'task_id':str(self.id),'graph_version':GRAPH_VERSION}
            except (GraphWait,service.Fenced,service.YieldTask,PolicyStop,MeasurementError):raise
            except Exception:raise MeasurementError('TASK_GRAPH_NODE_FAILED','Graph node unavailable',status=503) from None
        return execute

    def build(self,saver):
        builder=StateGraph(Cursor)
        for name in ('prepare','basic','profile','envelope','context','frequency','bound_envelope','matching','comparison'):
            builder.add_node(name,self.node(name))
        builder.add_edge(START,'prepare')
        builder.add_conditional_edges('prepare',lambda _:END if self.scope()['goal']=='explain_method' else 'comparison' if self.scope()['goal']=='compare_measurements' else 'basic')
        builder.add_conditional_edges('basic',lambda _:END if self.scope()['goal']=='inspect_signal' else 'profile')
        for before,after in (('profile','envelope'),('envelope','context'),('context','frequency'),('bound_envelope','matching')):builder.add_edge(before,after)
        builder.add_conditional_edges('frequency',lambda _:'bound_envelope' if self.match_ready() else END)
        builder.add_edge('matching',END);builder.add_edge('comparison',END)
        return builder.compile(checkpointer=saver)

    def finish(self):
        goal=self.scope()['goal']
        if goal=='explain_method':service._finish(self.id,self.token,self.principal,'refused',(),('published_knowledge_service_not_integrated',));return
        if goal=='compare_measurements':
            result=self.source('compare_measurements');summary=result['result']['summary']
            missing=summary.get('reasons',[])+summary.get('related_reasons',[])
            if summary['comparison_status']!='comparable' and not missing:missing=['comparison_not_comparable']
            observations=[service._observation(result['tool_execution_id'],'tool_run','exploratory_observation','比较状态：'+summary['comparison_status']+'；差值不代表维修有效。')]
        else:
            basic=self.source('analyze_basic');missing=[]
            observations=[service._observation(basic['analysis_id'],'basic_analysis','numeric_fact','基础指标与幅度谱已由确定性工具计算，来源可追溯。')]
            if goal=='bearing_observations':
                envelope=self.source('analyze_envelope',bound=False);frequency=self.source('calculate_bearing_frequencies')
                observations.extend([service._observation(envelope['tool_execution_id'],'tool_run','exploratory_observation','包络运行状态：'+envelope['result_status']+'；不构成轴承故障结论。'),
                    service._observation(frequency['tool_execution_id'],'tool_run','exploratory_observation','理想频率运行状态：'+frequency['result_status']+'；仍受参数与不确定量条件限制。')])
                missing=list(frequency['result']['missing_requirements'])
                if self.match_ready():
                    matching=self.source('match_frequency_evidence')
                    observations.append(service._observation(matching['tool_execution_id'],'tool_run','exploratory_observation','频率匹配保留显著性、重合与替代解释；不是故障分类。'))
                    missing.extend(matching['result']['missing_requirements'])
                else:missing.append('matching_parents_or_uncertainty_incomplete')
                missing.append('diagnostic_profile_not_qualified')
        service._finish(self.id,self.token,self.principal,'completed_degraded' if missing else 'completed',observations,missing)


def execute(task,principal):
    run=RuleExecution(task,principal)
    with task_saver(task,principal) as saver:
        config={'configurable':{'thread_id':saver.binding['thread_id']},'recursion_limit':16}
        graph=run.build(saver)
        existing=saver.get_tuple(config)
        # Business pause/resume is authoritative. Retry a failed/pending node rather than
        # replaying START or accepting a browser-supplied LangGraph Command/config.
        graph.invoke(None if existing else {'task_id':str(task['id']),'graph_version':GRAPH_VERSION},config,durability='sync')
        if graph.get_state(config).next:invalid()
    # Final business publication is outside graph saving. A crash here resumes END,
    # re-reads every actual source and commits only under the current task lease.
    run.finish()
