from hashlib import sha256
import io
import json
import os
from uuid import uuid4
import numpy as np

from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.file_store import FileStore

KINDS={'result':'result.json','waveform_preview':'waveform-preview.json',
       'amplitude_spectrum':'amplitude-spectrum.npz','manifest':'manifest.json'}
MAX_ARTIFACT_BYTES=32*1024*1024


def json_bytes(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf8')


class AnalysisStore(FileStore):
    def publish(self,organization_id,analysis_id,result,computed):
        self.staging.mkdir(parents=True,exist_ok=True)
        temporary=self.staging/('analysis-'+str(uuid4()))
        temporary.mkdir()
        key=f'analyses/{organization_id}/{analysis_id}'
        target=self.resolve(key)
        rows=[]
        try:
            for kind,name in KINDS.items():
                if kind=='manifest':
                    payload=json_bytes({'analysis_id':str(analysis_id),'input_sha256':result['input_sha256'],
                        'measurement_revision_id':result['measurement_revision_id'],
                        'algorithm_version':result['algorithm_version'],'config_hash':result['config_hash'],
                        'files':{r['kind']:{'sha256':r['sha256'],'byte_size':r['byte_size']} for r in rows}})
                elif kind=='amplitude_spectrum':
                    buffer=io.BytesIO()
                    np.savez(buffer,frequency_hz=computed.frequency,amplitude_m_s2=computed.amplitude)
                    payload=buffer.getvalue()
                else:
                    payload=json_bytes(result if kind=='result' else computed.waveform)
                with (temporary/name).open('xb') as destination:
                    destination.write(payload);destination.flush();os.fsync(destination.fileno())
                rows.append({'id':uuid4(),'kind':kind,'storage_key':key+'/'+name,'sha256':sha256(payload).hexdigest(),
                             'byte_size':len(payload),'format':'npz' if kind=='amplitude_spectrum' else 'json'})
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                raise FileExistsError('Analysis directory already exists')
            os.rename(temporary,target)
            return rows
        finally:
            # Only remove this generated staging directory's known files, never published data.
            if temporary.exists() and temporary.resolve().is_relative_to(self.staging.resolve()):
                for name in KINDS.values():
                    (temporary/name).unlink(missing_ok=True)
                temporary.rmdir()

    def verified(self,rows):
        blobs={}
        if len(rows)!=4 or {row['kind'] for row in rows}!=set(KINDS):
            raise MeasurementError('ARTIFACT_INTEGRITY','Analysis artifacts incomplete',status=409)
        for row in rows:
            if row['state']!='ready':
                raise MeasurementError('ARTIFACT_INTEGRITY','Analysis artifact is not ready',status=409)
            try:
                with self.resolve(row['storage_key']).open('rb') as source:
                    payload=source.read(MAX_ARTIFACT_BYTES+1)
            except FileNotFoundError:
                raise MeasurementError('ARTIFACT_MISSING','Analysis artifact is missing',status=409) from None
            if len(payload)!=row['byte_size'] or sha256(payload).hexdigest()!=row['sha256']:
                raise MeasurementError('ARTIFACT_INTEGRITY','Analysis artifact integrity check failed',status=409)
            blobs[row['kind']]=payload
        manifest=json.loads(blobs['manifest'])
        expected={r['kind']:{'sha256':r['sha256'],'byte_size':r['byte_size']} for r in rows if r['kind']!='manifest'}
        if manifest.get('files')!=expected:
            raise MeasurementError('ARTIFACT_INTEGRITY','Manifest does not match registered artifacts',status=409)
        return blobs
