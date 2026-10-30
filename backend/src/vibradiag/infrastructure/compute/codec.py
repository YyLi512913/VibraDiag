"""Closed JSON/NPZ protocol. No pickle, dynamic imports or caller-chosen file names."""
from hashlib import sha256
import json
import math
import re
from pathlib import Path
import zipfile
import numpy as np
from pydantic import BaseModel
from vibradiag.analysis.basic import BasicResult
from vibradiag.domain.tool_results import NumericToolResult
from vibradiag.schemas.tools import EnvelopeParameters,BearingFrequencyParameters
from vibradiag.schemas.comparison import ComparisonCapture,ComparisonParameters

PROTOCOL='numeric-ipc-v1'
MAX_BYTES=128*1024*1024
MAX_JSON=4*1024*1024
MODELS={c.__name__:c for c in (EnvelopeParameters,BearingFrequencyParameters,ComparisonCapture,ComparisonParameters)}


def write(path,value):
    payload=json.dumps(value,allow_nan=False,separators=(',',':')).encode()
    if len(payload)>MAX_JSON:raise ValueError('Numerical metadata too large')
    temporary=path.with_suffix('.tmp');temporary.write_bytes(payload);temporary.replace(path)


def read(path):
    if path.stat().st_size>MAX_JSON:raise ValueError('Numerical metadata too large')
    return json.loads(path.read_bytes())


def encode(value,directory,prefix):
    arrays={};blobs={};total=0
    def visit(item,depth=0):
        nonlocal total
        if depth>40:raise ValueError('Numerical nesting limit')
        if isinstance(item,np.ndarray):
            if item.ndim!=1 or len(item)>1_000_000 or item.dtype.kind not in 'fiu' or not np.isfinite(item).all():raise ValueError('Invalid numerical array')
            total+=item.nbytes
            if len(arrays)>=64 or total>MAX_BYTES:raise ValueError('Numerical arrays too large')
            key='a'+str(len(arrays));arrays[key]=item;return {'t':'array','v':key}
        if isinstance(item,bytes):
            total+=len(item)
            if len(blobs)>=4 or total>MAX_BYTES:raise ValueError('Numerical input too large')
            name=prefix+'-b'+str(len(blobs))+'.bin';(directory/name).write_bytes(item)
            blobs[name]=dict(sha256=sha256(item).hexdigest(),size=len(item));return {'t':'bytes','v':name}
        if isinstance(item,BasicResult):return {'t':'basic','v':visit(dict(summary=item.summary,waveform=item.waveform,frequency=item.frequency,amplitude=item.amplitude),depth+1)}
        if isinstance(item,NumericToolResult):return {'t':'tool','v':visit(dict(tool_name=item.tool_name,tool_version=item.tool_version,status=item.status,
            summary=item.summary,arrays=item.arrays,missing_requirements=list(item.missing_requirements),limitations=list(item.limitations),error_code=item.error_code),depth+1)}
        if isinstance(item,BaseModel):
            if type(item).__name__ not in MODELS:raise ValueError('Unapproved numeric model')
            return {'t':type(item).__name__,'v':visit(item.model_dump(mode='json'),depth+1)}
        if isinstance(item,dict):
            if len(item)>2000 or any(not isinstance(k,str) for k in item):raise ValueError('Invalid numerical mapping')
            return {'t':'dict','v':{k:visit(v,depth+1) for k,v in item.items()}}
        if isinstance(item,(list,tuple)):
            if len(item)>10000:raise ValueError('Numerical list limit')
            return {'t':'list','v':[visit(v,depth+1) for v in item]}
        if isinstance(item,np.generic):item=item.item()
        if item is None or type(item) in (str,bool,int,float):
            if isinstance(item,float) and not math.isfinite(item):raise ValueError('Nonfinite numerical metadata')
            return {'t':'value','v':item}
        raise ValueError('Unsupported numerical transport type')
    tree=visit(value);array_path=directory/(prefix+'.npz');np.savez(array_path,**arrays)
    if array_path.stat().st_size+sum(b['size'] for b in blobs.values())>MAX_BYTES:raise ValueError('Numerical payload limit')
    return dict(tree=tree,blobs=blobs,arrays=dict(sha256=sha256(array_path.read_bytes()).hexdigest(),size=array_path.stat().st_size))


def decode(frame,directory,prefix):
    if set(frame)!={'tree','blobs','arrays'}:raise ValueError('Invalid numerical frame')
    if not isinstance(frame['blobs'],dict) or len(frame['blobs'])>4:raise ValueError('Numerical blob limit')
    path=directory/(prefix+'.npz')
    if path.stat().st_size>MAX_BYTES or path.stat().st_size!=frame['arrays']['size'] or sha256(path.read_bytes()).hexdigest()!=frame['arrays']['sha256']:raise ValueError('Numerical array integrity')
    total=path.stat().st_size
    for name,descriptor in frame['blobs'].items():
        if not re.fullmatch(re.escape(prefix)+r'-b[0-3]\.bin',name):raise ValueError('Invalid numeric blob name')
        size=(directory/name).stat().st_size
        if size!=descriptor['size']:raise ValueError('Numerical blob size')
        total+=size
    if total>MAX_BYTES:raise ValueError('Numerical payload limit')
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist())>64 or sum(i.file_size for i in archive.infolist())>MAX_BYTES:raise ValueError('Numerical array archive limit')
        seen=set()
        for info in archive.infolist():
            if not re.fullmatch(r'a[0-9]+\.npy',info.filename) or info.filename in seen:raise ValueError('Invalid numerical member')
            seen.add(info.filename)
            with archive.open(info) as member:
                if np.lib.format.read_magic(member)!=(1,0):raise ValueError('Unsupported numerical array format')
                shape,_,dtype=np.lib.format.read_array_header_1_0(member)
                if len(shape)!=1 or not 0<=shape[0]<=1_000_000 or dtype.kind not in 'fiu' or dtype.itemsize>8:raise ValueError('Invalid numerical array header')
                if shape[0]*dtype.itemsize+member.tell()!=info.file_size:raise ValueError('Numerical array size mismatch')
    with np.load(path,allow_pickle=False) as loaded:
        arrays={k:loaded[k] for k in loaded.files}
    for key,array in arrays.items():
        if not re.fullmatch('a[0-9]+',key) or array.ndim!=1 or len(array)>1_000_000 or array.dtype.kind not in 'fiu' or not np.isfinite(array).all():raise ValueError('Invalid numerical array')
    def visit(node,depth=0):
        if depth>40 or not isinstance(node,dict) or set(node)!={'t','v'}:raise ValueError('Invalid numerical node')
        kind=node['t'];value=node['v']
        if kind=='array':return arrays[value]
        if kind=='bytes':
            if not isinstance(value,str) or not re.fullmatch(re.escape(prefix)+r'-b[0-3]\.bin',value):raise ValueError('Invalid numeric blob name')
            blob=directory/value;descriptor=frame['blobs'][value]
            if blob.stat().st_size>MAX_BYTES or blob.stat().st_size!=descriptor['size']:raise ValueError('Numeric blob size')
            raw=blob.read_bytes()
            if sha256(raw).hexdigest()!=descriptor['sha256']:raise ValueError('Numeric blob integrity')
            return raw
        if kind=='value':
            if value is not None and type(value) not in (str,bool,int,float):raise ValueError('Invalid scalar')
            if isinstance(value,float) and not math.isfinite(value):raise ValueError('Invalid scalar')
            return value
        if kind=='dict':
            if not isinstance(value,dict) or len(value)>2000:raise ValueError('Invalid mapping')
            return {k:visit(v,depth+1) for k,v in value.items()}
        if kind=='list':
            if not isinstance(value,list) or len(value)>10000:raise ValueError('Invalid list')
            return [visit(v,depth+1) for v in value]
        if kind in MODELS:return MODELS[kind].model_validate(visit(value,depth+1))
        if kind=='basic':return BasicResult(**visit(value,depth+1))
        if kind=='tool':
            data=visit(value,depth+1);data['missing_requirements']=tuple(data['missing_requirements']);data['limitations']=tuple(data['limitations'])
            return NumericToolResult(**data)
        raise ValueError('Unapproved numerical type')
    return visit(frame['tree'])
