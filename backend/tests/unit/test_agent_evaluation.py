from copy import deepcopy
from vibradiag.evaluation.agent_metrics import assess,summarize,order
from vibradiag.evaluation.agent_benchmark import protocol


def example():
    case=protocol()['cases'][0]
    task=dict(status='completed',question=None,calls=[dict(action='analyze_basic',state='completed',resource_id='actual')],
        draft=dict(observations=[dict(citations=[dict(kind='basic_analysis',id='actual')])]),draft_validity=dict(usable=True))
    return case,task


def test_rotation_balances_three_strategies_and_protocol_is_development_only():
    assert len({order(i)[0] for i in range(3)})==3
    assert order(0)==order(3)
    p=protocol();assert not p['independent_holdout'] and len(p['cases'])==5
    assert len({c['id'] for c in p['cases']})==5 and p['repetitions']==1


def test_valid_links_are_not_semantic_correctness_or_industrial_qualification():
    case,task=example();result=assess(case,task,model_reserved=0,model_confirmed=0)
    assert result['expectation_met'] and result['citation_links_valid']
    assert not result['semantic_correctness_evaluated']


def test_invented_or_wrong_role_citations_fail_even_with_matching_status():
    case,task=example()
    for change in (dict(id='invented',kind='basic_analysis'),dict(id='actual',kind='knowledge_chunk')):
        task['draft']['observations'][0]['citations']=[change]
        result=assess(case,task,model_reserved=1,model_confirmed=0)
        assert not result['expectation_met'] and not result['citation_links_valid'] and result['model_calls_unknown']==1


def test_tool_multiplicity_and_early_stop_are_scored_not_hidden():
    case,task=example();task['calls']*=2
    assert not assess(case,task,model_reserved=0,model_confirmed=0)['tool_coverage_exact']
    task['calls']=[];task['draft']=None;task['status']='refused'
    assert not assess(case,task,model_reserved=1,model_confirmed=1)['expectation_met']


def test_expected_pause_requires_no_tools_and_no_model_reservation():
    case=protocol()['cases'][3];task=dict(status='waiting_information',question=dict(code='input_confirmation_required'),calls=[],draft=None,draft_validity=dict(usable=False))
    assert assess(case,task,model_reserved=0,model_confirmed=0)['expectation_met']
    assert not assess(case,task,model_reserved=1,model_confirmed=0)['pause_before_model_valid']


def test_unknown_usage_is_not_zero_and_no_money_or_latency_ranking():
    row=dict(strategy='agent',status='failed',calls=[],execution_seconds=2.,model_traces=[dict(usage=dict(prompt_tokens=10))],metrics=dict(expectation_met=False,citation_links_valid=True,model_calls_reserved=1,model_calls_confirmed=1))
    result=summarize([row]);assert result['agent']['token_usage']==dict(prompt_tokens=10,completion_tokens=None,total_tokens=None)
    assert result['agent']['monetary_cost'] is None and result['rules']['median_execution_seconds'] is None


def test_inactive_draft_does_not_count_as_valid_citation():
    case,task=example();task['draft_validity']['usable']=False
    assert not assess(case,task,model_reserved=0,model_confirmed=0)['citation_links_valid']


def test_no_answer_does_not_inflate_cited_draft_denominator():
    case=protocol()['cases'][3];task=dict(status='waiting_information',question=dict(code='input_confirmation_required'),calls=[],draft=None,draft_validity=dict(usable=False))
    metrics=assess(case,task,model_reserved=0,model_confirmed=0)
    assert metrics['citation_links_valid'] is None and metrics['expectation_met']
    summary=summarize([dict(strategy='rules',status=task['status'],calls=[],execution_seconds=1.,model_traces=[],metrics=metrics)])['rules']
    assert summary['cited_drafts']==0 and summary['citation_links_valid']==0
    assert summary['token_usage']['total_tokens']==0
