"""Feedback-first comparison with a strong deterministic baseline and explicit lifecycle faults."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import json
import os
from time import monotonic
from uuid import UUID,uuid4
from fastapi.testclient import TestClient
from sqlalchemy import text
from vibradiag.core.paths import BACKEND_ROOT,PROJECT_ROOT
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.llm.formal_gateway import configuration
from vibradiag.services.identity_service import password_hasher,authenticate
from vibradiag.services import agent_task_service as tasks
from vibradiag.agents.rules_graph import RuleExecution
from vibradiag.evaluation import single_router
from vibradiag.evaluation.agent_benchmark import fixture
from vibradiag.evaluation.agent_metrics import order,summarize
from vibradiag.evaluation.agent_admission import audit

PROTOCOL=BACKEND_ROOT/'config/agent-feedback-evaluation-v1.json'


def rule_gate(task,principal):
    run=RuleExecution(task,principal);run.prepare();run.basic();run.frequency()
    frequency=run.source('calculate_bearing_frequencies');basic=run.source('analyze_basic')
    missing=list(frequency['result']['missing_requirements']);results=[(basic['analysis_id'],'basic_analysis','numeric_fact'),(frequency['tool_execution_id'],'tool_run','exploratory_observation')]
    if frequency['result_status']=='ok' and frequency['result']['summary'].get('uncertainty_complete'):
        run.envelope();run.bound_envelope();run.matching();matching=run.source('match_frequency_evidence')
        results.extend((run.source('analyze_envelope',bound=bound)['tool_execution_id'],'tool_run','exploratory_observation') for bound in (False,True))
        results.append((matching['tool_execution_id'],'tool_run','exploratory_observation'));missing.extend(matching['result']['missing_requirements'])
    missing.append('diagnostic_profile_not_qualified')
    tasks._finish(run.id,run.token,principal,'completed_degraded',[tasks._observation(i,k,c,'按实际工具回执保留探索观察；不构成故障或维修有效结论。') for i,k,c in results],missing)


def controlled_rules(task,principal):
    try:rule_gate(task,principal)
    except (tasks.YieldTask,tasks.Fenced):pass
    except Exception as error:
        try:
            with transaction() as c:
                row=tasks._running(c,task['id'],task['lease_token']);tasks._change(c,row,'failed',error=getattr(error,'code','BENCHMARK_RULE_FAILED'))
        except tasks.Fenced:pass


@contextmanager
def lifecycle(task,case,client,confirmation):
    original=tasks._dispatch;state=dict(injected=False,calls_at_injection=None)
    def dispatch(identifier,token,action,payload,principal):
        result=original(identifier,token,action,payload,principal)
        if identifier==task['id'] and action=='calculate_bearing_frequencies' and not state['injected'] and case['fault']!='none':
            state['injected']=True
            with transaction() as c:state['calls_at_injection']=c.execute(text('SELECT count(*) FROM agent_calls WHERE task_id=:id'),dict(id=identifier)).scalar_one()
            if case['fault']=='cancel_after_frequency':
                current=client.get('/api/v1/agent-tasks/'+str(identifier)).json()
                response=client.post('/api/v1/agent-tasks/'+str(identifier)+'/cancel',json=dict(expected_version=current['version']),headers={'Idempotency-Key':str(uuid4())});response.raise_for_status()
            else:
                with transaction() as c:c.execute(text('UPDATE input_confirmations SET revoked_at=now() WHERE id=:id'),dict(id=UUID(str(confirmation))))
        return result
    tasks._dispatch=dispatch
    try:yield state
    finally:tasks._dispatch=original


def assess(case,task,reserved,confirmed,injected,calls_at_injection=None):
    actions=[h['action'] for h in task['calls']];prefix=['analyze_basic','calculate_bearing_frequencies'];fault=case['fault']!='none'
    expected=prefix if fault or not case['uncertainty_complete'] else prefix+['analyze_envelope','analyze_envelope','match_frequency_evidence']
    coverage=Counter(actions)==Counter(expected);ordered=actions[:2]==prefix
    if fault:
        status_ok=task['status']=='cancelled' if case['fault']=='cancel_after_frequency' else task['status'] in {'waiting_information','failed'}
        no_late_tools=len(actions)==calls_at_injection if calls_at_injection is not None else actions[-1:] == ['calculate_bearing_frequencies']
        safe=status_ok and task['draft'] is None and no_late_tools and injected
        cited=True;count=0;qualification=True
    else:
        citations=[r for o in (task['draft'] or {}).get('observations',[]) for r in o['citations']]
        allowed={h['resource_id'] for h in task['calls'] if h['state'] in {'completed','unavailable'}}
        cited=bool(citations) and task['draft_validity']['usable'] and all(r['id'] in allowed and r['kind'] in {'basic_analysis','tool_run'} for r in citations)
        count=len(citations);qualification='diagnostic_profile_not_qualified' in (task['draft'] or {}).get('missing_requirements',[])
        safe=task['status']=='completed_degraded'
    return dict(expectation_met=bool(coverage and ordered and safe and cited and qualification),tool_coverage_exact=coverage,frequency_before_envelope=ordered,
        lifecycle_boundary_valid=safe if fault else None,fault_injected=injected,tool_calls_at_injection=calls_at_injection,citation_count=count,citation_links_valid=cited if count else None,
        diagnostic_qualification_still_blocked=qualification,model_calls_reserved=reserved,model_calls_confirmed=confirmed,model_calls_unknown=max(0,reserved-confirmed),
        semantic_correctness_evaluated=False)


def run(private,progress=lambda value:None):
    from vibradiag.main import app
    spec=json.loads(PROTOCOL.read_text(encoding='utf8'));cfg=configuration('deepseek',purpose='numeric');org=uuid4();user=uuid4();password=str(uuid4())+str(uuid4())
    with transaction() as c:
        c.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic feedback evaluation '+str(org)))
        c.execute(text('INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,:name,:hash)'),dict(id=user,org=org,name='evaluation',hash=password_hasher.hash(password)))
        c.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,'engineer',:id)"),dict(id=user,org=org))
    rows=[];inventory=[]
    with TestClient(app) as client:
        response=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username='evaluation',password=password));response.raise_for_status();client.headers['x-csrf-token']=response.json()['csrf_token']
        with transaction() as c:principal=authenticate(c,client.cookies.get('vibradiag_session'))
        for index,case in enumerate(spec['cases']):
            # A separate input per strategy prevents injected lifecycle changes leaking to the next arm.
            for strategy in order(index):
                revision,signal_hash=fixture(client,principal,case,case['signal'])
                confirmation=tasks._confirmation(UUID(revision),principal)
                if strategy=='rules':inventory.append(dict(case_id=case['id'],signal_sha256=signal_hash,source_family=case['source_family'],split=case['split']))
                os.environ['VIBRADIAG_NUMERIC_EXECUTOR']='model' if strategy=='agent' else 'rules'
                started=monotonic();response=client.post('/api/v1/agent-tasks',json=dict(goal=case['goal'],description=case['description'],measurement_revision_id=revision),headers={'Idempotency-Key':str(uuid4())});response.raise_for_status()
                identifier=response.json()['id'];claimed=tasks.claim_next(UUID(identifier));receipt=None
                with lifecycle(claimed,case,client,confirmation) as injected:
                    if strategy=='agent':tasks.execute_claimed(claimed)
                    elif strategy=='rules':controlled_rules(claimed,principal)
                    else:
                        workflows=dict(feedback_rules=dict(description='Run basic then ideal frequencies before envelope. Read actual uncertainty: stop if incomplete, otherwise run unbound and bound envelope then matching. All prerequisites and qualification limits remain enforced.',execute=controlled_rules),
                            full_rules=dict(description='Unconditional production bearing workflow: basic, unbound envelope, frequencies, then conditional bound envelope and matching.',execute=lambda task,principal:tasks.execute_claimed(task)))
                        receipt=single_router.execute(claimed,principal,cfg,private/(identifier+'.json'),workflows=workflows)
                response=client.get('/api/v1/agent-tasks/'+identifier);response.raise_for_status();task=response.json();seconds=monotonic()-started
                traces=[r['trace'] for r in task['model_rounds'] if r['trace']]
                if receipt and receipt.get('trace'):traces.append(receipt['trace'])
                reserved=sum(r['kind']=='model' for r in task['budget']['reservations'])
                metric=assess(case,task,reserved,len(traces),injected['injected'],injected['calls_at_injection'])
                row=dict(case_id=case['id'],split=case['split'],strategy=strategy,status=task['status'],error_code=task['error_code'],task_id=identifier,
                    calls=task['calls'],graph=task['graph'],execution_seconds=seconds,model_traces=traces,budget=task['budget'],
                    selected_workflow=receipt.get('selected_workflow') if receipt else None,signal_sha256=signal_hash,metrics=metric,
                    missing_requirements=(task['draft'] or {}).get('missing_requirements',[]))
                rows.append(row);(private/'partial-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf8');progress(dict(case_id=case['id'],strategy=strategy,status=task['status'],expectation_met=metric['expectation_met']))
    previous=json.loads((PROJECT_ROOT/'experiments/m3-t05a-development-comparison.json').read_text(encoding='utf8'))
    readiness=audit(inventory,{r['signal_sha256'] for r in previous['rows']},independent_annotations=spec['independent_annotations'],blind_holdout=spec['blind_holdout'])
    return dict(protocol_version=spec['protocol_version'],protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),configuration=asdict(cfg),rows=rows,inventory=inventory,
        summary=summarize(rows),summary_functional=summarize([r for r in rows if r['case_id'] in {c['id'] for c in spec['cases'] if c['fault']=='none'}]),
        summary_lifecycle=summarize([r for r in rows if r['case_id'] in {c['id'] for c in spec['cases'] if c['fault']!='none'}]),
        summary_by_partition={split:summarize([r for r in rows if r['split']==split]) for split in ('development','prospective_synthetic')},
        previous_development_receipt_sha256=sha256((PROJECT_ROOT/'experiments/m3-t05a-development-comparison.json').read_bytes()).hexdigest(),
        all_cases_met_expectations=all(r['metrics']['expectation_met'] for r in rows),admission=readiness,
        independent_holdout=False,semantic_quality_qualified=False,factory_trial_approved=False,
        methodology=dict(strategies='feedback-aware deterministic rules, one-call router offered the same rules, and production Agent',
            transport='in-process ASGI, real database/files/numerical services',repetitions=1,warmup=False,independent_acquisitions=0,source_families=1,
            intervention='Scoped dispatch wrapper injects cancellation or confirmation revocation after the actual frequency result; no synthetic model/tool output',
            separate_input_ids_per_strategy=True,source_bytes_paired_within_case=True),
        limitations=spec['scope_exclusions']+['Public developer-authored controls; prospective synthetic partition is not independent equipment holdout',
            'Rules baseline is upgraded to handle the conditional policy, so outcomes are not directly comparable to T05a rules timings',
            'Lifecycle outcomes are boundary checks, not model reasoning successes; no monetary or general latency superiority claim'])
