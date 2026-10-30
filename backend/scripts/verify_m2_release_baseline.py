"""Reproduce frozen synthetic rules/profile sensitivity and public strict-applicability baseline."""
from collections import Counter
from hashlib import sha256
import json
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.evaluation.holdout import read_frozen_bundle
from vibradiag.evaluation.m2_acceptance import BANDS,synthetic_observation,public_applicability


def main():
    cases=[synthetic_observation(case,band) for case in
        ('AM','unmodulated','beating','out_of_band','weak_in_noise','boundary_impulse') for band in BANDS]
    assert all(row['original_unchanged'] and row['diagnosis'] is None and not row['evidence_eligible'] for row in cases)
    assert all(row['target_status']=='ambiguous' and 'possible_carrier_beating' in row['confounders'] for row in cases if row['case']=='beating')
    assert all(row['target_status'] in {'no_observed_support','not_evaluable'} for row in cases if row['case']=='unmodulated')
    _,_,development,validation,_=read_frozen_bundle(PROJECT_ROOT/'datasets/public-data-v2',PROJECT_ROOT)
    rows=[public_applicability(record) for record in development.records+validation.records]
    def groups(key):
        return {value:{'records':sum(row[key]==value for row in rows),'matching_observation_coverage':0.,
            'decision_counts':dict(Counter(row['decision'] for row in rows if row[key]==value))}
            for value in sorted({row[key] for row in rows})}
    paths=['backend/src/vibradiag/evaluation/m2_acceptance.py','backend/scripts/verify_m2_release_baseline.py',
        'backend/src/vibradiag/analysis/envelope.py','backend/src/vibradiag/analysis/bearing_frequencies.py',
        'backend/src/vibradiag/analysis/matching.py','backend/src/vibradiag/analysis/basic.py',
        'datasets/public-data-v1/manifest.json','datasets/public-data-v2/validation-manifest.json',
        'datasets/public-data-v2/catalog.json','backend/requirements.lock.txt']
    report={'passed':True,'purpose':'M2 software acceptance baseline; no factory or classifier validation',
        'synthetic_cases':cases,'profile_selection':{'retained_band_hz':[2000.,4000.],
            'reason':'Pre-existing synthetic development baseline, bounded budget and explicit response; neighbor-band observations do not establish an optimal or qualified field profile',
            'alternatives':[[1800.,4200.],[2200.,3800.]],'published_qualifications':0,
            'registered_result_sensitivity_updated':False},
        'public_baseline':{'records':rows,'by_label':groups('label'),'by_dataset':groups('dataset'),'by_split':groups('split'),
            'decision_counts':dict(Counter(row['decision'] for row in rows)),
            'effective_matching_coverage':0.,'accuracy':None,'tool_executions':0,
            'interpretation':'All-source applicability/refusal denominator, not empirical false-positive or refusal-accuracy measurement'},
        'sealed_test_signals_read':0,'factory_data_used':False,'model_called':False,
        'source_sha256':{path:sha256((PROJECT_ROOT/path).read_bytes()).hexdigest() for path in paths}}
    target=PROJECT_ROOT/'experiments/m2-release-baseline.json'
    target.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':True,'synthetic_cases':len(cases),'public_records':len(rows),'public_matching_coverage':0.,
        'target_statuses':{case:[row['target_status'] for row in cases if row['case']==case] for case in sorted({r['case'] for r in cases})}}))


if __name__=='__main__':main()
