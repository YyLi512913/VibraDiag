import json
import pytest
from pydantic import TypeAdapter,ValidationError
from vibradiag.agents.formal_contracts import AgentDecision
from vibradiag.agents.model_contracts import ExtractiveMethodDraft
from vibradiag.agents.model_validation import ModelSchemaError,ValidationDiagnostics


def failure(raw,adapter=None):
    adapter=adapter or TypeAdapter(AgentDecision)
    with pytest.raises(ValidationError) as caught:adapter.validate_json(raw)
    return ModelSchemaError(raw,caught.value)


@pytest.mark.parametrize('raw,kind,field',[
    ('{"action":"choose_action","candidate_id":"bad-private-value"}','uuid_parsing','candidate_id'),
    ('{"action":"choose_action","candidate_id":"00000000-0000-0000-0000-000000000001"}','missing','rationale'),
    ('{"action":"PRIVATE_MODEL_VALUE"}','union_tag_invalid',None),
    ('{"candidate_id":"PRIVATE_MODEL_VALUE"}','union_tag_not_found',None),
    ('{"action":"choose_action", PRIVATE_MODEL_VALUE','json_invalid',None),
])
def test_schema_errors_are_useful_without_persisting_model_values(raw,kind,field):
    error=failure(raw);data=error.validation_diagnostics
    assert any(i['kind']==kind for i in data['issues'])
    if field:assert any(field in i['location'] for i in data['issues'])
    encoded=json.dumps(data)
    assert 'PRIVATE_MODEL_VALUE' not in encoded and 'bad-private-value' not in encoded
    assert set(data)=={'version','top_level_type','action_kind','known_fields','unknown_field_count','error_count','issues','truncated'}
    assert error.code=='MODEL_OUTPUT_SCHEMA_INVALID' and str(error)=='Model output did not pass local policy checks'
    ValidationDiagnostics.model_validate(data)


def test_unknown_extra_keys_and_nested_values_cannot_leak_through_locations():
    raw=json.dumps(dict(outcome='insufficient_evidence',selections=[],**{'PRIVATE_FIELD_'+str(i):'PRIVATE_VALUE' for i in range(64)}))
    data=failure(raw,TypeAdapter(ExtractiveMethodDraft)).validation_diagnostics
    assert data['unknown_field_count']==64 and data['error_count']==64 and data['truncated']
    assert len(data['issues'])==8 and len(json.dumps(data).encode())<4096
    assert all(i['location']==['unrecognized_field'] for i in data['issues'])
    assert 'PRIVATE_' not in json.dumps(data)


@pytest.mark.parametrize('raw,shape',[('[]','array'),('"PRIVATE_VALUE"','string'),('true','boolean'),('12','number'),('null','null')])
def test_top_level_shape_does_not_include_body(raw,shape):
    data=failure(raw).validation_diagnostics
    assert data['top_level_type']==shape and data['known_fields']==[] and data['action_kind']=='absent'
    assert 'PRIVATE_VALUE' not in json.dumps(data)
