"""Apply a frozen input-readiness protocol to validation entities, without touching sealed signals."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import platform

import numpy as np
import scipy
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.evaluation.datasets import DatasetManifest,audit_manifest
from vibradiag.evaluation.holdout import read_frozen_bundle
from vibradiag.evaluation.readiness import check_validation_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,default=PROJECT_ROOT/'.runtime/public-data')
    args = parser.parse_args()
    config = PROJECT_ROOT/'datasets/public-data-v2'
    plan,catalog,development,validation,protocol = read_frozen_bundle(config,PROJECT_ROOT)
    audit = audit_manifest(DatasetManifest(schema_version='public-records-v1',purpose='development/validation audit',
        split_policy=development.split_policy,records=development.records+validation.records))
    rows = [check_validation_record(record,args.data_root,protocol) for record in validation.records]
    labels = sorted({record.label for record in validation.records})
    files = ['datasets/public-data-v1/manifest.json','datasets/public-data-v2/split-plan.json',
        'datasets/public-data-v2/validation-protocol.json','datasets/public-data-v2/catalog.json',
        'datasets/public-data-v2/validation-manifest.json','datasets/public-data-v2/metadata-review.json',
        'backend/src/vibradiag/evaluation/datasets.py','backend/src/vibradiag/evaluation/holdout.py',
        'backend/src/vibradiag/evaluation/readiness.py','backend/scripts/prepare_public_data.py',
        'backend/scripts/prepare_holdout_data.py','backend/scripts/validate_public_readiness.py',
        'backend/scripts/verify_holdout_reproduction.py','backend/tests/unit/test_public_holdouts.py',
        'backend/requirements.lock.txt','experiments/m2-t05b-freeze.json']
    result = {'execution_completed':True,'purpose':'Frozen validation input-readiness audit, not fault-classification accuracy',
        'environment':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__},
        'plan_sha256':catalog.plan_sha256,'protocol_sha256':catalog.protocol_sha256,'protocol':protocol,
        'source_sha256':{name:sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() for name in files},
        'split_audit':audit,'validation_records':rows,'input_ready_count':sum(row['input_ready'] for row in rows),
        'blocking_reason_counts':dict(Counter(reason for row in rows for reason in row['blocking_reasons'])),
        'per_class':{label:{'records':sum(row['label']==label for row in rows),
                           'input_ready':sum(row['label']==label and row['input_ready'] for row in rows)} for label in labels},
        'sealed_test':{'bearings':[row.bearing_id for row in plan.sealed_test],
            'scope':'opaque archives retained; no members listed, extracted or parsed','signals_read':0,
            'signal_level_duplicates_checked':False,'released_for_evaluation':False},
        'accuracy':None,'prediction_count':0,'diagnostic_profiles_qualified':0,'factory_data_used':False,'model_called':False,
        'limitations':['One bearing per class per held-out split; three labels only, rolling-element class absent',
            'Known source entity and byte/hash separation does not establish statistical independence',
            'Unknown physical scale and contradictory timing remain blockers; no silent metadata overrides',
            'Test archive integrity does not replace signal/channel-level duplicate checks before eventual test release']}
    (PROJECT_ROOT/'experiments/m2-t05b-validation-readiness.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({key:result[key] for key in ('execution_completed','input_ready_count','blocking_reason_counts','per_class','accuracy')}))


if __name__ == '__main__':main()
