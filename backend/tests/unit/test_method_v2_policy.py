from dataclasses import asdict
import json
from uuid import uuid4
import httpx
import pytest
from pydantic import TypeAdapter,ValidationError
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.agents.model_contracts import MethodDecision
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.llm.formal_gateway import configuration,Gateway,phase_policy,prompt_version,METHOD_V2_POLICY_SHA256


def test_original_method_and_numeric_policy_fingerprints_remain_unchanged():
    for path,purpose in [('m3-t05c3a-method-diagnostics.json','method'),('m3-t05c1-scoped-regression.json','numeric_v2'),('m3-t05b-feedback-comparison.json','numeric')]:
        receipt=json.loads((PROJECT_ROOT/'experiments'/path).read_text(encoding='utf8'))
        assert asdict(configuration('deepseek',purpose=purpose))==receipt['configuration']
    assert configuration('deepseek',purpose='method_v2').policy_sha256==METHOD_V2_POLICY_SHA256
    assert phase_policy('decision_method_v2')==phase_policy('extractive_method_v2')==METHOD_V2_POLICY_SHA256
    assert prompt_version('decision_method_v2')=='formal-method-json-v3'


@pytest.mark.parametrize('extra',[dict(arguments={}),dict(outcome=None),dict(tool='search_published_knowledge')])
def test_additional_fields_are_rejected_not_removed(extra):
    with pytest.raises(ValidationError):TypeAdapter(MethodDecision).validate_python(dict(action='choose_action',candidate_id=uuid4(),rationale='Retrieve',**extra))


@pytest.mark.parametrize('output',[dict(action='ask_information',question_id=uuid4(),rationale='Ask'),dict(action='finish',outcome='analysis_only',rationale='Finish')])
def test_method_specific_schema_does_not_offer_unsupported_actions(output):
    with pytest.raises(ValidationError):TypeAdapter(MethodDecision).validate_python(output)


def test_cross_policy_phase_fails_before_any_network():
    calls=[]
    def transport(request):calls.append(request);raise AssertionError('No network expected')
    for cfg,phase in [(configuration('deepseek'),'decision_method_v2'),(configuration('deepseek',purpose='method_v2'),'decision')]:
        with pytest.raises(MeasurementError) as caught:Gateway(cfg,transport=httpx.MockTransport(transport)).invoke(phase,{}, {})
        assert caught.value.code=='MODEL_POLICY_MISMATCH'
    assert calls==[]
