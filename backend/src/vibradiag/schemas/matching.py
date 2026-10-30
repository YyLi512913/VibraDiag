"""Internal matching inputs; reference declarations do not authenticate resources."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import Field
from vibradiag.schemas.tools import Parameters, Nonnegative, SourceReference


class MatchingSource(Parameters):
    execution_id: UUID
    measurement_revision_id: UUID
    context_id: UUID
    input_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class MatchingParameters(Parameters):
    envelope_source: MatchingSource
    frequency_source: MatchingSource
    basic_source: MatchingSource | None = None
    execution_mode: Literal['exploratory', 'diagnostic_evidence'] = 'exploratory'
    slip_allowance_relative: Annotated[Nonnegative, Field(le=.05)] = .01
    slip_source: SourceReference = SourceReference(kind='user_statement',
        reference='Developer candidate allowance; not an industrial standard or verified physical slip bound', version='matching-v1')
