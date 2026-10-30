"""Fetch/verify pinned official research assets, extracting only allowlisted MATLAB members."""
import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile

import httpx
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.evaluation.datasets import DatasetManifest, audit_manifest, verified_signal

CONFIG = PROJECT_ROOT / 'datasets/public-data-v1'


def inside(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or '\\' in relative or ':' in relative:
        raise ValueError('asset path must be relative and contained')
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('asset path escapes research directory')
    return result


def check(path, item):
    if path.stat().st_size != item['bytes'] or sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError(f'Pinned bytes/hash mismatch: {item["relative_path"]}; preserve and investigate this cache')


def prepare(data_root, fetch=False, extractor=None):
    root = Path(data_root).resolve(); root.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((CONFIG / 'catalog.json').read_text(encoding='utf8'))
    manifest = DatasetManifest.model_validate_json((CONFIG / 'manifest.json').read_text(encoding='utf8'))
    audit = audit_manifest(manifest)
    downloaded = extracted = 0
    for asset in catalog['assets']:
        path = inside(root, asset['relative_path']); path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            if not fetch:
                raise ValueError(f'Missing {asset["relative_path"]}; use --fetch for original-source download')
            if not asset['url'].startswith(('https://engineering.case.edu/sites/default/files/',
                                             'https://groups.uni-paderborn.de/kat/BearingDataCenter/')):
                raise ValueError('download source outside official allowlist')
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                try:
                    count = 0
                    with httpx.stream('GET', asset['url'], follow_redirects=True, timeout=60) as response:
                        response.raise_for_status()
                        for chunk in response.iter_bytes(1024 * 1024):
                            count += len(chunk)
                            if count > asset['bytes']: raise ValueError('download exceeds pinned size')
                            temporary.write(chunk)
                    temporary.close(); check(temporary_path, asset); temporary_path.replace(path)
                    downloaded += 1
                finally:
                    temporary_path.unlink(missing_ok=True)
        check(path, asset)
        for member in asset['members']:
            target = inside(root, member['relative_path'])
            if not target.exists():
                if not extractor: raise ValueError('RAR extraction requires 7z (--extractor path); system tar may not support its filters')
                # stdout extraction avoids archive-provided filesystem paths or links.
                with tempfile.TemporaryFile() as output:
                    subprocess.run([str(extractor), 'x', '-so', str(path), member['member']],
                                   stdout=output, stderr=subprocess.PIPE, check=True, timeout=120)
                    size = output.tell()
                    if size != member['bytes']: raise ValueError('extracted member size mismatch')
                    output.seek(0); contents = output.read()
                if sha256(contents).hexdigest() != member['sha256']:
                    raise ValueError('extracted member hash mismatch')
                target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(contents)
                extracted += 1
            check(target, member)
    for record in manifest.records:
        verified_signal(record, root)
    return {'passed': True, 'assets_verified': len(catalog['assets']), 'records_verified': len(manifest.records),
            'assets_downloaded': downloaded, 'members_extracted': extracted,
            'manifest_sha256': sha256((CONFIG / 'manifest.json').read_bytes()).hexdigest(),
            'catalog_sha256': sha256((CONFIG / 'catalog.json').read_bytes()).hexdigest(), 'split_audit': audit,
            'factory_data_used': False, 'raw_data_distributed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=PROJECT_ROOT / '.runtime/public-data')
    parser.add_argument('--fetch', action='store_true', help='download missing assets from pinned official URLs')
    parser.add_argument('--extractor', type=Path)
    args = parser.parse_args()
    extractor = args.extractor or shutil.which('7z')
    local = PROJECT_ROOT / '.runtime/tools/7zip/portable/7z.exe'
    if not extractor and local.is_file(): extractor = local
    result = prepare(args.data_root, args.fetch, extractor)
    destination = PROJECT_ROOT / 'experiments/m2-t05a-data-integrity.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
