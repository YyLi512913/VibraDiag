"""Engineering declarations; identity and checked state are supplied by the service."""
from typing import Annotated, ClassVar, Literal
from uuid import UUID
from pydantic import Field, model_validator
from vibradiag.schemas.tools import (
    Parameters, BearingGeometry, BearingCoefficients, SourceReference,
    AcquisitionBandwidth, Nonnegative, Positive, Text,
)


class GeometryDeclaration(BearingGeometry):
    checked: ClassVar[bool] = False
    source: SourceReference


class CoefficientsDeclaration(BearingCoefficients):
    checked: ClassVar[bool] = False
    source: SourceReference


class CreateComponent(Parameters):
    asset_id: UUID
    component_code: Annotated[str, Field(min_length=1, max_length=128)]
    name: Annotated[str, Field(min_length=1, max_length=256)]


class CreateBearingRevision(Parameters):
    component_id: UUID
    expected_current_revision_id: UUID | None
    parameterization: Literal['geometry', 'coefficients']
    geometry: GeometryDeclaration | None = None
    coefficients: CoefficientsDeclaration | None = None
    reason: Text

    @model_validator(mode='after')
    def selected_parameters(self):
        if (self.geometry is not None) != (self.parameterization == 'geometry') or (self.coefficients is not None) != (self.parameterization == 'coefficients'):
            raise ValueError('Supply exactly the selected parameterization')
        return self


class ConditionsDeclaration(Parameters):
    rpm_basis: Literal['measured', 'estimated', 'rated_reference', 'synthetic_exact', 'unknown'] = 'unknown'
    rpm_source: SourceReference | None = None
    rpm_uncertainty_rpm: Nonnegative | None = None
    rpm_uncertainty_source: SourceReference | None = None
    speed_condition: Literal['constant_steady_checked', 'variable', 'not_established'] = 'not_established'
    rotation: Literal['inner_ring_rotates_outer_fixed', 'both_rings_rotate', 'outer_ring_rotates_inner_fixed', 'unknown'] = 'unknown'
    parameters_conflict: bool = Field(False, strict=True)


class BandwidthDeclaration(Parameters):
    low_hz: Nonnegative
    high_hz: Positive
    source: SourceReference

    @model_validator(mode='after')
    def ordered(self):
        AcquisitionBandwidth.model_validate(self.model_dump())
        return self


class CreateDiagnosticContext(Parameters):
    measurement_revision_id: UUID
    component_id: UUID
    bearing_revision_id: UUID
    conditions: ConditionsDeclaration = ConditionsDeclaration()
    acquisition: BandwidthDeclaration | None = None
    source_reference: SourceReference
    reason: Text
