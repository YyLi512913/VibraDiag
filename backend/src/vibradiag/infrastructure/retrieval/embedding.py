"""Pinned local dense embeddings for formal indexing; independent of prototype caches."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import re
from threading import BoundedSemaphore
from time import monotonic
from urllib.parse import urlsplit

import httpx

from vibradiag.domain.measurement_errors import MeasurementError

_SLOT = BoundedSemaphore(1)
MAX_RESPONSE = 2 * 1024 * 1024
MAX_BATCH = 8
MAX_TEXT = 2000


def failure(code, status=503):
    # Never expose server response bodies, input text, credentials or endpoint URLs.
    raise MeasurementError(code, 'Pinned local embedding is unavailable or incompatible', status=status)


@dataclass(frozen=True)
class EmbeddingIdentity:
    model: str
    digest: str
    dimensions: int = 1024
    input_policy: str = 'bge-m3-raw-text-v1'
    normalization: str = 'client-l2-float32-v1'
    metric: str = 'cosine'

    def __post_init__(self):
        if (self.model != 'bge-m3:567m' or not re.fullmatch(r'[0-9a-f]{64}', self.digest)
                or self.dimensions != 1024 or self.input_policy != 'bge-m3-raw-text-v1'
                or self.normalization != 'client-l2-float32-v1' or self.metric != 'cosine'):
            raise ValueError('Unsupported formal embedding identity')

    @property
    def fingerprint(self):
        return sha256(json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_vectors(value, count, dimensions=1024):
    """Reject shape, bool, nonfinite and zero output, then normalize actual float32 data."""
    import numpy as np
    if not isinstance(value, list) or len(value) != count:
        failure('EMBEDDING_OUTPUT_INVALID')
    for row in value:
        if (not isinstance(row, list) or len(row) != dimensions
                or any(type(v) not in (int, float) or abs(v) > 3.4028234e38 or not math.isfinite(v) for v in row)):
            failure('EMBEDDING_OUTPUT_INVALID')
    with np.errstate(over='ignore', invalid='ignore'):
        array = np.asarray(value, dtype=np.float32)
    norms = np.linalg.norm(array.astype(np.float64), axis=1)
    if not np.all(np.isfinite(array)) or not np.all(np.isfinite(norms)) or np.any(norms <= 1e-12):
        failure('EMBEDDING_OUTPUT_INVALID')
    result = (array.astype(np.float64) / norms[:, None]).astype(np.float32)
    return result


class LocalEmbedding:
    def __init__(self, identity, *, base_url='http://127.0.0.1:11434', transport=None, timeout=60):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or parsed.username or parsed.password or parsed.path not in {'', '/'}
                or parsed.query or parsed.fragment or not 0 < timeout <= 120):
            raise ValueError('Local loopback Ollama endpoint and bounded timeout required')
        if not isinstance(identity, EmbeddingIdentity):raise ValueError('Explicit pinned identity required')
        self.identity, self.base_url, self.transport, self.timeout = identity, base_url.rstrip('/'), transport, timeout

    @contextmanager
    def _session(self):
        if not _SLOT.acquire(blocking=False):failure('EMBEDDING_BUSY', 429)
        try:
            with httpx.Client(base_url=self.base_url, trust_env=False, follow_redirects=False,
                              transport=self.transport) as client:
                yield client, monotonic() + self.timeout
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            failure('EMBEDDING_SERVICE_UNAVAILABLE')
        finally:
            _SLOT.release()

    def _call(self, client, deadline, path, payload=None):
        remaining = deadline - monotonic()
        if remaining <= 0:failure('EMBEDDING_DEADLINE_EXCEEDED')
        with client.stream('GET' if payload is None else 'POST', path, json=payload,
                           timeout=httpx.Timeout(remaining, connect=min(remaining, 5), pool=min(remaining, 5))) as response:
            if response.status_code != 200:failure('EMBEDDING_SERVICE_UNAVAILABLE')
            body = bytearray()
            for part in response.iter_bytes(chunk_size=65536):
                if monotonic() >= deadline:failure('EMBEDDING_DEADLINE_EXCEEDED')
                if len(body) + len(part) > MAX_RESPONSE:failure('EMBEDDING_RESPONSE_TOO_LARGE')
                body.extend(part)
            result = json.loads(body)
            if not isinstance(result, dict):failure('EMBEDDING_OUTPUT_INVALID')
            return result

    def _check_pin(self, client, deadline):
        models = self._call(client, deadline, '/api/tags').get('models')
        if not isinstance(models, list):failure('EMBEDDING_OUTPUT_INVALID')
        matches = [m for m in models if isinstance(m, dict) and m.get('name') == self.identity.model]
        if len(matches) != 1 or matches[0].get('digest') != self.identity.digest:
            failure('EMBEDDING_IDENTITY_MISMATCH')

    def inspect(self):
        with self._session() as (client, deadline):
            self._check_pin(client, deadline)
            version = self._call(client, deadline, '/api/version').get('version')
            shown = self._call(client, deadline, '/api/show', {'model': self.identity.model})
            capabilities = shown.get('capabilities', [])
            if (not isinstance(version, str) or not re.fullmatch(r'[0-9A-Za-z.+-]{1,80}', version)
                    or not isinstance(capabilities, list) or 'embedding' not in capabilities):
                failure('EMBEDDING_CAPABILITY_MISMATCH')
            self._check_pin(client, deadline)
            return {'identity': asdict(self.identity), 'fingerprint': self.identity.fingerprint,
                    'ollama_version': version, 'capability': 'embedding'}

    def encode(self, texts):
        if (not isinstance(texts, list) or not 1 <= len(texts) <= MAX_BATCH
                or any(not isinstance(t, str) or not t.strip() or len(t) > MAX_TEXT
                       or any(ord(c) < 32 and c not in '\n\t' for c in t) for t in texts)):
            failure('EMBEDDING_INPUT_INVALID', 422)
        started = monotonic()
        with self._session() as (client, deadline):
            self._check_pin(client, deadline)
            result = self._call(client, deadline, '/api/embed', {
                'model': self.identity.model, 'input': texts, 'truncate': False,
                'keep_alive': '5m', 'options': {'num_gpu': 0, 'num_thread': 4}})
            if result.get('model') != self.identity.model:failure('EMBEDDING_IDENTITY_MISMATCH')
            vectors = validate_vectors(result.get('embeddings'), len(texts), self.identity.dimensions)
            self._check_pin(client, deadline)
            if monotonic() >= deadline:failure('EMBEDDING_DEADLINE_EXCEEDED')
        return vectors, {'identity_fingerprint': self.identity.fingerprint,
                         'wall_seconds': monotonic() - started, 'count': len(texts),
                         'dimensions': self.identity.dimensions, 'device_requested': 'cpu'}
