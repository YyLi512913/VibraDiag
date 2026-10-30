"""Public requests reference controlled records; no trusted flags or arbitrary overrides."""
from typing import Annotated, Literal, Union
from uuid import UUID
from pydantic import Field
from vibradiag.schemas.tools import Parameters, Positive, Text


class EnvelopeProfileDeclaration(Parameters):
    asset_id: UUID
    sampling_rate_hz: Positive
    band_low_hz: Positive
    band_high_hz: Positive
    modulation_low_hz: Positive
    modulation_high_hz: Positive
    selection_reason: Text


class RunBase(Parameters):
    measurement_revision_id: UUID
    confirmation_id: UUID
    retry_of: UUID | None = None


class EnvelopeRunRequest(RunBase):
    tool_name: Literal['analyze_envelope']
    profile_id: UUID
    context_id: UUID | None = None
    mode: Literal['exploratory', 'diagnostic_evidence'] = 'exploratory'


class FrequencyRunRequest(RunBase):
    tool_name: Literal['calculate_bearing_frequencies']
    context_id: UUID


class MatchingRunRequest(RunBase):
    tool_name: Literal['match_frequency_evidence']
    context_id: UUID
    envelope_run_id: UUID
    frequency_run_id: UUID
    basic_analysis_id: UUID
    config_profile: Literal['matching-v1'] = 'matching-v1'


class ComparisonRunRequest(RunBase):
    tool_name: Literal['compare_measurements']
    declaration_id: UUID
    before_confirmation_id: UUID


ToolRunRequest = Annotated[Union[EnvelopeRunRequest, FrequencyRunRequest, MatchingRunRequest, ComparisonRunRequest], Field(discriminator='tool_name')]
