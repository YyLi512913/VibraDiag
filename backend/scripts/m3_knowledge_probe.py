"""Dedicated-test-database process probe; not imported by API or the hosted consumer."""
import json
import os
from pathlib import Path
import sys
from time import sleep
from uuid import UUID
from sqlalchemy import text
from vibradiag.core.config import setting
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.services import agent_task_service as tasks
from vibradiag.services.analysis_runtime import runtime


def main(path):
    if setting('VIBRADIAG_DATABASE_URL')!=setting('VIBRADIAG_TEST_DATABASE_URL'):
        raise RuntimeError('Dedicated test database required')
    root=(PROJECT_ROOT/'.runtime/agent-knowledge').resolve();path=Path(path).resolve()
    if not path.is_relative_to(root) or path.name!='job.json':raise RuntimeError('Owned probe job path required')
    job=json.loads(path.read_text(encoding='utf8'));identifier=UUID(job['task_id']);mode=job['mode']
    folder=path.parent
    def record(name,value):
        temporary=folder/(name+'.tmp');temporary.write_text(json.dumps(value),encoding='utf8');temporary.replace(folder/name)
    record('pid.json',dict(pid=os.getpid()))
    runtime.start()  # The owned probe acquires the same single-process lease; never bypass ensure().
    if mode=='recover':tasks.recover_interrupted()
    elif mode in {'execute','pause_after_retrieval','pause_after_retrieval_release_lease'}:
        if mode.startswith('pause_after_retrieval'):
            from vibradiag.services import knowledge_index_service as index
            original=index.search
            def pause(*args,**kwargs):
                result=original(*args,**kwargs)
                if mode=='pause_after_retrieval_release_lease':runtime.stop()  # Simulate loss of the old executor's lease before a late acknowledgement.
                record('ready.json',dict(task_id=str(identifier),retrieval_id=result['id']))
                for _ in range(600):
                    if (folder/'release').exists():return result
                    sleep(.1)
                raise RuntimeError('Probe not released')
            index.search=pause
        claimed=tasks.claim_next(identifier)
        if claimed is None:raise RuntimeError('Specific queued probe task required')
        tasks.execute_claimed(claimed)
    else:raise RuntimeError('Unknown fixed probe mode')
    with transaction() as c:
        state=c.execute(text('SELECT status FROM agent_tasks WHERE id=:id'),dict(id=identifier)).scalar_one()
    record('done.json',dict(task_id=str(identifier),status=state))
    runtime.stop()


if __name__=='__main__':
    try:main(sys.argv[1])
    except Exception:raise SystemExit('Owned knowledge probe failed; credentials hidden') from None
