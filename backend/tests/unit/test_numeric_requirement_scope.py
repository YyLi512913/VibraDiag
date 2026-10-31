import json
from pathlib import Path
from vibradiag.services.numeric_requirement_scope import scopes,enrich
from vibradiag.infrastructure.llm.formal_gateway import configuration,Gateway
from vibradiag.domain.measurement_errors import MeasurementError
import pytest


def pair(missing,status='ok',mode='exploratory',action='analyze_envelope'):
    return dict(action_name=action,payload=dict(mode=mode)),dict(status='completed',result_status=status,result=dict(missing_requirements=missing,summary=dict(uncertainty_complete=True)))


def test_original_failure_conflated_frequency_uncertainty_with_envelope_release_limits():
    original=json.loads((Path(__file__).parents[1]/'fixtures/t05b-f02-decision.json').read_text(encoding='utf8'))
    last=original['rounds'][-1];assert last['validated_output']['action']=='finish' and last['context']['candidates']
    envelope=last['context']['completed'][-1];call,result=pair(envelope['missing_requirements'])
    classified=scopes(call,result)
    assert classified['diagnostic_release_limitations']==['verified_acquisition_bandwidth','qualified_numeric_profile']
    assert not classified['frequency_matching_preconditions'] and not classified['execution_blockers']
    assert last['context']['completed'][1]['missing_requirements']==[]


@pytest.mark.parametrize('status,mode',[('unavailable','exploratory'),('ok','diagnostic_evidence')])
def test_release_scopes_cannot_turn_unavailable_or_diagnostic_results_into_allowed_exploration(status,mode):
    call,result=pair(['qualified_numeric_profile'],status,mode);classified=scopes(call,result)
    assert not classified['diagnostic_release_limitations']
    assert classified['execution_blockers'] if status=='unavailable' else classified['unclassified']


def test_unknown_missing_items_remain_unknown_and_raw_missing_list_is_preserved():
    call,result=pair(['qualified_numeric_profile','new_requirement'])
    context=dict(completed=[dict(missing_requirements=result['result']['missing_requirements'])],candidates=[dict(candidate_id='actual',action='analyze_envelope')])
    enriched=enrich(context,[call],[result])
    assert enriched['completed'][0]['missing_requirements']==['qualified_numeric_profile','new_requirement']
    assert enriched['completed'][0]['requirement_scopes']['unclassified']==['new_requirement']
    assert not enriched['candidates'][0]['diagnostic_release_allowed'] and 'requirement_scopes' not in context['completed'][0]


@pytest.mark.parametrize('complete',[True,False,None])
def test_frequency_uncertainty_is_actual_tool_field_not_inferred_from_empty_missing(complete):
    call,result=pair(['geometry_uncertainty'] if complete is False else [],action='calculate_bearing_frequencies')
    result['result']['summary']['uncertainty_complete']=complete
    enriched=enrich(dict(completed=[dict(missing_requirements=result['result']['missing_requirements'])],candidates=[]),[call],[result])
    assert enriched['completed'][0]['frequency_uncertainty_complete'] is complete


@pytest.mark.parametrize('purpose,phase',[('numeric','numeric_decision_v2'),('numeric_v2','numeric_decision')])
def test_v1_and_v2_policy_cannot_cross_phase(purpose,phase):
    cfg=configuration('deepseek',purpose=purpose)
    with pytest.raises(MeasurementError) as error:Gateway(cfg).invoke(phase,{},dict(type='object'))
    assert error.value.code=='MODEL_POLICY_MISMATCH'


def test_original_numeric_policy_fingerprint_is_preserved():
    from dataclasses import asdict
    from vibradiag.core.paths import PROJECT_ROOT
    original=json.loads((PROJECT_ROOT/'experiments/m3-t05b-feedback-comparison.json').read_text(encoding='utf8'))
    assert asdict(configuration('deepseek',purpose='numeric'))==original['configuration']
    assert configuration('deepseek',purpose='numeric_v2').policy_sha256!=original['configuration']['policy_sha256']
