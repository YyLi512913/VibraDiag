"""Validated numerical parameter objects, NOT public tool-run/authorization requests.

T02b must resolve and verify provenance/qualification against server-owned records;
these in-memory checked flags are not authentication or calibration certificates.
"""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from vibradiag.domain.tool_results import ToolInputError

Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Positive = Annotated[Finite, Field(gt=0)]
Nonnegative = Annotated[Finite, Field(ge=0)]
Text = Annotated[str, Field(min_length=1, max_length=2000)]


class Parameters(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, validate_default=True,
                              revalidate_instances='always', str_strip_whitespace=True)


class SourceReference(Parameters):
    kind: Literal['user_statement', 'manufacturer_document', 'dataset_metadata', 'synthetic_exact']
    reference: Text
    version: Annotated[str, Field(min_length=1, max_length=128)]


class TargetReference(Parameters):
    measurement_revision_id: UUID
    component_id: UUID
    bearing_revision_id: UUID


class GeometryUncertainty(Parameters):
    rolling_element_diameter_absolute: Nonnegative
    pitch_diameter_absolute: Nonnegative
    contact_angle_deg_absolute: Nonnegative
    source: SourceReference


class BearingGeometry(Parameters):
    rolling_elements: Annotated[int, Field(strict=True, ge=3, le=64)]
    rolling_element_diameter: Positive
    pitch_diameter: Positive
    length_unit: Literal['mm', 'm']
    contact_angle_deg: Annotated[Finite, Field(ge=0, lt=90)]
    bearing_design: Literal['single_row_ball', 'double_row_ball', 'roller', 'unknown'] = 'unknown'
    source: SourceReference | None = None
    checked: bool = Field(False, strict=True)
    uncertainty: GeometryUncertainty | None = None

    @model_validator(mode='after')
    def bounds(self):
        if self.rolling_element_diameter >= self.pitch_diameter:
            raise ValueError('rolling element diameter must be smaller than pitch diameter')
        u = self.uncertainty
        if u:
            if self.rolling_element_diameter - u.rolling_element_diameter_absolute <= 0 or self.pitch_diameter - u.pitch_diameter_absolute <= 0:
                raise ValueError('diameter uncertainty interval must stay positive')
            if self.rolling_element_diameter + u.rolling_element_diameter_absolute >= self.pitch_diameter - u.pitch_diameter_absolute:
                raise ValueError('diameter intervals must preserve d < D')
            if self.contact_angle_deg - u.contact_angle_deg_absolute < 0 or self.contact_angle_deg + u.contact_angle_deg_absolute >= 90:
                raise ValueError('angle uncertainty interval must stay within [0, 90) degrees')
        return self


class CoefficientValues(Parameters):
    FTF: Positive
    BPFO: Positive
    BPFI: Positive
    BSF: Positive


class CoefficientUncertainty(Parameters):
    FTF: Nonnegative
    BPFO: Nonnegative
    BPFI: Nonnegative
    BSF: Nonnegative
    source: SourceReference


class BearingCoefficients(Parameters):
    values: CoefficientValues
    unit: Literal['shaft_order']
    bsf_definition: Literal['spin', 'twice_spin', 'unknown']
    rotation_assumption: Literal['inner_ring_rotates_outer_fixed', 'other', 'unknown']
    applicability: Text
    source: SourceReference | None = None
    checked: bool = Field(False, strict=True)
    uncertainty: CoefficientUncertainty | None = None

    @model_validator(mode='after')
    def intervals_positive(self):
        if self.uncertainty:
            for name in ('FTF', 'BPFO', 'BPFI', 'BSF'):
                if getattr(self.uncertainty, name) >= getattr(self.values, name):
                    raise ValueError('coefficient uncertainty interval must stay positive')
        return self


class FrequencyConditions(Parameters):
    target: TargetReference | None = None
    rpm: Positive | None = None
    rpm_basis: Literal['measured', 'estimated', 'rated_reference', 'synthetic_exact', 'unknown'] = 'unknown'
    rpm_source: SourceReference | None = None
    rpm_checked: bool = Field(False, strict=True)
    rpm_uncertainty_rpm: Nonnegative | None = None
    rpm_uncertainty_source: SourceReference | None = None
    speed_condition: Literal['constant_steady_checked', 'variable', 'not_established'] = 'not_established'
    rotation: Literal['inner_ring_rotates_outer_fixed', 'both_rings_rotate', 'outer_ring_rotates_inner_fixed', 'unknown'] = 'unknown'
    parameters_conflict: bool = Field(False, strict=True)

    @model_validator(mode='after')
    def interval_positive(self):
        if self.rpm is not None and self.rpm_uncertainty_rpm is not None and self.rpm_uncertainty_rpm >= self.rpm:
            raise ValueError('rpm uncertainty interval must stay positive')
        return self


class BearingFrequencyParameters(Parameters):
    parameterization: Literal['geometry', 'coefficients']
    geometry: BearingGeometry | None = None
    coefficients: BearingCoefficients | None = None
    conditions: FrequencyConditions

    @model_validator(mode='after')
    def explicit_path(self):
        if self.parameterization == 'geometry' and self.coefficients is not None or self.parameterization == 'coefficients' and self.geometry is not None:
            raise ValueError('supply only the explicitly selected parameterization')
        return self


class AcquisitionBandwidth(Parameters):
    low_hz: Nonnegative
    high_hz: Positive
    source: SourceReference | None = None
    checked: bool = Field(False, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.low_hz >= self.high_hz:
            raise ValueError('acquisition bandwidth must be ordered')
        return self


class NumericQualification(Parameters):
    profile_id: UUID
    numeric_configuration_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    manifest_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    source: SourceReference


class EnvelopeParameters(Parameters):
    sampling_rate_hz: Positive
    acceleration_unit: Literal['g', 'm/s2']
    band_low_hz: Positive
    band_high_hz: Positive
    modulation_low_hz: Positive
    modulation_high_hz: Positive
    selection_reason: Text
    execution_mode: Literal['exploratory', 'diagnostic_evidence'] = 'exploratory'
    acquisition: AcquisitionBandwidth | None = None
    qualification: NumericQualification | None = None

    @model_validator(mode='after')
    def valid_band(self):
        if not 0 < self.band_low_hz < self.band_high_hz < self.sampling_rate_hz / 2:
            raise ValueError('band must satisfy 0 < low < high < Nyquist')
        if not self.modulation_low_hz < self.modulation_high_hz < self.sampling_rate_hz / 2:
            raise ValueError('modulation range must be positive, ordered and below Nyquist')
        if self.modulation_high_hz > (self.band_high_hz - self.band_low_hz) / 2:
            raise ValueError('modulation upper bound exceeds half the carrier bandwidth')
        return self


def validate_parameters(model, value):
    try:
        return model.model_validate(value)
    except ValidationError as error:
        first = error.errors()[0]
        location = '.'.join(str(item) for item in first['loc']) or 'parameters'
        raise ToolInputError('TOOL_INPUT_INVALID', 'Invalid numerical parameters: ' + first['msg'], location) from None
