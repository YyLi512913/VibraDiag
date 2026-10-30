from contextlib import contextmanager
import threading
from vibradiag.domain.errors import ResourceBusy

class ModelExecutionGate:
    """Prototype process-local gate; not the future durable job queue."""
    def __init__(self):
        self._lock=threading.Lock()

    @contextmanager
    def acquire(self):
        if not self._lock.acquire(blocking=False):
            raise ResourceBusy('已有模型任务，请稍后重试')
        try:
            yield
        finally:
            self._lock.release()
