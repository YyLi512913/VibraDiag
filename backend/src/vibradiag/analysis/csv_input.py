"""Strict, bounded CSV parsing; no filtering, unit conversion or fault diagnosis."""
import csv
from dataclasses import dataclass
import io
import math
import re

import numpy as np

from vibradiag.domain.measurement_errors import MeasurementError

MAX_FILE_BYTES = 32 * 1024 * 1024
MIN_POINTS = 1024
MAX_POINTS = 1_000_000
PARSER_VERSION = 'csv-value-v1'
NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z', re.ASCII)


@dataclass(frozen=True)
class ParsedSignal:
    values: np.ndarray

    @property
    def summary(self):
        return {'sample_count': len(self.values), 'column': 'value',
                'encoding': 'utf-8-sig', 'parser_version': PARSER_VERSION}


def parse_csv(raw: bytes) -> ParsedSignal:
    if len(raw) > MAX_FILE_BYTES:
        raise MeasurementError('FILE_TOO_LARGE', 'CSV exceeds 32 MiB', status=413)
    try:
        decoded = raw.decode('utf-8-sig', errors='strict')
    except UnicodeDecodeError:
        raise MeasurementError('CSV_ENCODING', 'CSV must be UTF-8', field='file') from None
    reader = csv.reader(io.StringIO(decoded, newline=''), strict=True)
    values = np.empty(min(MAX_POINTS, max(MIN_POINTS, len(raw)//2)), dtype=np.float64)
    count = 0
    try:
        if next(reader, None) != ['value'] or reader.line_num != 1:
            raise MeasurementError('CSV_HEADER', 'CSV must contain exactly one value column', row=1)
        for record in reader:
            row = reader.line_num
            if len(record) != 1 or '\n' in record[0] or '\r' in record[0]:
                raise MeasurementError('CSV_STRUCTURE', 'Expected one numeric field per physical row', row=row)
            token = record[0].strip(' \t')
            if len(record[0]) > 64 or not NUMBER.fullmatch(token):
                raise MeasurementError('CSV_INVALID_VALUE', 'Expected a finite decimal number', field='value', row=row)
            value = float(token)
            if not math.isfinite(value):
                raise MeasurementError('CSV_INVALID_VALUE', 'Expected a finite decimal number', field='value', row=row)
            # Do not silently turn nonzero decimal magnitudes into float64 zero.
            mantissa = re.split('[eE]', token)[0]
            if value == 0 and any(c in '123456789' for c in mantissa):
                raise MeasurementError('NUMERIC_RANGE_UNSUPPORTED', 'Value underflows float64', field='value', row=row)
            if count >= MAX_POINTS:
                raise MeasurementError('CSV_POINT_LIMIT', 'CSV exceeds 1000000 points', row=row)
            if count == len(values):
                values.resize(min(MAX_POINTS, len(values)*2), refcheck=False)
            values[count] = value
            count += 1
    except csv.Error:
        raise MeasurementError('CSV_STRUCTURE', 'Invalid CSV quoting or record structure', row=reader.line_num) from None
    if count < MIN_POINTS:
        raise MeasurementError('CSV_TOO_SHORT', 'CSV requires at least 1024 points')
    return ParsedSignal(values[:count].copy())


def validate_time_axis(count: int, sampling_rate: float):
    try:
        duration = count / sampling_rate
        interval = 1 / sampling_rate
        last = (count-1) / sampling_rate
    except (OverflowError, ZeroDivisionError):
        duration = interval = last = float('inf')
    if not all(math.isfinite(v) and v > 0 for v in (duration, interval, last)):
        raise MeasurementError('TIME_AXIS_UNSUPPORTED', 'Sampling rate cannot represent this time axis', field='sampling_rate_hz')
    return duration


def make_preview(parsed: ParsedSignal, sampling_rate: float):
    x = parsed.values
    # At most 1000 buckets, both extrema per bucket, chronologically ordered.
    indices = []
    for group in np.array_split(np.arange(len(x)), min(1000, len(x))):
        low = int(group[np.argmin(x[group])]); high = int(group[np.argmax(x[group])])
        indices.extend(sorted({low, high}))
    return {'indices': indices, 'time_s': [i/sampling_rate for i in indices],
            'values': [float(x[i]) for i in indices], 'sample_count':len(x),
            'method':'bucket_min_max', 'max_points':2000, 'display_only':True,
            'duration_s':validate_time_axis(len(x),sampling_rate),
            'last_sample_time_s':(len(x)-1)/sampling_rate}
