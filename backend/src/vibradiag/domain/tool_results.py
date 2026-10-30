"""Numerical outcomes, without HTTP, persistence, permissions or invented evidence IDs."""
from dataclasses import dataclass, field
from typing import Literal
import numpy as np


class ToolInputError(ValueError):
    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.code, self.field = code, field


class NumericRangeError(ArithmeticError):
    pass


@dataclass(frozen=True)
class NumericToolResult:
    tool_name: str
    tool_version: str
    status: Literal['ok', 'unavailable', 'failed']
    summary: dict = field(default_factory=dict)
    arrays: dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    missing_requirements: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    error_code: str | None = None

    def metadata(self) -> dict:
        """JSON-safe summary only. Large arrays are separate numerical products."""
        return {'tool_name': self.tool_name, 'tool_version': self.tool_version, 'status': self.status,
                'summary': self.summary, 'missing_requirements': list(self.missing_requirements),
                'limitations': list(self.limitations), 'error_code': self.error_code, 'retryable': False,
                'array_shapes': {key: list(value.shape) for key, value in self.arrays.items()}}
