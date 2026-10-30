from typing import Annotated
from pydantic import Field
from vibradiag.agents.formal_contracts import Strict


class CancelAgentTask(Strict):
    expected_version: Annotated[int,Field(strict=True,ge=1)]
