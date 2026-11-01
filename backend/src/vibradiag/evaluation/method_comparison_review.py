"""Post-measurement audit v1: runtime rejection must not earn semantic-refusal credit.

Consumes the original frozen receipt, without re-running providers or changing its
pre-registered scores. Lifecycle coverage is separate from content functionality.
"""


def review(receipt, protocol):
    cases=protocol['method_cases']+protocol['comparison_cases']
    identifiers=[r['case_id'] for r in receipt['rows']]
    if len(identifiers)!=len(set(identifiers)) or set(identifiers)!={c['id'] for c in cases}:
        raise ValueError('Exactly one original observation per registered case required')
    original={r['case_id']:r for r in receipt['rows']};rows=[]
    for case in cases:
        row=original[case['id']];metric=row['metrics'];rounds=row['model_rounds']
        if row['kind']=='comparison':
            group='comparison';passed=metric['met_expectations'];classification='comparison_rubric_pass' if passed else 'comparison_rubric_failure'
        else:
            group='method_lifecycle' if case['withdraw_after_generation'] else 'method_functional'
            generated=any(r['phase']=='extractive_draft' and r['state']=='completed' and r['trace'] for r in rounds)
            failed=any(r['state']!='completed' for r in rounds)
            safe=row['status']=='refused' and not (row['draft'] or {}).get('observations')
            if case['withdraw_after_generation']:
                observed_generation=any(r['phase']=='extractive_draft' and r['trace'] for r in rounds)
                passed=bool(metric['met_expectations'] and metric['lifecycle_injected'] and observed_generation and safe)
                classification='withdrawal_boundary_pass' if passed else 'withdrawal_not_exercised_or_failed'
            elif failed:
                passed=False;classification='runtime_rejection_not_semantic_refusal'
            elif not case['documents']:
                searched=any(c['action']=='search_published_knowledge' and c['state']=='unavailable' for c in row['calls'])
                passed=bool(metric['met_expectations'] and safe and searched and row['error_code']=='PUBLISHED_KNOWLEDGE_UNAVAILABLE')
                classification='empty_corpus_refusal' if passed else 'empty_corpus_not_verified'
            elif case['must_refuse']:
                passed=bool(metric['met_expectations'] and safe and generated and row['error_code']=='MODEL_NO_SUPPORTED_EXTRACT')
                classification='unsupported_source_refusal' if passed else 'source_refusal_not_verified'
            else:
                passed=bool(metric['met_expectations'] and generated)
                classification='supported_extract' if passed else 'supported_extract_missing'
        rows.append(dict(case_id=case['id'],group=group,classification=classification,passed=bool(passed),original_rubric_met=metric['met_expectations']))
    return dict(review_version='method-comparison-outcome-audit-v1',post_measurement_review=True,model_calls_added=0,rows=rows,
                summary={group:dict(runs=sum(r['group']==group for r in rows),passed=sum(r['group']==group and r['passed'] for r in rows)) for group in ('method_functional','method_lifecycle','comparison')},
                all_cases_met_expectations=all(r['passed'] for r in rows),independent_holdout=False,semantic_quality_qualified=False,diagnostic_release=False,factory_trial_approved=False)
