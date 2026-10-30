"""M3-T01 DTOs only. None of these objects grants resource access or publishes a report."""
from typing import Annotated, Literal, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

Text=Annotated[str,Field(min_length=1,max_length=2000)]
CallKey=Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
Goal=Literal['inspect_signal','bearing_observations','compare_measurements','explain_method']
TaskStatus=Literal['queued','running','waiting_information','completed','completed_degraded','refused',
    'failed','cancelled','interrupted','superseded','budget_exhausted','timed_out']
ReferenceKind=Literal['basic_analysis','tool_run','fact','evidence','knowledge_chunk']


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,str_strip_whitespace=True,validate_default=True,
        revalidate_instances='always')


class CreateAgentTask(Strict):
    measurement_revision_id: UUID | None = None
    goal: Goal
    description: Annotated[Text,Field(max_length=4000)]
    comparison_declaration_id: UUID | None = None

    @model_validator(mode='after')
    def task_inputs(self):
        if self.goal!='explain_method' and self.measurement_revision_id is None:
            raise ValueError('a measurement task requires an explicit input revision')
        if (self.goal=='compare_measurements')!=(self.comparison_declaration_id is not None):
            raise ValueError('only comparison tasks require a controlled declaration')
        return self


class ResumeAgentTask(Strict):
    expected_version: Annotated[int,Field(strict=True,ge=1)]
    question_id: UUID
    answer: Annotated[Text,Field(max_length=4000)]


class ChooseAction(Strict):
    action: Literal['choose_action']
    candidate_id: UUID
    rationale: Annotated[Text,Field(max_length=500)]


class AskInformation(Strict):
    action: Literal['ask_information']
    question_id: UUID
    rationale: Annotated[Text,Field(max_length=500)]


class FinishDecision(Strict):
    action: Literal['finish']
    outcome: Literal['analysis_only','insufficient_evidence']
    rationale: Annotated[Text,Field(max_length=500)]


AgentDecision=Annotated[Union[ChooseAction,AskInformation,FinishDecision],Field(discriminator='action')]


class Citation(Strict):
    kind: ReferenceKind
    id: UUID


class DraftObservation(Strict):
    claim_kind: Literal['numeric_fact','exploratory_observation','method_explanation']
    text: Annotated[Text,Field(max_length=500)]
    citations: Annotated[tuple[Citation,...],Field(min_length=1,max_length=10)]


class ExploratoryDraft(Strict):
    contract_version: Literal['m3-exploratory-draft-v1']='m3-exploratory-draft-v1'
    status: Literal['analysis_only','insufficient_evidence']
    summary: Annotated[Text,Field(max_length=1000)]
    observations: Annotated[tuple[DraftObservation,...],Field(max_length=20)]
    missing_requirements: Annotated[tuple[Text,...],Field(max_length=20)]
    limitations: Annotated[tuple[Text,...],Field(min_length=1,max_length=20)]
    next_steps: Annotated[tuple[Text,...],Field(max_length=10)]
    publication_state: Literal['unpublished_draft']='unpublished_draft'

    @model_validator(mode='after')
    def useful_or_refused(self):
        if self.status=='analysis_only' and not self.observations:
            raise ValueError('analysis-only draft requires a cited observation')
        if self.status=='insufficient_evidence' and not self.missing_requirements:
            raise ValueError('insufficient-evidence draft must name missing requirements')
        return self


# Internal server-resolved DTOs. Do not expose them as public request bodies.
class TaskScope(Strict):
    task_id: UUID
    organization_id: UUID
    measurement_revision_ids: Annotated[tuple[UUID,...],Field(max_length=2)]

    @model_validator(mode='after')
    def unique_inputs(self):
        if len(set(self.measurement_revision_ids))!=len(self.measurement_revision_ids):
            raise ValueError('task input revisions must be unique')
        return self


class ResolvedReference(Strict):
    kind: ReferenceKind
    id: UUID
    organization_id: UUID
    measurement_revision_ids: tuple[UUID,...]
    permitted_claim: Literal['numeric_fact','exploratory_observation','method_explanation']
    current_and_valid: bool=Field(strict=True)


class CandidateAction(Strict):
    id: UUID
    task_id: UUID
    organization_id: UUID
    measurement_revision_ids: tuple[UUID,...]
    kind: Literal['read_basic_analysis','analyze_basic','analyze_envelope','calculate_bearing_frequencies',
        'match_frequency_evidence','compare_measurements','search_published_knowledge']
    logical_call_key: CallKey

    @model_validator(mode='after')
    def input_scope(self):
        count=len(self.measurement_revision_ids)
        if len(set(self.measurement_revision_ids))!=count:
            raise ValueError('candidate input roots must be unique')
        if self.kind=='search_published_knowledge' and count!=0:
            raise ValueError('knowledge candidate does not establish a measurement root')
        if self.kind!='search_published_knowledge' and count!=(2 if self.kind=='compare_measurements' else 1):
            raise ValueError('candidate must bind the correct number of measurement roots')
        return self


class CandidateQuestion(Strict):
    id: UUID
    task_id: UUID
    organization_id: UUID
    code: Literal['input_confirmation_required','measurement_parameters_required','target_context_required',
        'profile_required','comparison_conditions_required','source_uncertainty_required','task_clarification_required']


class Reservation(Strict):
    key: CallKey
    kind: Literal['model','tool']
    retries_used: Annotated[int,Field(strict=True,ge=0,le=1)]=0


class ExecutionBudget(Strict):
    policy_version: Literal['agent-budget-v1']='agent-budget-v1'
    active_ms: Annotated[int,Field(strict=True,ge=0,le=120000)]=0
    reservations: tuple[Reservation,...]=()

    @model_validator(mode='after')
    def bounded_reservations(self):
        keys=[item.key for item in self.reservations]
        if len(keys)!=len(set(keys)):raise ValueError('logical call keys must be unique')
        if sum(item.kind=='model' for item in self.reservations)>8:
            raise ValueError('model decision budget exceeded')
        if sum(item.kind=='tool' for item in self.reservations)>20:
            raise ValueError('logical tool budget exceeded')
        return self
