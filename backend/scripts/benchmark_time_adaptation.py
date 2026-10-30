"""Frozen, conditional shape sensitivity on development/validation signals; sealed test stays opaque."""
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
from vibradiag.evaluation.time_adaptation import check_protocol,inspect_research_record


def fingerprints(names):
    return {name:sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() for name in names}


def evaluate_records(records,data_root,protocol):
    if any(record.split not in {'development','validation'} for record in records):
        raise ValueError('sealed-test record in batch; no signals have been read')
    return [inspect_research_record(record,data_root,protocol) for record in records]


def freeze_candidate(protocol_path):
    receipt_path=PROJECT_ROOT/'experiments/m2-t05b2-synthetic.json'
    receipt=json.loads(receipt_path.read_text(encoding='utf8'))
    protocol_hash=sha256(protocol_path.read_bytes()).hexdigest()
    if not receipt['passed'] or receipt['protocol_sha256']!=protocol_hash:
        raise ValueError('matching successful synthetic validation is required before public-signal processing')
    if fingerprints(receipt['source_sha256'])!=receipt['source_sha256']:
        raise ValueError('synthetic validation source fingerprint changed')
    names=list(receipt['source_sha256'])+[
        'backend/scripts/benchmark_time_adaptation.py','backend/tests/unit/test_time_adaptation.py',
        'datasets/public-data-v1/manifest.json','datasets/public-data-v2/validation-manifest.json',
        'datasets/public-data-v2/catalog.json','datasets/public-data-v2/split-plan.json',
        'backend/src/vibradiag/evaluation/holdout.py']
    freeze={'purpose':'Candidate frozen after synthetic truth checks and before public feature evaluation',
        'protocol_sha256':protocol_hash,'synthetic_receipt_sha256':sha256(receipt_path.read_bytes()).hexdigest(),
        'source_sha256':fingerprints(names),
        'prior_validation_access':'Readiness/time blockers already inspected at T05b1; not a fully blind validation',
        'sealed_test_release':False}
    target=PROJECT_ROOT/'experiments/m2-t05b2-freeze.json'
    if target.exists():
        if json.loads(target.read_text(encoding='utf8'))!=freeze:
            raise ValueError('frozen research candidate changed; retain this receipt and start an explicitly versioned new experiment')
    else:
        target.write_text(json.dumps(freeze,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return freeze


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,default=PROJECT_ROOT/'.runtime/public-data')
    parser.add_argument('--verify-replay',action='store_true')
    args=parser.parse_args()
    config=PROJECT_ROOT/'datasets/public-data-v2'
    plan,catalog,development,validation,_=read_frozen_bundle(config,PROJECT_ROOT)
    protocol_path=config/'research-adaptation-protocol.json'
    protocol=json.loads(protocol_path.read_text(encoding='utf8'));check_protocol(protocol)
    freeze=freeze_candidate(protocol_path)
    selected=tuple(record for record in development.records if record.dataset=='paderborn')+validation.records
    audit=audit_manifest(DatasetManifest(schema_version='public-records-v1',purpose='research split audit',
        split_policy=development.split_policy,records=development.records+validation.records))
    rows=evaluate_records(selected,args.data_root,protocol)
    replay_verified=False
    if args.verify_replay:
        replay=evaluate_records(selected,args.data_root,protocol)
        if rows!=replay:raise ValueError('repeated research execution produced a different result')
        replay_verified=True
    def aggregate(split):
        subset=[row for row in rows if row['split']==split]
        return {'records':len(subset),'adaptation_status':dict(Counter(row['adaptation_status'] for row in subset)),
            'unavailable_reasons':dict(Counter(row['adaptation']['reason'] for row in subset if row['adaptation_status']!='ok')),
            'timebase_sensitive_count':sum(row.get('timebase_sensitivity',{}).get('timebase_sensitive',False) for row in subset),
            'per_class':{label:{'records':sum(row['label_for_inventory_only']==label for row in subset),
                'conditional_features_available':sum(row['label_for_inventory_only']==label and row.get('stored_axis_hypothesis',{}).get('status')=='ok' for row in subset)}
                for label in sorted({row['label_for_inventory_only'] for row in subset})}}
    report={'execution_completed':True,'purpose':'Conditional research feature sensitivity; not metadata repair or diagnostic accuracy',
        'environment':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__},
        'protocol':protocol,'protocol_sha256':freeze['protocol_sha256'],'source_sha256':freeze['source_sha256'],
        'freeze_receipt_sha256':sha256((PROJECT_ROOT/'experiments/m2-t05b2-freeze.json').read_bytes()).hexdigest(),
        'split_audit':audit,'partitions':{split:aggregate(split) for split in ('development','validation')},
        'records':rows,'deterministic_replay_verified':replay_verified,
        'sealed_test':{'bearing_ids':[row.bearing_id for row in plan.sealed_test],
            'signals_read':0,'members_extracted':0,'released_for_evaluation':False},
        'physical_input_readiness_changed':False,'diagnostic_profiles_qualified':0,'accuracy':None,
        'matching_runs_qualified':0,'factory_data_used':False,'model_called':False}
    target=PROJECT_ROOT/'experiments/m2-t05b2-research-features.json'
    target.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'execution_completed':True,'partitions':report['partitions'],
        'deterministic_replay_verified':replay_verified,'accuracy':None,'sealed_signals_read':0}))


if __name__=='__main__':main()
