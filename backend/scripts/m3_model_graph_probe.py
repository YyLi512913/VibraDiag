"""Owned dedicated-database process faults around completed model receipts."""
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
from vibradiag.services.analysis_runtime import runtime
from vibradiag.services import agent_task_service as tasks,agent_model_service as models


def main(path):
    if setting('VIBRADIAG_DATABASE_URL')!=setting('VIBRADIAG_TEST_DATABASE_URL'):raise RuntimeError('Dedicated test database required')
    path=Path(path).resolve()
    if not path.is_relative_to((PROJECT_ROOT/'.runtime/model-method').resolve()) or path.name!='job.json':raise RuntimeError('Owned probe job required')
    job=json.loads(path.read_text(encoding='utf8'));identifier=UUID(job['task_id']);mode=job['mode'];folder=path.parent
    def record(name,value):
        temp=folder/(name+'.tmp');temp.write_text(json.dumps(value),encoding='utf8');temp.replace(folder/name)
    record('pid.json',dict(pid=os.getpid()));runtime.start()
    try:
        if mode=='recover':tasks.recover_interrupted()
        elif mode in {'execute','pause_after_generation','pause_after_decision_release_lease'}:
            if mode!='execute':
                original=models.run_round
                def pause(task,token,principal,phase,**kwargs):
                    result=original(task,token,principal,phase,**kwargs)
                    target='extractive_draft' if mode=='pause_after_generation' else 'decision'
                    if phase==target and not kwargs.get('read_only') and not result['reused']:
                        if mode.endswith('release_lease'):runtime.stop()
                        record('ready.json',dict(task_id=str(identifier),model_round_id=result['id'],phase=phase))
                        for _ in range(600):
                            if (folder/'release').exists():return result
                            sleep(.1)
                        raise RuntimeError('Owned probe was not released')
                    return result
                models.run_round=pause
            claimed=tasks.claim_next(identifier)
            if claimed is None:raise RuntimeError('Specific queued task required')
            tasks.execute_claimed(claimed)
        else:raise RuntimeError('Unknown fixed probe mode')
        with transaction() as c:state=c.execute(text('SELECT status FROM agent_tasks WHERE id=:id'),dict(id=identifier)).scalar_one()
        record('done.json',dict(task_id=str(identifier),status=state))
    finally:runtime.stop()


if __name__=='__main__':
    try:main(sys.argv[1])
    except Exception:raise SystemExit('Owned model graph probe failed; credentials hidden') from None
