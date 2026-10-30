"""Loopback HTTP smoke with synthetic input. Creates one development measurement."""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from vibradiag.core.paths import PROJECT_ROOT


def verify(access_file):
    access=json.loads(access_file.read_text(encoding='utf8'))
    account=next(account for account in access['accounts'] if account['username']=='local-engineer')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    log=PROJECT_ROOT/'.runtime/measurement-http-smoke.log'
    process=None
    output=log.open('w',encoding='utf8')

    def start():
        nonlocal process
        process=subprocess.Popen([sys.executable,'-m','uvicorn','vibradiag.main:app','--host','127.0.0.1','--port',str(port)],
            cwd=PROJECT_ROOT,stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(50):
            if process.poll() is not None:
                raise RuntimeError('Temporary API did not start')
            try:
                if httpx.get(url+'/',timeout=1).status_code==200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        raise RuntimeError('Temporary API startup timed out')

    def stop():
        nonlocal process
        if process and process.poll() is None:
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

    try:
        start()
        raw=('value\n'+'\n'.join(str(2*math.sin(2*math.pi*64*n/1024)) for n in range(1024))+'\n').encode()
        metadata={'sampling_rate_hz':1024,'acceleration_unit':'m/s2','source_type':'synthetic',
                  'source_note':'M1-T03 loopback HTTP smoke: generated 64 Hz sine, not factory data'}
        with httpx.Client(base_url=url,timeout=30) as client:
            login(client)
            response=client.post('/api/v1/measurements',files={'file':('synthetic-smoke.csv',raw,'text/csv')},
                                 data={'metadata':json.dumps(metadata)})
            response.raise_for_status();record=response.json()
            prefix='/api/v1/measurements/'+record['id']
            assert record['sha256']==sha256(raw).hexdigest()
            confirmation=client.post(prefix+'/confirmations',json={'revision_id':record['revision']['id']})
            confirmation.raise_for_status()
            metadata['sampling_rate_hz']=2048
            revised=client.post(prefix+'/revisions',json=dict(metadata=metadata,reason='Synthetic smoke parameter revision',
                expected_current_revision_id=record['revision']['id'],row_version=record['row_version']))
            revised.raise_for_status();updated=revised.json()
            assert updated['input_confirmation'] is None
            assert client.post(prefix+'/confirmations',json={'revision_id':record['revision']['id']}).status_code==409
            assert client.get(prefix+'/original').content==raw
        stop();start()
        with httpx.Client(base_url=url,timeout=30) as client:
            login(client)
            reread=client.get(prefix);reread.raise_for_status()
            assert reread.json()['revision']['id']==updated['revision']['id']
            versions=client.get(prefix+'/revisions');versions.raise_for_status()
            assert [r['revision_no'] for r in versions.json()['items']]==[2,1]
            assert client.get(prefix+'/original').content==raw
        return {'passed':True,'transport':'loopback HTTP','input_source':'synthetic',
                'measurement_id':record['id'],'sample_count':1024,'sha256':record['sha256'],
                'checks':['login','upload','input_confirmation','parameter_revision','stale_confirmation_409',
                          'original_bytes_unchanged','API_process_restart','persistent_revision_history'],
                'factory_data_used':False}
    finally:
        stop();output.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--access-file',type=Path,default=PROJECT_ROOT/'.runtime/local-access.json')
    args=parser.parse_args()
    try:
        result=verify(args.access_file)
    except Exception:
        raise SystemExit('HTTP smoke failed; inspect private runtime log (credentials hidden)') from None
    report=PROJECT_ROOT/'experiments/m1-t03-http-smoke.json'
    report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))
