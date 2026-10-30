"""Reproduce the public development shape baseline without reading held-out signal files."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import platform
import time

import numpy as np
import scipy
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.evaluation.baseline import PROTOCOL, shape_baseline
from vibradiag.evaluation.datasets import DatasetManifest, audit_manifest, load_channel, verified_signal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=PROJECT_ROOT / '.runtime/public-data')
    args = parser.parse_args()
    manifest_path = PROJECT_ROOT / 'datasets/public-data-v1/manifest.json'
    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding='utf8'))
    audit = audit_manifest(manifest)
    rows = []
    for record in manifest.records:
        if record.split != 'development': continue
        start = time.perf_counter()
        signal = verified_signal(record, args.data_root)
        _, metadata = load_channel(args.data_root / record.relative_path, record.dataset, record.channel)
        result = shape_baseline(record, signal, metadata)
        result['seconds_including_verified_read'] = time.perf_counter() - start
        rows.append(result)
    files = ['datasets/public-data-v1/catalog.json', 'datasets/public-data-v1/manifest.json',
             'backend/scripts/prepare_public_data.py', 'backend/scripts/benchmark_public_data.py',
             'backend/scripts/verify_public_data_reproduction.py',
             'backend/src/vibradiag/evaluation/datasets.py', 'backend/src/vibradiag/evaluation/baseline.py',
             'backend/src/vibradiag/analysis/envelope.py', 'backend/src/vibradiag/analysis/basic.py',
             'backend/src/vibradiag/analysis/csv_input.py', 'backend/src/vibradiag/schemas/tools.py',
             'backend/src/vibradiag/domain/tool_results.py', 'backend/requirements.lock.txt',
             'backend/tests/unit/test_public_datasets.py']
    result = {'execution_completed': True, 'purpose': 'Development data integrity and shape baseline, not diagnostic accuracy',
        'protocol': PROTOCOL, 'protocol_sha256': sha256(json.dumps(PROTOCOL, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        'environment': {'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__},
        'source_sha256': {name: sha256((PROJECT_ROOT / name).read_bytes()).hexdigest() for name in files},
        'split_audit': audit, 'development_records': rows,
        'envelope_counts': dict(Counter(row['envelope']['status'] for row in rows)),
        'unavailable_reasons': dict(Counter(row['envelope']['reason'] for row in rows if row['envelope']['status'] != 'ok')),
        'accuracy': None, 'diagnostic_profiles_qualified': 0, 'factory_data_used': False, 'model_called': False,
        'limitations': ['No classifier, thresholds, label-based profile tuning or held-out performance claims',
                        'All inspected records remain development; validation/test are empty',
                        'Original units unverified: no physical RMS/amplitude or severity claim',
                        'Strict time-axis gate may reject export timing jitter; no silent resampling',
                        'Shape-only numerical proxies never become diagnostic evidence or business records']}
    destination = PROJECT_ROOT / 'experiments/m2-t05a-development-baseline.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    print(json.dumps({key: result[key] for key in ('execution_completed', 'envelope_counts', 'unavailable_reasons', 'accuracy')}))


if __name__ == '__main__':
    main()
