"""Closed, bounded numerical protocol; no constructor or pickle transport."""
from hashlib import sha256
import io
import zipfile
import numpy as np
import pytest
from vibradiag.infrastructure.compute import codec


def test_array_and_reserved_mapping_keys_roundtrip_without_ambiguity(tmp_path):
    original={'t':'not-a-tag','v':[1,None,True],'array':np.arange(1024,dtype=np.float64),'raw':b'value\n1\n'}
    frame=codec.encode(original,tmp_path,'input');restored=codec.decode(frame,tmp_path,'input')
    assert restored['t']=='not-a-tag' and restored['raw']==original['raw'] and restored['v']==original['v']
    np.testing.assert_array_equal(restored['array'],original['array'])


@pytest.mark.parametrize('array',[np.array([object()],dtype=object),np.array([float('nan')]),np.ones((2,2))])
def test_object_nonfinite_and_multidimensional_arrays_refused(tmp_path,array):
    with pytest.raises(ValueError):codec.encode(array,tmp_path,'input')


def test_tampered_arrays_fail_hash_check(tmp_path):
    frame=codec.encode(np.arange(1024.),tmp_path,'input');(tmp_path/'input.npz').write_bytes(b'tampered')
    with pytest.raises(ValueError):codec.decode(frame,tmp_path,'input')


def test_unknown_type_cannot_load_a_constructor(tmp_path):
    frame=codec.encode(None,tmp_path,'input');frame['tree']={'t':'pickle','v':'never-loaded'}
    with pytest.raises(ValueError):codec.decode(frame,tmp_path,'input')


def test_blob_name_cannot_escape_private_job_directory(tmp_path):
    frame=codec.encode(b'private',tmp_path,'input');frame['tree']['v']='../outside.bin'
    with pytest.raises(ValueError):codec.decode(frame,tmp_path,'input')


def test_npz_header_size_checked_before_array_allocation(tmp_path):
    frame=codec.encode(None,tmp_path,'input');header=io.BytesIO()
    np.lib.format.write_array_header_1_0(header,{'descr':'<f8','fortran_order':False,'shape':(10**12,)})
    path=tmp_path/'input.npz'
    with zipfile.ZipFile(path,'w') as archive:archive.writestr('a0.npy',header.getvalue())
    raw=path.read_bytes();frame['arrays']=dict(sha256=sha256(raw).hexdigest(),size=len(raw))
    frame['tree']={'t':'array','v':'a0'}
    with pytest.raises(ValueError):codec.decode(frame,tmp_path,'input')
