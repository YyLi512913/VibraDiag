"""Private immutable originals; user-supplied filenames never determine paths."""
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4

from vibradiag.analysis.csv_input import MAX_FILE_BYTES
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.domain.measurement_errors import MeasurementError


class FileStore:
    def __init__(self):
        self.root = Path(setting('VIBRADIAG_DATA_ROOT') or str(PROJECT_ROOT/'.runtime/m1_data')).resolve()
        self.staging = self.root/'staging'

    def new_staging(self):
        self.staging.mkdir(parents=True, exist_ok=True)
        return self.staging/(str(uuid4())+'.part')

    def resolve(self, key):
        relative = PurePosixPath(key)
        if relative.is_absolute() or '\\' in key or ':' in key or '..' in relative.parts:
            raise MeasurementError('FILE_INTEGRITY', 'Invalid stored file reference', status=409)
        path = (self.root/Path(*relative.parts)).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise MeasurementError('FILE_INTEGRITY', 'Invalid stored file reference', status=409)
        return path

    def promote(self, staged, organization_id, file_id):
        key = f'originals/{organization_id}/{file_id}.csv'
        path = self.resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive atomic publication on the same filesystem; never overwrite.
        os.link(staged, path)
        staged.unlink()
        return key

    def read_verified(self, key, expected_hash, expected_size):
        path = self.resolve(key)
        try:
            with path.open('rb') as source:
                raw = source.read(MAX_FILE_BYTES+1)
        except FileNotFoundError:
            raise MeasurementError('FILE_MISSING', 'Original file is unavailable', status=409) from None
        if len(raw) != expected_size or sha256(raw).hexdigest() != expected_hash:
            raise MeasurementError('FILE_INTEGRITY', 'Original file integrity check failed', status=409)
        return raw
