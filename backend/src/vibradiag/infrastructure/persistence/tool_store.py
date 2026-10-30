"""Tool-specific atomic artifact bundles; independent of M1's four-file contract."""
from hashlib import sha256
import io
import json
import os
from uuid import uuid4
import numpy as np
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.infrastructure.persistence.analysis_store import json_bytes

KINDS = {'result':'result.json', 'arrays':'arrays.npz', 'manifest':'manifest.json'}
LIMIT = 64 * 1024 * 1024
IDENTITY = ('tool_execution_id','measurement_revision_id','input_sha256','input_fingerprint','tool_name','tool_version')


class ToolStore(FileStore):
    def publish(self, org, identifier, result, computed):
        self.staging.mkdir(parents=True, exist_ok=True)
        temporary = self.staging / ('tool-' + str(uuid4()))
        temporary.mkdir()
        key = f'tools/{org}/{identifier}'
        target = self.resolve(key)
        rows = []
        try:
            blobs = {'result':json_bytes(result)}
            if computed.arrays:
                buffer = io.BytesIO()
                if any(a.dtype.kind != 'f' or not np.isfinite(a).all() for a in computed.arrays.values()):
                    raise ValueError('Invalid numerical output')
                np.savez(buffer, **computed.arrays)
                blobs['arrays'] = buffer.getvalue()
            for kind in ('result','arrays','manifest'):
                if kind == 'manifest':
                    blobs[kind] = json_bytes(dict({name:result[name] for name in IDENTITY},
                        files={r['kind']:{'sha256':r['sha256'],'byte_size':r['byte_size']} for r in rows}))
                if kind not in blobs:
                    continue
                payload = blobs[kind]
                if len(payload) > LIMIT:
                    raise MeasurementError('TOOL_ARTIFACT_LIMIT', 'Tool output exceeds storage budget', status=409)
                with (temporary / KINDS[kind]).open('xb') as output:
                    output.write(payload); output.flush(); os.fsync(output.fileno())
                rows.append(dict(id=uuid4(), kind=kind, storage_key=key+'/'+KINDS[kind], sha256=sha256(payload).hexdigest(),
                    byte_size=len(payload), format='npz' if kind=='arrays' else 'json', state='ready'))
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise FileExistsError('Tool directory already exists')
            os.rename(temporary, target)
            return rows
        finally:
            if temporary.exists() and temporary.resolve().is_relative_to(self.staging.resolve()):
                for name in KINDS.values():
                    (temporary/name).unlink(missing_ok=True)
                temporary.rmdir()

    def verified(self, rows, *, arrays_expected):
        expected = {'result','manifest','arrays'} if arrays_expected else {'result','manifest'}
        if len(rows) != len(expected) or {r['kind'] for r in rows} != expected:
            raise MeasurementError('ARTIFACT_INTEGRITY', 'Tool artifact set is incomplete', status=409)
        blobs = {}
        for row in rows:
            if row['state'] != 'ready' or not 0 < row['byte_size'] <= LIMIT:
                raise MeasurementError('ARTIFACT_INTEGRITY', 'Tool artifact is not ready', status=409)
            try:
                with self.resolve(row['storage_key']).open('rb') as source:
                    payload = source.read(LIMIT+1)
            except FileNotFoundError:
                raise MeasurementError('ARTIFACT_MISSING', 'Tool artifact is missing', status=409) from None
            if len(payload) != row['byte_size'] or sha256(payload).hexdigest() != row['sha256']:
                raise MeasurementError('ARTIFACT_INTEGRITY', 'Tool artifact hash mismatch', status=409)
            blobs[row['kind']] = payload
        try:
            manifest = json.loads(blobs['manifest'])
            files = {r['kind']:{'sha256':r['sha256'],'byte_size':r['byte_size']} for r in rows if r['kind']!='manifest'}
            if manifest.get('files') != files:
                raise ValueError()
        except (ValueError, TypeError):
            raise MeasurementError('ARTIFACT_INTEGRITY', 'Tool manifest mismatch', status=409) from None
        return blobs
