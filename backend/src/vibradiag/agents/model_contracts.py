from typing import Annotated,Literal,Union
from uuid import UUID
from pydantic import Field,model_validator
from .formal_contracts import Strict,ChooseAction,FinishDecision


class MethodFinishDecision(FinishDecision):
    outcome:Literal['insufficient_evidence']


MethodDecision=Annotated[Union[ChooseAction,MethodFinishDecision],Field(discriminator='action')]


class SelectedQuote(Strict):
    source_id:UUID
    quote:Annotated[str,Field(min_length=1,max_length=400)]


class ExtractiveMethodDraft(Strict):
    outcome:Literal['cited_context','insufficient_evidence']
    selections:Annotated[tuple[SelectedQuote,...],Field(max_length=3)]

    @model_validator(mode='after')
    def selections_match_outcome(self):
        if (self.outcome=='cited_context')!=bool(self.selections):raise ValueError('Cited context requires exact source quotes')
        if len({s.source_id for s in self.selections})!=len(self.selections):raise ValueError('Duplicate selected source')
        return self
