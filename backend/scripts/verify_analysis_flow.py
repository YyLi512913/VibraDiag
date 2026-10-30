"""Real loopback HTTP and process-kill recovery using explicitly synthetic inputs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import numpy as np
from sqlalchemy import text

from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.infrastructure.persistence.database import transaction


def verify(access_file):
    access=json.loads(access_file.read_text(encoding='utf8'))
    account=next(a for a in access['accounts'] if a['username']=='local-engineer')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    process=None
    log=(PROJECT_ROOT/'.runtime/analysis-http-smoke.log').open('w',encoding='utf8')

    def start():
        nonlocal process
        process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],
            cwd=PROJECT_ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(50):
            if process.poll() is not None:
                raise RuntimeError('Temporary API startup failed')
            try:
                if httpx.get(url+'/',timeout=1).status_code==200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        raise RuntimeError('Temporary API startup timed out')

    def stop(force=False):
        nonlocal process
        if process and process.poll() is None:
            if force:
                process.kill()
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
        process=None

    def login(client):
        response=client.post('/api/v1/auth/login',json=dict(organization_id=access['organization_id'],
            username=account['username'],password=account['password']))
        response.raise_for_status()
        client.headers['x-csrf-token']=response.json()['csrf_token']

    def upload_confirm(client,raw,name):
        response=client.post('/api/v1/measurements',files={'file':(name,raw,'text/csv')},data={'metadata':json.dumps({
            'sampling_rate_hz':1024,'acceleration_unit':'m/s2','source_type':'synthetic',
            'source_note':'M1-T04 loopback smoke; generated signal, not factory data'})})
        response.raise_for_status();record=response.json()
        confirmation=client.post(f"/api/v1/measurements/{record['id']}/confirmations",json={'revision_id':record['revision']['id']})
        confirmation.raise_for_status()
        return {'measurement_revision_id':record['revision']['id'],'confirmation_id':confirmation.json()['id']}

    try:
        start()
        with httpx.Client(base_url=url,timeout=60) as client:
            login(client)
            raw=('value\n'+'\n'.join(str(2*math.sin(2*math.pi*64*n/1024)) for n in range(1024))+'\n').encode()
            payload=upload_confirm(client,raw,'synthetic-64hz.csv');key=str(uuid4())
            response=client.post('/api/v1/analyses',json=payload,headers={'Idempotency-Key':key})
            response.raise_for_status();completed=response.json()
            assert completed['status']=='completed'
            assert math.isclose(completed['metrics']['rms']['value'],math.sqrt(2),rel_tol=1e-9)
            prefix='/api/v1/analyses/'+completed['analysis_id']
            binary=client.get(prefix+'/artifacts/amplitude_spectrum');binary.raise_for_status()
            with np.load(io.BytesIO(binary.content),allow_pickle=False) as data:
                assert math.isclose(float(data['amplitude_m_s2'][64]),2,rel_tol=1e-8)
            replay=client.post('/api/v1/analyses',json=payload,headers={'Idempotency-Key':key})
            assert replay.status_code==200 and replay.json()['analysis_id']==completed['analysis_id']
            large_payload=upload_confirm(client,b'value\n'+b'1\n'*1_000_000,'synthetic-million-point-constant.csv')
            crash_key=str(uuid4())
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(client.post,'/api/v1/analyses',json=large_payload,headers={'Idempotency-Key':crash_key})
                running_id=None
                for _ in range(200):
                    with transaction() as connection:
                        row=connection.execute(text('''SELECT r.id,r.status FROM idempotency_records i
                            JOIN analysis_runs r ON r.id=i.resource_id WHERE i.organization_id=:org
                            AND i.actor_id=:actor AND i.operation='basic-analysis' AND i.key_hash=:key'''),
                            dict(org=access['organization_id'],actor=account['user_id'],key=sha256(crash_key.encode()).hexdigest())).first()
                    if row:
                        if row.status!='running':
                            raise RuntimeError('Analysis completed before crash injection')
                        running_id=str(row.id);break
                    time.sleep(.01)
                if not running_id:
                    raise RuntimeError('Could not observe durably accepted analysis')
                stop(force=True)
                try:
                    pending.result(timeout=10)
                except httpx.HTTPError:
                    pass
        start()
        with httpx.Client(base_url=url,timeout=60) as client:
            login(client)
            reread=client.get(prefix);reread.raise_for_status()
            assert reread.json()['metrics']==completed['metrics']
            replay=client.post('/api/v1/analyses',json=large_payload,headers={'Idempotency-Key':crash_key})
            assert replay.status_code==200 and replay.json()['analysis_id']==running_id
            assert replay.json()['status']=='interrupted' and replay.json()['error_code']=='PROCESS_INTERRUPTED'
            retry=client.post('/api/v1/analyses',json=dict(large_payload,retry_of=running_id),headers={'Idempotency-Key':str(uuid4())})
            retry.raise_for_status()
            assert retry.json()['status']=='completed' and retry.json()['analysis_id']!=running_id
            assert retry.json()['result_summary']['sample_count']==1_000_000
        return {'passed':True,'transport':'loopback HTTP','input_source':'synthetic','factory_data_used':False,
                'sine_analysis_id':completed['analysis_id'],'interrupted_analysis_id':running_id,
                'retry_analysis_id':retry.json()['analysis_id'],'large_input_points':1_000_000,
                'checks':['sine_metrics','spectrum_npz','idempotent_replay','observed_committed_running',
                          'forced_API_process_kill','startup_interrupted_recovery','completed_result_survives_restart',
                          'interrupted_same_key_no_automatic_rerun','explicit_retry_new_id']}
    finally:
        stop();log.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--access-file',type=Path,default=PROJECT_ROOT/'.runtime/local-access.json')
    args=parser.parse_args()
    try:
        result=verify(args.access_file)
    except Exception:
        raise SystemExit('Analysis HTTP smoke failed; inspect private runtime log (credentials hidden)') from None
    (PROJECT_ROOT/'experiments/m1-t04-http-smoke.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))
