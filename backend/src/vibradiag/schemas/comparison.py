"""Internal comparison snapshots, never a public self-attestation request.

The future service must derive these from authorized immutable records and
engineering declarations. checked/source fields are not proof of calibration.
"""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import ConfigDict, Field, model_validator
from vibradiag.schemas.measurements import MeasurementMetadata
from vibradiag.schemas.tools import (
    Parameters, SourceReference, FrequencyConditions, AcquisitionBandwidth,
    Nonnegative,
)

Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class ComparisonMetadata(MeasurementMetadata):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, frozen=True,
                              revalidate_instances='always', str_strip_whitespace=True)


class CaptureProvenance(Parameters):
    acquisition_id: Identifier | None = None
    root_acquisition_refs: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()
    source: SourceReference | None = None
    checked: bool = Field(False, strict=True)

    @model_validator(mode='after')
    def unique_roots(self):
        if len(set(self.root_acquisition_refs)) != len(self.root_acquisition_refs):
            raise ValueError('Acquisition roots must be unique')
        return self


class MeasurementChain(Parameters):
    configuration_sha256: Digest
    source: SourceReference | None = None
    checked: bool = Field(False, strict=True)


class CaptureTiming(Parameters):
    time_reference: Literal['start', 'unknown'] = 'unknown'
    source: SourceReference | None = None
    checked: bool = Field(False, strict=True)


class LoadDeclaration(Parameters):
    quantity: Literal['torque', 'force', 'power', 'relative_load', 'unknown'] = 'unknown'
    reference: Identifier | None = None
    condition: Literal['constant_steady_checked', 'variable', 'not_established'] = 'not_established'
    source: SourceReference | None = None
    checked: bool = Field(False, strict=True)


class LoadTolerance(Parameters):
    absolute: Nonnegative
    unit: Annotated[str, Field(min_length=1, max_length=64)]
    quantity: Literal['torque', 'force', 'power', 'relative_load']
    reference: Identifier
    source: SourceReference
    checked: bool = Field(False, strict=True)


class MetricUncertainty(Parameters):
    absolute: Nonnegative
    unit: Literal['m/s2', '1']
    source: SourceReference
    checked: bool = Field(False, strict=True)

    @model_validator(mode='after')
    def exact_zero(self):
        if self.absolute==0 and self.source.kind!='synthetic_exact':
            raise ValueError('Zero metric uncertainty requires an exact synthetic source')
        return self


METRICS = ('mean', 'rms', 'ac_rms', 'absolute_peak', 'peak_to_peak', 'crest_factor', 'pearson_kurtosis',
           'envelope_mean', 'envelope_rms', 'envelope_peak')


class ComparisonCapture(Parameters):
    organization_id: UUID
    measurement_id: UUID
    measurement_revision_id: UUID
    file_object_id: UUID
    input_sha256: Digest
    normalized_signal_sha256: Digest | None = None
    basic_execution_id: UUID
    envelope_execution_id: UUID | None = None
    metadata: ComparisonMetadata
    provenance: CaptureProvenance = CaptureProvenance()
    timing: CaptureTiming = CaptureTiming()
    speed: FrequencyConditions = FrequencyConditions()
    acquisition: AcquisitionBandwidth | None = None
    measurement_chain: MeasurementChain | None = None
    load: LoadDeclaration = LoadDeclaration()
    metric_uncertainties: dict[str, MetricUncertainty] = Field(default_factory=dict, max_length=10)

    @model_validator(mode='after')
    def consistent_snapshot(self):
        if self.envelope_execution_id==self.basic_execution_id:
            raise ValueError('Basic and envelope executions must be distinct')
        target = self.speed.target
        if target and target.measurement_revision_id != self.measurement_revision_id:
            raise ValueError('Target belongs to another input revision')
        if self.speed.rpm is not None and self.speed.rpm != self.metadata.rpm:
            raise ValueError('Speed must reference the metadata rpm, not override it')
        if any(name not in METRICS for name in self.metric_uncertainties):
            raise ValueError('Unsupported metric uncertainty key')
        for name, uncertainty in self.metric_uncertainties.items():
            unit = '1' if name in {'crest_factor', 'pearson_kurtosis'} else 'm/s2'
            if uncertainty.unit != unit:
                raise ValueError('Metric uncertainty unit does not match the metric')
        sources = [self.provenance.source, self.timing.source, self.load.source,
                   self.speed.rpm_source, self.speed.rpm_uncertainty_source,
                   self.measurement_chain.source if self.measurement_chain else None,
                   self.acquisition.source if self.acquisition else None]
        sources += [u.source for u in self.metric_uncertainties.values()]
        if self.metadata.source_type != 'synthetic' and any(s and s.kind == 'synthetic_exact' for s in sources):
            raise ValueError('Synthetic exact declarations require synthetic input')
        return self


class ComparisonParameters(Parameters):
    config_profile: Literal['comparison-v1'] = 'comparison-v1'
    load_tolerance: LoadTolerance | None = None
    order_checked: bool = Field(False, strict=True)
    order_source: SourceReference | None = None
