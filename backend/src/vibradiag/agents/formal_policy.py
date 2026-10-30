"""Pure M3-T01 contract checks. Runtime authorization, CAS and persistence belong to T02+."""
from hashlib import sha256
import json
from pydantic import TypeAdapter

from vibradiag.agents.formal_contracts import (ExecutionBudget,Reservation,ChooseAction,AskInformation,
    AgentDecision,TaskScope,CandidateAction,CandidateQuestion,ExploratoryDraft,ResolvedReference)


class PolicyStop(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def logical_call_key(task_id,input_fingerprint,action_snapshot):
    """Only call with server-resolved immutable snapshots, never raw model argument text."""
    payload={'task_id':str(task_id),'input_fingerprint':input_fingerprint,'action':action_snapshot}
    return sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def reserve_call(budget,kind,key):
    budget=ExecutionBudget.model_validate(budget)
    if budget.active_ms>=120000:raise PolicyStop('ACTIVE_TIME_EXHAUSTED')
    existing=next((item for item in budget.reservations if item.key==key),None)
    if existing:
        if existing.kind!=kind:raise PolicyStop('LOGICAL_CALL_CONFLICT')
        return budget,False
    limit={'model':8,'tool':20}.get(kind)
    if limit is None:raise PolicyStop('CALL_KIND_NOT_ALLOWED')
    if sum(item.kind==kind for item in budget.reservations)>=limit:raise PolicyStop('CALL_BUDGET_EXHAUSTED')
    return ExecutionBudget(active_ms=budget.active_ms,reservations=budget.reservations+(Reservation(kind=kind,key=key),)),True


def reserve_retry(budget,key):
    budget=ExecutionBudget.model_validate(budget)
    if budget.active_ms>=120000:raise PolicyStop('ACTIVE_TIME_EXHAUSTED')
    existing=next((item for item in budget.reservations if item.key==key),None)
    if existing is None:raise PolicyStop('ORIGINAL_CALL_NOT_RESERVED')
    if existing.retries_used>=1:raise PolicyStop('RETRY_BUDGET_EXHAUSTED')
    replacement=Reservation(kind=existing.kind,key=key,retries_used=1)
    return ExecutionBudget(active_ms=budget.active_ms,reservations=tuple(replacement if item.key==key else item for item in budget.reservations))


def charge_active_time(budget,elapsed_ms):
    budget=ExecutionBudget.model_validate(budget)
    if isinstance(elapsed_ms,bool) or not isinstance(elapsed_ms,int) or elapsed_ms<0:
        raise PolicyStop('INVALID_ELAPSED_TIME')
    # Saturation preserves a stop state instead of losing a spent budget on validation failure.
    return ExecutionBudget(active_ms=min(120000,budget.active_ms+elapsed_ms),reservations=budget.reservations)


def validate_decision(decision,scope,candidates,questions):
    decision=TypeAdapter(AgentDecision).validate_python(decision)
    scope=TaskScope.model_validate(scope)
    candidates=tuple(CandidateAction.model_validate(item) for item in candidates)
    questions=tuple(CandidateQuestion.model_validate(item) for item in questions)
    if isinstance(decision,ChooseAction):
        options=[item for item in candidates if item.id==decision.candidate_id]
        if len(options)!=1:raise PolicyStop('UNKNOWN_OR_DUPLICATE_CANDIDATE')
        item=options[0]
        if (item.task_id!=scope.task_id or item.organization_id!=scope.organization_id
                or not set(item.measurement_revision_ids).issubset(scope.measurement_revision_ids)):
            raise PolicyStop('CANDIDATE_SCOPE_MISMATCH')
    elif isinstance(decision,AskInformation):
        options=[item for item in questions if item.id==decision.question_id]
        if len(options)!=1:raise PolicyStop('UNKNOWN_OR_DUPLICATE_QUESTION')
        item=options[0]
        if item.task_id!=scope.task_id or item.organization_id!=scope.organization_id:
            raise PolicyStop('QUESTION_SCOPE_MISMATCH')
    return decision


def validate_draft_references(draft,scope,resolved):
    draft=ExploratoryDraft.model_validate(draft)
    scope=TaskScope.model_validate(scope)
    references={}
    for item in resolved:
        item=ResolvedReference.model_validate(item)
        key=(item.kind,item.id)
        if key in references:raise PolicyStop('DUPLICATE_RESOLVED_REFERENCE')
        references[key]=item
    for observation in draft.observations:
        for citation in observation.citations:
            kinds={'numeric_fact':{'basic_analysis','fact'},
                'exploratory_observation':{'tool_run','fact','evidence'},'method_explanation':{'knowledge_chunk'}}
            if citation.kind not in kinds[observation.claim_kind]:raise PolicyStop('REFERENCE_ROLE_NOT_ALLOWED')
            item=references.get((citation.kind,citation.id))
            if item is None:raise PolicyStop('UNRESOLVED_REFERENCE')
            if item.organization_id!=scope.organization_id or not item.current_and_valid:
                raise PolicyStop('REFERENCE_UNAVAILABLE')
            if item.permitted_claim!=observation.claim_kind:
                raise PolicyStop('REFERENCE_CLAIM_KIND_MISMATCH')
            roots=set(item.measurement_revision_ids)
            if not roots.issubset(scope.measurement_revision_ids):raise PolicyStop('REFERENCE_INPUT_SCOPE_MISMATCH')
            if observation.claim_kind!='method_explanation' and not roots:
                raise PolicyStop('MEASUREMENT_REFERENCE_REQUIRED')
            if observation.claim_kind=='method_explanation' and roots:
                raise PolicyStop('KNOWLEDGE_CANNOT_ESTABLISH_MEASUREMENT_ROOT')
    # This validates provenance/structure only; it is not semantic claim verification or publication approval.
    return draft
