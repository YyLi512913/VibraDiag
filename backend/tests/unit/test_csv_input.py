import numpy as np
import pytest
from pydantic import ValidationError

from vibradiag.analysis.csv_input import MAX_FILE_BYTES, make_preview, parse_csv, validate_time_axis
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.schemas.measurements import MeasurementMetadata


def csv_bytes(token='0', count=1024):
    return ('value\n'+(token+'\n')*count).encode()


@pytest.mark.parametrize('token',['0','-1.25','+2','1e-3','.5','1.','" 1.5\t"'])
def test_valid_decimal_csv_and_bom(token):
    parsed=parse_csv(b'\xef\xbb\xbf'+csv_bytes(token))
    assert len(parsed.values)==1024 and parsed.summary['parser_version']=='csv-value-v1'


@pytest.mark.parametrize('token',['',' ','NaN','Inf','1e999','1e-9999','0x10','1_000','1,000',
                                       '١','1\x00','"1\n2"','1,2','"1"garbage','1'*65])
def test_invalid_rows_are_not_deleted(token):
    with pytest.raises(MeasurementError) as error:
        parse_csv(csv_bytes(token))
    assert error.value.row is not None


def test_encoding_header_and_trailing_lines():
    for raw in [b'value\n\xff', b'Value\n'+b'0\n'*1024,b'value,other\n'+b'0,0\n'*1024,
                csv_bytes()+b'\n',csv_bytes()+b' \n']:
        with pytest.raises(MeasurementError):
            parse_csv(raw)
    assert len(parse_csv(csv_bytes()).values)==1024
    assert len(parse_csv(csv_bytes()[:-1]).values)==1024


@pytest.mark.parametrize('count,accepted',[(1023,False),(1024,True),(1_000_000,True),(1_000_001,False)])
def test_point_boundaries(count,accepted):
    if accepted:
        assert len(parse_csv(csv_bytes(count=count)).values)==count
    else:
        with pytest.raises(MeasurementError):
            parse_csv(csv_bytes(count=count))


def test_exact_file_byte_limit():
    count,remainder=divmod(MAX_FILE_BYTES-6,64)
    raw=b'value\n'+(b'0'+b' '*62+b'\n')*count+(b'0'+b' '*(remainder-2)+b'\n')
    assert len(raw)==MAX_FILE_BYTES
    assert len(parse_csv(raw).values)==count+1
    with pytest.raises(MeasurementError) as error:
        parse_csv(raw+b'\n')
    assert error.value.status==413


def test_preview_preserves_impulse_and_does_not_change_input():
    raw=b'value\n'+b'0\n'*4999+b'100\n'+b'0\n'*5000
    parsed=parse_csv(raw)
    original=parsed.values.copy()
    preview=make_preview(parsed,1000)
    assert len(preview['values'])<=2000 and 100 in preview['values']
    assert preview['indices']==sorted(set(preview['indices']))
    assert preview['duration_s']==10 and preview['last_sample_time_s']==9.999
    np.testing.assert_array_equal(parsed.values,original)


@pytest.mark.parametrize('overrides',[{'sampling_rate_hz':True},{'sampling_rate_hz':float('inf')},
    {'sampling_rate_hz':0},{'acceleration_unit':'mm/s'},{'rpm':float('nan')},
    {'load_value':10},{'direction':'other'},{'acquired_at':'2026-10-29T10:00:00'},
    {'parameter_sources':{}},{'measurement_point_id':'00000000-0000-0000-0000-000000000001'}])
def test_metadata_rejects_missing_or_invented_fields(overrides):
    with pytest.raises(ValidationError):
        MeasurementMetadata.model_validate(dict(sampling_rate_hz=1024,acceleration_unit='g',**{})|overrides)


def test_time_axis_range_and_unknown_provenance():
    with pytest.raises(MeasurementError):
        validate_time_axis(1024,5e-324)
    metadata=MeasurementMetadata(sampling_rate_hz=1024,acceleration_unit='g')
    assert metadata.source_type=='unknown' and metadata.acquired_at is None and metadata.rpm is None
