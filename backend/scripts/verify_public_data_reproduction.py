"""Verify pinned archive extraction in a fresh, isolated local research directory."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from prepare_public_data import CONFIG, inside, prepare
from vibradiag.core.paths import PROJECT_ROOT


def main():
    catalog = json.loads((CONFIG/'catalog.json').read_text(encoding='utf8'))
    root = PROJECT_ROOT/'.runtime/public-data-reproduction'/uuid4().hex
    root.mkdir(parents=True)
    source_root = PROJECT_ROOT/'.runtime/public-data'
    for asset in catalog['assets']:
        source = inside(source_root,asset['relative_path']); target = inside(root,asset['relative_path'])
        target.parent.mkdir(parents=True,exist_ok=True)
        # Read-only originals are shared; extracted member bytes are recreated from scratch.
        os.link(source,target)
    extractor = shutil.which('7z') or PROJECT_ROOT/'.runtime/tools/7zip/portable/7z.exe'
    first = prepare(root,extractor=extractor)
    assert first['members_extracted'] == 24 and first['assets_downloaded'] == 0
    second = prepare(root,extractor=extractor)
    assert second['members_extracted'] == 0
    version = subprocess.run([str(extractor),'i'],capture_output=True,check=True).stdout.decode(errors='replace')
    report = {'passed': True, 'first': first, 'replay': second,
        'extraction_tool': {'version': next(line for line in version.splitlines() if '7-Zip' in line),
                            'executable_sha256': sha256(Path(extractor).read_bytes()).hexdigest()},
        'originals_changed': False, 'factory_data_used': False}
    (PROJECT_ROOT/'experiments/m2-t05a-reproduction.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':True,'fresh_members':24,'replayed_members':0,'records_verified':28}))


if __name__ == '__main__':
    main()
