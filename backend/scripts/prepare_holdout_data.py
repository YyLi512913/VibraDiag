"""Verify frozen holdout archives and extract validation members only; test archives stay opaque."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import httpx
from prepare_public_data import inside, check
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.evaluation.datasets import DatasetManifest, audit_manifest, verified_signal
from vibradiag.evaluation.holdout import read_frozen_bundle

CONFIG = PROJECT_ROOT/'datasets/public-data-v2'


def ensure_archive(root,asset,fetch):
    path = inside(root,asset.relative_path); path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        if not fetch: raise ValueError('missing original archive; use --fetch')
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                count = 0
                with httpx.stream('GET',asset.url,follow_redirects=True,timeout=60) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes(1024*1024):
                        count += len(chunk)
                        if count > asset.bytes: raise ValueError('archive exceeds pinned size')
                        temporary.write(chunk)
                temporary.close(); check(temporary_path,asset.model_dump()); temporary_path.replace(path)
            finally:
                temporary_path.unlink(missing_ok=True)
    check(path,asset.model_dump())
    return path


def extract_validation(root,asset,extractor):
    # Denial precedes lookup, archive listing, subprocess execution or writing.
    if asset.split != 'validation':
        raise ValueError('sealed test archives must never be extracted by this command')
    extracted = 0
    for member in asset.members:
        target = inside(root,member.relative_path)
        if not target.exists():
            if not extractor: raise ValueError('7-Zip required: pass --extractor')
            with tempfile.TemporaryFile() as output:
                subprocess.run([str(extractor),'x','-so',str(inside(root,asset.relative_path)),member.member],
                               stdout=output,stderr=subprocess.PIPE,check=True,timeout=120)
                if output.tell() != member.bytes: raise ValueError('member size mismatch')
                output.seek(0); data = output.read()
            if sha256(data).hexdigest() != member.sha256: raise ValueError('member SHA mismatch')
            target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data); extracted += 1
        check(target,member.model_dump())
    return extracted


def prepare(data_root,fetch=False,extractor=None):
    root = Path(data_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    plan,catalog,development,validation,protocol = read_frozen_bundle(CONFIG,PROJECT_ROOT)
    audit = audit_manifest(DatasetManifest(schema_version='public-records-v1',purpose='source split audit',
        split_policy=development.split_policy,records=development.records+validation.records))
    extracted = 0
    for asset in catalog.assets:
        ensure_archive(root,asset,fetch)
        if asset.split == 'validation': extracted += extract_validation(root,asset,extractor)
    for record in validation.records: verified_signal(record,root)
    return {'passed':True,'original_archives_verified':len(catalog.assets),'validation_records_verified':len(validation.records),
        'validation_members_extracted':extracted,'sealed_test_archives_verified':len(plan.sealed_test),
        'sealed_test_members_listed':0,'sealed_test_members_extracted':0,'sealed_test_signals_parsed':0,
        'plan_sha256':catalog.plan_sha256,'protocol_sha256':catalog.protocol_sha256,
        'split_audit':audit,'sealed_archive_scope':'opaque bytes/hash only; excluded from signal-level duplicate checks',
        'factory_data_used':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,default=PROJECT_ROOT/'.runtime/public-data')
    parser.add_argument('--fetch',action='store_true')
    parser.add_argument('--extractor',type=Path)
    args = parser.parse_args()
    extractor = args.extractor or shutil.which('7z') or PROJECT_ROOT/'.runtime/tools/7zip/portable/7z.exe'
    result = prepare(args.data_root,args.fetch,extractor)
    (PROJECT_ROOT/'experiments/m2-t05b-holdout-integrity.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':main()
