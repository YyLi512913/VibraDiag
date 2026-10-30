"""Local HTTP baseline in the dedicated test database; not a factory performance claim."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import platform
import runpy
import re
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import numpy as np
import scipy
from sqlalchemy import text
from sqlalchemy.engine import make_url

from vibradiag.core.config import setting
from vibradiag.core.paths import BACKEND_ROOT,PROJECT_ROOT
from vibradiag.infrastructure.persistence.database import engine,transaction
from vibradiag.services.identity_service import password_hasher


def memory_info(pid):
    if os.name!='nt':
        return {'api_peak_working_set_bytes':None,'physical_ram_bytes':None,'measurement':'unavailable'}
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
            ['peak','current','paged_peak','paged','nonpaged_peak','nonpaged','pagefile','pagefile_peak']]
    class Memory(ctypes.Structure):
        _fields_=[('length',wintypes.DWORD),('load',wintypes.DWORD)]+[(name,ctypes.c_ulonglong) for name in
            ['total','available','total_page','available_page','total_virtual','available_virtual','extended']]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi',use_last_error=True)
    kernel.OpenProcess.restype=wintypes.HANDLE;kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    handle=kernel.OpenProcess(0x0400|0x0010,False,pid)
    counter=Counters();counter.cb=ctypes.sizeof(counter)
    try:
        measured=bool(handle and psapi.GetProcessMemoryInfo(handle,ctypes.byref(counter),counter.cb))
    finally:
        if handle:kernel.CloseHandle(handle)
    memory=Memory();memory.length=ctypes.sizeof(memory)
    kernel.GlobalMemoryStatusEx(ctypes.byref(memory))
    return {'api_peak_working_set_bytes':counter.peak if measured else None,'physical_ram_bytes':memory.total,
            'measurement':'Windows GetProcessMemoryInfo PeakWorkingSetSize (API process only)'}


def main():
    dev=make_url(setting('VIBRADIAG_DATABASE_URL'))
    test=make_url(setting('VIBRADIAG_TEST_DATABASE_URL'))
    if test.database==dev.database:
        raise ValueError('Dedicated benchmark/test database required')
    os.environ['VIBRADIAG_DATABASE_URL']=setting('VIBRADIAG_TEST_DATABASE_URL')
    os.environ['VIBRADIAG_MIGRATION_DATABASE_URL']=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    os.environ['VIBRADIAG_COOKIE_SECURE']='false';os.environ['VIBRADIAG_ENABLE_PROTOTYPE']='false'
    run_id=uuid4().hex
    data_root=PROJECT_ROOT/'.runtime/m1-benchmark'/run_id
    os.environ['VIBRADIAG_DATA_ROOT']=str(data_root)
    engine.cache_clear();runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']()
    org,actor=uuid4(),uuid4();password=str(uuid4())+str(uuid4())
    with transaction() as connection:
        connection.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name='Synthetic benchmark '+run_id))
        connection.execute(text("INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,'benchmark',:hash)"),
                           dict(id=actor,org=org,hash=password_hasher.hash(password)))
        connection.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,'engineer',:id)"),dict(id=actor,org=org))
        version=connection.execute(text('SELECT version()')).scalar_one()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    log=(PROJECT_ROOT/'.runtime/m1-benchmark.log').open('w',encoding='utf8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],
        cwd=PROJECT_ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        for _ in range(50):
            if process.poll() is not None:raise RuntimeError('Benchmark API startup failed')
            try:
                if httpx.get(url+'/',timeout=1).status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.2)
        else:raise RuntimeError('Benchmark startup timed out')
        with httpx.Client(base_url=url,timeout=120) as client:
            auth=client.post('/api/v1/auth/login',json=dict(organization_id=str(org),username='benchmark',password=password));auth.raise_for_status()
            client.headers['x-csrf-token']=auth.json()['csrf_token']
            def upload(raw,rate,name):
                response=client.post('/api/v1/measurements',files={'file':(name,raw,'text/csv')},
                    data={'metadata':json.dumps({'sampling_rate_hz':rate,'acceleration_unit':'m/s2','source_type':'synthetic','source_note':'M1 local performance baseline'})})
                response.raise_for_status();record=response.json()
                confirm=client.post(f"/api/v1/measurements/{record['id']}/confirmations",json={'revision_id':record['revision']['id']});confirm.raise_for_status()
                return {'measurement_revision_id':record['revision']['id'],'confirmation_id':confirm.json()['id']}
            raw=('value\n'+'\n'.join(str(2*math.sin(2*math.pi*120*n/12000)) for n in range(120000))+'\n').encode()
            payload=upload(raw,12000,'synthetic-120000.csv')
            durations=[];failures=[];analysis_ids=[]
            for index in range(100):
                start=time.perf_counter()
                try:
                    response=client.post('/api/v1/analyses',json=payload,headers={'Idempotency-Key':str(uuid4())})
                    body=response.json()
                    if response.status_code!=201 or body.get('status')!='completed':
                        failures.append({'index':index,'http_status':response.status_code,'error_code':body.get('error_code')})
                    elif not math.isclose(body['metrics']['rms']['value'],math.sqrt(2),rel_tol=1e-9):
                        failures.append({'index':index,'code':'NUMERIC_MISMATCH'})
                    else:analysis_ids.append(body['analysis_id'])
                except httpx.HTTPError:
                    failures.append({'index':index,'code':'HTTP_ERROR'})
                durations.append(time.perf_counter()-start)
                if (index+1)%25==0:print(f'Baseline requests completed: {index+1}/100',flush=True)
            large=upload(b'value\n'+b'1\n'*1_000_000,1024,'synthetic-million.csv')
            def large_run():
                start=time.perf_counter()
                response=client.post('/api/v1/analyses',json=large,headers={'Idempotency-Key':str(uuid4())})
                return {'seconds':time.perf_counter()-start,'http_status':response.status_code,'status':response.json().get('status')}
            standalone=large_run()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(large_run) for _ in range(2)]
                observed=False
                for _ in range(200):
                    with transaction() as connection:
                        count=connection.execute(text("SELECT count(*) FROM analysis_runs WHERE requested_by=:actor AND status='running'"),dict(actor=actor)).scalar_one()
                    if count==2:observed=True;break
                    time.sleep(.01)
                rejection=client.post('/api/v1/analyses',json=large,headers={'Idempotency-Key':str(uuid4())}) if observed else None
                concurrent=[future.result() for future in futures]
            # Windows venv python.exe can be a small redirector process; measure Uvicorn itself.
            api_pids=re.findall(r'Started server process \[(\d+)\]',(PROJECT_ROOT/'.runtime/m1-benchmark.log').read_text(encoding='utf8'))
            if not api_pids:raise RuntimeError('API process ID not available for memory measurement')
            memory=memory_info(int(api_pids[-1]))
            ordered=sorted(durations)
            result={'run_id':run_id,'input_source':'synthetic','factory_data_used':False,'environment':{
                'os':platform.platform(),'processor':platform.processor(),'logical_cpu_count':os.cpu_count(),
                'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,'database':version,**memory},
                'transport':'loopback HTTP; upload/confirmation excluded; POST includes acceptance, full input/outputs verification and persistence',
                'typical_input':{'sample_count':120000,'sampling_rate_hz':12000,'sha256':sha256(raw).hexdigest()},
                'requests':100,'successes':len(analysis_ids),'failures':failures,'failure_count':len(failures),
                'latency_seconds':{'p50':ordered[49],'p95':ordered[94],'max':max(ordered),'all_requests':durations,'percentile':'nearest rank, including failed attempts'},
                'million_point_standalone':standalone,'two_concurrent_million_point_runs':concurrent,
                'observed_two_running':observed,'over_capacity_http_status':rejection.status_code if rejection else None,
                'passed':not failures and standalone['status']=='completed' and all(r['status']=='completed' for r in concurrent)
                    and observed and rejection is not None and rejection.status_code==429,
                'reference_environment_release_gate':False}
        report=PROJECT_ROOT/'experiments/m1-performance-baseline.json'
        report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
        print(json.dumps({k:v for k,v in result.items() if k not in {'latency_seconds','environment'}},ensure_ascii=False))
        print(json.dumps({'p50_seconds':ordered[49],'p95_seconds':ordered[94],**memory}))
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        log.close()


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Benchmark failed; inspect private runtime log (credentials hidden)') from None
