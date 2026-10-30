"""Reproduce validation extraction while ensuring sealed archives are never passed to an extractor."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4
from unittest.mock import patch

import prepare_holdout_data as preparation
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.evaluation.holdout import read_frozen_bundle


def main():
    plan,catalog,*_ = read_frozen_bundle(preparation.CONFIG,PROJECT_ROOT)
    root = PROJECT_ROOT/'.runtime/holdout-reproduction'/uuid4().hex; root.mkdir(parents=True)
    for asset in catalog.assets:
        source = preparation.inside(PROJECT_ROOT/'.runtime/public-data',asset.relative_path)
        target = preparation.inside(root,asset.relative_path); target.parent.mkdir(parents=True,exist_ok=True)
        os.link(source,target)
    extractor = shutil.which('7z') or PROJECT_ROOT/'.runtime/tools/7zip/portable/7z.exe'
    real_run = preparation.subprocess.run
    calls = []
    sealed_names = {assignment.bearing_id+'.rar' for assignment in plan.sealed_test}
    def guarded_run(command,**kwargs):
        if any(str(argument).replace('\\','/').split('/')[-1] in sealed_names for argument in command):
            raise AssertionError('sealed archive reached extractor')
        calls.append(command[-1]); return real_run(command,**kwargs)
    with patch.object(preparation.subprocess,'run',side_effect=guarded_run):
        first = preparation.prepare(root,extractor=extractor)
        second = preparation.prepare(root,extractor=extractor)
    assert first['validation_members_extracted']==24 and second['validation_members_extracted']==0
    assert len(calls)==24
    for assignment in plan.sealed_test:
        assert not (root/'paderborn'/assignment.bearing_id).exists()
    result = {'passed':True,'first':first,'replay':second,'validation_extractor_calls':len(calls),
        'sealed_archive_extractor_calls':0,'sealed_test_directory_created':False,
        'extractor_sha256':sha256(Path(extractor).read_bytes()).hexdigest()}
    (PROJECT_ROOT/'experiments/m2-t05b-reproduction.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':True,'validation_extractions':24,'sealed_extractions':0,'replay_extractions':0}))


if __name__=='__main__':main()
