from copy import deepcopy
import pytest
from vibradiag.evaluation.method_comparison_review import review


def example():
    case=dict(id='K05',documents=['injection'],must_refuse=True,withdraw_after_generation=False)
    row=dict(case_id='K05',kind='method',status='refused',draft=dict(observations=[]),error_code='MODEL_NO_SUPPORTED_EXTRACT',calls=[],
             metrics=dict(met_expectations=True),model_rounds=[dict(phase='extractive_draft',state='completed',trace=dict(provider='fixture'))])
    return dict(rows=[row]),dict(method_cases=[case],comparison_cases=[])


def test_runtime_schema_rejection_is_safe_but_not_semantic_success():
    receipt,spec=example();assert review(receipt,spec)['all_cases_met_expectations']
    before=deepcopy(receipt)
    receipt['rows'][0].update(error_code='MODEL_DECISION_UNAVAILABLE',model_rounds=[dict(phase='decision',state='failed',trace=dict(provider='fixture'))])
    result=review(receipt,spec)
    assert not result['all_cases_met_expectations'] and result['rows'][0]['original_rubric_met']
    assert result['rows'][0]['classification']=='runtime_rejection_not_semantic_refusal'
    assert result['model_calls_added']==0 and not result['semantic_quality_qualified']
    assert review(before,spec)['all_cases_met_expectations']


def test_refusal_before_retrieval_does_not_prove_source_relevance_judgment():
    receipt,spec=example();receipt['rows'][0]['model_rounds']=[dict(phase='decision',state='completed',trace={})]
    assert not review(receipt,spec)['all_cases_met_expectations']


def test_withdrawal_must_actually_happen_and_observe_provider_response():
    receipt,spec=example();spec['method_cases'][0]['withdraw_after_generation']=True
    receipt['rows'][0]['metrics']['lifecycle_injected']=False
    assert not review(receipt,spec)['all_cases_met_expectations']
    receipt['rows'][0]['metrics']['lifecycle_injected']=True
    receipt['rows'][0]['model_rounds'][0]['state']='failed'  # withdrawal fences generation receipt
    assert review(receipt,spec)['all_cases_met_expectations']


@pytest.mark.parametrize('mode',['missing','duplicate'])
def test_incomplete_or_duplicate_receipt_cannot_be_reviewed(mode):
    receipt,spec=example()
    receipt['rows']=[] if mode=='missing' else receipt['rows']*2
    with pytest.raises(ValueError):review(receipt,spec)
