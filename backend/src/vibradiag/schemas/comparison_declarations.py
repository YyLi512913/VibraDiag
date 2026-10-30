"""Controlled engineering declarations; checked flags and hashes are server-owned."""
from typing import ClassVar
from uuid import UUID
from pydantic import Field
from vibradiag.schemas.tools import Parameters, SourceReference, Text
from vibradiag.schemas.comparison import CaptureProvenance, CaptureTiming, LoadDeclaration, LoadTolerance, MetricUncertainty


class ProvenanceDeclaration(CaptureProvenance):
    checked: ClassVar[bool] = False


class TimingDeclaration(CaptureTiming):
    checked: ClassVar[bool] = False


class LoadStatement(LoadDeclaration):
    checked: ClassVar[bool] = False


class ToleranceDeclaration(LoadTolerance):
    checked: ClassVar[bool] = False


class UncertaintyDeclaration(MetricUncertainty):
    checked: ClassVar[bool] = False


class ChainConfiguration(Parameters):
    sensor_id: Text
    channel_id: Text
    daq_id: Text
    mounting_reference: Text
    calibration_reference: Text
    acquisition_configuration: Text


class ChainDeclaration(Parameters):
    configuration: ChainConfiguration
    source: SourceReference


class ComparisonSideDeclaration(Parameters):
    provenance: ProvenanceDeclaration = ProvenanceDeclaration()
    timing: TimingDeclaration = TimingDeclaration()
    load: LoadStatement = LoadStatement()
    measurement_chain: ChainDeclaration | None = None
    metric_uncertainties: dict[str, UncertaintyDeclaration] = Field(default_factory=dict, max_length=10)


class CreateComparisonDeclaration(Parameters):
    before_context_id: UUID
    after_context_id: UUID
    before_basic_id: UUID
    after_basic_id: UUID
    before_envelope_id: UUID | None = None
    after_envelope_id: UUID | None = None
    before: ComparisonSideDeclaration = ComparisonSideDeclaration()
    after: ComparisonSideDeclaration = ComparisonSideDeclaration()
    order_source: SourceReference | None = None
    load_tolerance: ToleranceDeclaration | None = None
    reason: Text
