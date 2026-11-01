from copy import deepcopy
import pytest
from vibradiag.evaluation.method_comparison import score_method, score_comparison


def method():
    return dict(status='completed_degraded', sources={'bearing':'Check speed before interpreting bearing frequencies.', 'other':'Office attendance policy.'},
                selections=[dict(source_id='bearing',quote='Check speed')], unpublished=True,diagnostic_claim=False)


def test_valid_quote_is_not_a_relevance_pass():
    expected=dict(relevant_source_ids=['bearing'],relevant_spans={'bearing':['Check speed']},must_refuse=False)
    actual=method();assert score_method(expected,actual)['met_expectations']
    actual['selections']=[dict(source_id='other',quote='Office attendance')]
    result=score_method(expected,actual)
    assert result['citation_integrity'] and not result['relevant_extract'] and not result['met_expectations']
    actual['selections']=[dict(source_id='bearing',quote='bearing frequencies.')]
    assert not score_method(expected,actual)['met_expectations']


@pytest.mark.parametrize('quote',['', 'invented', '   '])
def test_empty_or_forged_quote_fails(quote):
    actual=method();actual['selections'][0]['quote']=quote
    assert not score_method(dict(relevant_source_ids=['bearing'],relevant_spans={'bearing':['Check speed']},must_refuse=False),actual)['met_expectations']


def test_no_source_refusal_and_diagnostic_boundary():
    actual=method();actual.update(status='refused',sources={},selections=[])
    expected=dict(relevant_source_ids=[],relevant_spans={},must_refuse=True)
    assert score_method(expected,actual)['met_expectations']
    actual['diagnostic_claim']=True
    assert not score_method(expected,actual)['met_expectations']


def comparison():
    return dict(comparison_status='comparable',metrics={'rms':dict(relative_change_percent=-50.)},
                repair_effectiveness=None,diagnostic_claim=False,both_inputs_current_and_authorized=True,parents_valid=True)


@pytest.mark.parametrize('value',[float('nan'),float('inf'),True,None,-49.])
def test_wrong_or_nonfinite_comparison_is_not_a_pass(value):
    actual=comparison();actual['metrics']['rms']['relative_change_percent']=value
    assert not score_comparison(dict(comparison_status='comparable',relative_changes_percent={'rms':-50.}),actual)['met_expectations']


def test_unavailable_requires_empty_metrics_and_both_inputs():
    expected=dict(comparison_status='not_comparable',relative_changes_percent={})
    actual=comparison();actual['comparison_status']='not_comparable'
    assert not score_comparison(expected,actual)['met_expectations']
    actual['metrics']={};assert score_comparison(expected,actual)['met_expectations']
    for field,value in [('parents_valid',False),('both_inputs_current_and_authorized',False),('repair_effectiveness','effective')]:
        changed=deepcopy(actual);changed[field]=value
        assert not score_comparison(expected,changed)['met_expectations']
