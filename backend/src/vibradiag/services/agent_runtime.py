"""One hosted rule executor under the existing single-process database lease."""
import logging
from threading import Event,Lock,Thread
from vibradiag.core.config import setting
from vibradiag.services import agent_task_service as service

logger=logging.getLogger(__name__)


class AgentRuntime:
    def __init__(self):
        self.guard=Lock();self.references=0;self.thread=None;self.stop_event=None

    def start(self):
        if not setting('VIBRADIAG_DATABASE_URL') or setting('VIBRADIAG_AGENT_WORKER_ENABLED','true').lower()!='true':
            return False
        with self.guard:
            if self.references:
                self.references+=1;return True
            service.recover_interrupted()
            stopped=Event()
            self.stop_event=stopped
            self.thread=Thread(target=self._loop,args=(stopped,),name='vibradiag-rule-tasks',daemon=True)
            self.references=1;self.thread.start()
            return True

    @staticmethod
    def _loop(stopped):
        while not stopped.is_set():
            try:
                if service.run_next():continue
            except Exception:
                # Never log raw database errors, payloads, paths or credentials.
                logger.error('Task executor iteration unavailable; retrying')
            stopped.wait(.25)

    def stop(self):
        with self.guard:
            if not self.references:return
            self.references-=1
            if self.references:return
            self.stop_event.set()
            self.thread.join(timeout=.5)
            # Fence an in-flight result. Numerical computation is cooperative, not hard killed.
            service.recover_interrupted()
            self.thread.join(timeout=1)
            self.thread=None;self.stop_event=None


agent_runtime=AgentRuntime()
