"""Bounded schema-failure metadata. Never persist model values or validator messages."""
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from vibradiag.domain.measurement_errors import MeasurementError

LocationName=Literal['action','candidate_id','rationale','outcome','selections','question_id','source_id','quote',
                     'choose_action','ask_information','finish','unrecognized_field','index_out_of_range']
ErrorKind=Literal['missing','extra_forbidden','union_tag_invalid','union_tag_not_found','uuid_parsing','uuid_type',
                  'string_type','string_too_long','string_too_short','list_type','too_long','too_short',
                  'model_type','model_attributes_type','literal_error','json_invalid','value_error','validation_error']
LOCATIONS={'action','candidate_id','rationale','outcome','selections','question_id','source_id','quote','choose_action','ask_information','finish'}
ERROR_KINDS={'missing','extra_forbidden','union_tag_invalid','union_tag_not_found','uuid_parsing','uuid_type',
             'string_type','string_too_long','string_too_short','list_type','too_long','too_short','model_type',
             'model_attributes_type','literal_error','json_invalid','value_error'}


class Issue(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    kind: ErrorKind
    location: Annotated[tuple[LocationName | Annotated[int,Field(strict=True,ge=0,le=20)],...],Field(max_length=6)]


class ValidationDiagnostics(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    version: Literal['model-validation-diagnostics-v1']='model-validation-diagnostics-v1'
    top_level_type: Literal['object','array','string','number','boolean','null','invalid_json']
    action_kind: Literal['choose_action','ask_information','finish','other','absent']
    known_fields: Annotated[tuple[Literal['action','candidate_id','rationale','outcome','selections','question_id','source_id','quote'],...],Field(max_length=8)]
    unknown_field_count: Annotated[int,Field(strict=True,ge=0)]
    error_count: Annotated[int,Field(strict=True,ge=1)]
    issues: Annotated[tuple[Issue,...],Field(min_length=1,max_length=8)]
    truncated: bool


def diagnostics(raw, error):
    """Only fixed labels, bounded integer positions and counts leave this function."""
    try:
        value=json.loads(raw)
        shape='object' if isinstance(value,dict) else 'array' if isinstance(value,list) else 'boolean' if isinstance(value,bool) else 'null' if value is None else 'string' if isinstance(value,str) else 'number'
    except (ValueError,TypeError,RecursionError):value=None;shape='invalid_json'
    fields={'action','candidate_id','rationale','outcome','selections','question_id','source_id','quote'}
    known=tuple(sorted(k for k in value if k in fields)) if isinstance(value,dict) else ()
    unknown=sum(k not in fields for k in value) if isinstance(value,dict) else 0
    action=value.get('action') if isinstance(value,dict) else None
    action_kind=action if isinstance(action,str) and action in {'choose_action','ask_information','finish'} else 'other' if isinstance(value,dict) and 'action' in value else 'absent'
    errors=error.errors(include_url=False,include_context=False,include_input=False)
    issues=[];truncated=len(errors)>8
    for item in errors[:8]:
        location=[];path=item['loc'];truncated=truncated or len(path)>6
        for part in path[:6]:
            location.append(part if isinstance(part,str) and part in LOCATIONS else part if type(part) is int and 0<=part<=20 else 'index_out_of_range' if type(part) is int else 'unrecognized_field')
        issues.append(Issue(kind=item['type'] if item['type'] in ERROR_KINDS else 'validation_error',location=tuple(location)))
    return ValidationDiagnostics(top_level_type=shape,action_kind=action_kind,known_fields=known,unknown_field_count=unknown,
                                 error_count=error.error_count(),issues=tuple(issues),truncated=truncated).model_dump(mode='json')


class ModelSchemaError(MeasurementError):
    def __init__(self, raw, error: ValidationError):
        super().__init__('MODEL_OUTPUT_SCHEMA_INVALID','Model output did not pass local policy checks',status=409)
        self.validation_diagnostics=diagnostics(raw,error)
