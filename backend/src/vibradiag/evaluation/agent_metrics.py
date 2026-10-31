"""Objective development-control metrics; no semantic judge or industrial accuracy claim."""
from collections import Counter
from statistics import median

STRATEGIES=('rules','single_router','agent')


def order(index):return STRATEGIES[index%3:]+STRATEGIES[:index%3]


def assess(case,task,*,model_reserved,model_confirmed):
    actual=Counter(c['action'] for c in task['calls'] if c['state'] in {'completed','unavailable'})
    expected=Counter(case['expected_actions']);pause=not case['confirmed']
    allowed={c['resource_id'] for c in task['calls'] if c['state'] in {'completed','unavailable'} and c['resource_id']}
    observations=(task['draft'] or {}).get('observations',[])
    citations=[citation for o in observations for citation in o['citations']]
    valid=all(c['id'] in allowed and c['kind'] in {'basic_analysis','tool_run'} for c in citations)
    valid=valid and (not observations or task['draft_validity']['usable'])
    covered=actual==expected
    pause_valid=bool(task['status']=='waiting_information' and task['question']['code']=='input_confirmation_required' and not task['calls'] and model_reserved==0) if pause else True
    cited=valid and (bool(citations) if expected else not citations)
    boundary=all(c['action'] in {'analyze_basic','analyze_envelope','calculate_bearing_frequencies','match_frequency_evidence'} for c in task['calls'])
    qualified_missing=case['goal']!='bearing_observations' or 'diagnostic_profile_not_qualified' in (task['draft'] or {}).get('missing_requirements',[])
    expectation=bool(task['status']==case['expected_status'] and covered and cited and pause_valid and boundary and qualified_missing)
    return dict(expectation_met=expectation,status_matches=task['status']==case['expected_status'],tool_coverage_exact=covered,
        citation_links_valid=valid if citations else None,citation_requirement_met=cited,citation_count=len(citations),pause_before_model_valid=pause_valid,
        allowed_action_boundary=boundary,diagnostic_qualification_still_blocked=qualified_missing,
        model_calls_reserved=model_reserved,model_calls_confirmed=model_confirmed,model_calls_unknown=max(0,model_reserved-model_confirmed),
        draft_available=bool(task['draft']),semantic_correctness_evaluated=False)


def summarize(rows):
    result={}
    for strategy in STRATEGIES:
        items=[r for r in rows if r['strategy']==strategy];usage=[t.get('usage',{}) for r in items for t in r['model_traces']]
        result[strategy]=dict(runs=len(items),expectations_met=sum(r['metrics']['expectation_met'] for r in items),
            statuses=dict(Counter(r['status'] for r in items)),cited_drafts=sum(r['metrics'].get('citation_count',0)>0 for r in items),
            citation_links_valid=sum(r['metrics']['citation_links_valid'] is True for r in items),
            model_reserved=sum(r['metrics']['model_calls_reserved'] for r in items),model_confirmed=sum(r['metrics']['model_calls_confirmed'] for r in items),
            tools=sum(len(r['calls']) for r in items),median_execution_seconds=median(r['execution_seconds'] for r in items) if items else None,
            token_usage={k:sum(u[k] for u in usage) if usage and all(type(u.get(k)) is int for u in usage) else 0 if items and all(r['metrics']['model_calls_reserved']==0 for r in items) else None for k in ('prompt_tokens','completion_tokens','total_tokens')},
            monetary_cost=None,monetary_cost_reason='No dated provider pricing/invoice bound to this run')
    return result
