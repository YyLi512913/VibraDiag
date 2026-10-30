"""Content-locked research records and conservative split-leakage checks."""
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.io import loadmat

Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Text = Annotated[str, Field(min_length=1, max_length=2000)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)


class DatasetRecord(Strict):
    record_id: Text
    dataset: Literal['cwru', 'paderborn']
    relative_path: Text
    file_sha256: Digest
    samples_sha256: Digest
    sample_count: Annotated[int, Field(strict=True, ge=1024, le=1000000)]
    channel: Text
    sampling_rate_hz: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)] | None
    sampling_rate_source: Text | None
    unit: Literal['g', 'm/s2'] | None
    unit_source: Text | None
    label: Literal['healthy_reference', 'inner_race', 'outer_race', 'rolling_element', 'unknown']
    label_source: Text
    entity_group: Text
    acquisition_root: Text
    split: Literal['development', 'validation', 'sealed_test']
    previously_used_for_development: bool = Field(strict=True)
    source_url: Text
    license_status: Literal['CC-BY-NC-4.0', 'not_specified_on_reviewed_pages']
    limitations: tuple[Text, ...]

    @model_validator(mode='after')
    def provenance(self):
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ':' in self.relative_path or '\\' in self.relative_path or '..' in path.parts:
            raise ValueError('relative_path must stay inside the research data directory')
        if (self.unit is None) != (self.unit_source is None):
            raise ValueError('unit and its source must be supplied together')
        if (self.sampling_rate_hz is None) != (self.sampling_rate_source is None):
            raise ValueError('sampling rate and its source must be supplied together')
        if self.previously_used_for_development and self.split != 'development':
            raise ValueError('previously inspected data cannot become held-out data')
        return self


class DatasetManifest(Strict):
    schema_version: Literal['public-records-v1']
    purpose: Text
    split_policy: Text
    records: Annotated[tuple[DatasetRecord, ...], Field(min_length=1)]


def vector(values):
    original = np.asarray(values)
    if original.dtype.kind not in 'fiu' or original.ndim not in (1, 2):
        raise ValueError('a real numeric vector is required')
    if original.ndim == 2 and 1 not in original.shape:
        raise ValueError('multichannel matrices cannot be silently flattened')
    result = original.reshape(-1).astype(np.float64, copy=True)
    if not 1024 <= len(result) <= 1000000 or not np.isfinite(result).all():
        raise ValueError('a finite vector of 1024–1000000 samples is required')
    if np.any((original.reshape(-1) != 0) & (result == 0)):
        raise ValueError('float64 conversion underflow')
    result[result == 0] = 0.0
    result.setflags(write=False)
    return result


def samples_sha256(values):
    return sha256(vector(values).astype('<f8', copy=False).tobytes()).hexdigest()


def load_channel(path, dataset, channel):
    """Load a local original MATLAB record. Never infer channel by field position."""
    data = loadmat(path, simplify_cells=True)
    if dataset == 'cwru':
        if channel not in data or not channel.endswith('_DE_time'):
            raise ValueError('explicit CWRU drive-end channel is missing')
        return vector(data[channel]), {'native_unit': None, 'time_axis': None}
    if dataset != 'paderborn' or channel != 'vibration_1':
        raise ValueError('unsupported dataset or channel')
    roots = [value for key, value in data.items() if not key.startswith('__')]
    if len(roots) != 1 or not isinstance(roots[0], dict):
        raise ValueError('ambiguous Paderborn record root')
    channels = roots[0].get('Y')
    if isinstance(channels, dict):
        channels = [channels]
    if not isinstance(channels, (list, np.ndarray)):
        raise ValueError('Paderborn channel metadata is missing')
    matches = [item for item in channels if isinstance(item, dict) and item.get('Name') == channel]
    if len(matches) != 1:
        raise ValueError('explicit Paderborn vibration channel is missing or duplicated')
    selected = matches[0]
    if int(selected.get('DownSampling', 1)) != 1:
        raise ValueError('downsampled channel requires a separately verified rate')
    values = vector(selected['Data'])
    axes = roots[0].get('X')
    if isinstance(axes, dict): axes = [axes]
    axis_index = int(selected['XIndex']) - 1
    if not isinstance(axes, (list, np.ndarray)) or not 0 <= axis_index < len(axes):
        raise ValueError('explicit Paderborn time axis is missing')
    axis = vector(axes[axis_index]['Data'])
    if len(axis) != len(values) or not np.all(np.diff(axis) > 0):
        raise ValueError('channel time axis must be increasing and length matched')
    native_unit = selected.get('Unit')
    return values, {'native_unit': native_unit if isinstance(native_unit, str) else None,
                    'time_axis': axis}


def verified_signal(record, data_root):
    root = Path(data_root).resolve()
    path = (root / record.relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('record path escapes root or is missing')
    if sha256(path.read_bytes()).hexdigest() != record.file_sha256:
        raise ValueError('original file hash mismatch')
    values, metadata = load_channel(path, record.dataset, record.channel)
    if len(values) != record.sample_count or samples_sha256(values) != record.samples_sha256:
        raise ValueError('channel content or sample count mismatch')
    if record.dataset == 'paderborn' and record.unit is not None:
        expected = {'m/s2': {'m/s^2', 'm/s²', 'm/s2'}, 'g': {'g'}}[record.unit]
        if metadata['native_unit'] not in expected:
            raise ValueError('channel unit differs from the manifest')
    return values


def audit_manifest(manifest):
    """Reject leakage even when filenames, channels, or declared group IDs differ."""
    ids = [row.record_id for row in manifest.records]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate record_id')
    collisions = []
    for field in ('entity_group', 'acquisition_root', 'file_sha256', 'samples_sha256'):
        owners = defaultdict(set)
        for row in manifest.records:
            owners[getattr(row, field)].add(row.split)
        collisions.extend({'field': field, 'value': key, 'splits': sorted(splits)}
                          for key, splits in owners.items() if len(splits) > 1)
    if collisions:
        raise ValueError('cross-split source leakage: ' + str(collisions))
    split_counts = Counter(row.split for row in manifest.records)
    coverage = {split: sorted({row.label for row in manifest.records if row.split == split})
                for split in ('development', 'validation', 'sealed_test')}
    required = {'healthy_reference', 'inner_race', 'outer_race', 'rolling_element'}
    missing = {split: sorted(required-set(labels)) for split, labels in coverage.items()}
    return {'cross_split_leakage': False, 'record_count': len(ids),
            'entity_group_count': len({row.entity_group for row in manifest.records}),
            'split_counts': {split: split_counts[split] for split in coverage},
            'label_coverage': coverage, 'missing_four_class_labels': missing,
            'four_class_evaluation_ready': not any(missing.values()),
            'statistical_independence_established': False}
