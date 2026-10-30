from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class MeasurementMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)
    sampling_rate_hz: float = Field(gt=0, strict=True)
    acceleration_unit: Literal['g', 'm/s2']
    asset_id: UUID | None = None
    measurement_point_id: UUID | None = None
    direction: Literal['horizontal_radial', 'vertical_radial', 'axial', 'other', 'unknown'] = 'unknown'
    direction_note: str | None = Field(None, max_length=500)
    rpm: float | None = Field(None, gt=0, strict=True)
    load_value: float | None = Field(None, strict=True)
    load_unit: str | None = Field(None, min_length=1, max_length=64)
    load_note: str | None = Field(None, max_length=1000)
    acquired_at: AwareDatetime | None = None
    source_type: Literal['public_dataset', 'synthetic', 'field_upload', 'unknown'] = 'unknown'
    source_note: str | None = Field(None, max_length=2000)

    @model_validator(mode='after')
    def validate_relationships(self):
        if self.measurement_point_id and not self.asset_id:
            raise ValueError('measurement_point_id requires asset_id')
        if self.direction == 'other' and not self.direction_note:
            raise ValueError('direction=other requires direction_note')
        if (self.load_value is None) != (self.load_unit is None):
            raise ValueError('load_value and load_unit must be supplied together')
        return self


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    metadata: MeasurementMetadata
    reason: str = Field(min_length=1, max_length=1000, pattern=r'\S')
    expected_current_revision_id: UUID
    row_version: int = Field(gt=0, strict=True)


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision_id: UUID
