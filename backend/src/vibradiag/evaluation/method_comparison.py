"""T05c2 development rubric. Labels are supplied by the frozen corpus, never the model.

This scorer consumes normalized observations from a runner. It does not establish
source authenticity, semantic generalization or industrial diagnostic qualification.
"""
from math import isclose, isfinite


def score_method(expected, actual):
    sources = actual['sources']  # runner-resolved IDs and original immutable chunk text
    selections = actual['selections']
    linked = all(s['source_id'] in sources and bool(s['quote'].strip()) and
                 s['quote'] in sources[s['source_id']] for s in selections)
    relevant = bool(selections) and all(
        s['source_id'] in expected['relevant_source_ids'] and
        any(span in s['quote'] for span in expected['relevant_spans'].get(s['source_id'], []))
        for s in selections)
    refused = actual['status'] == 'refused' and not selections
    boundary = actual['unpublished'] and not actual['diagnostic_claim']
    target = refused if expected['must_refuse'] else relevant and actual['status'] == 'completed_degraded'
    return dict(citation_integrity=linked, relevant_extract=relevant,
                refusal_correct=refused if expected['must_refuse'] else None,
                boundary_preserved=boundary, met_expectations=linked and target and boundary)


def score_comparison(expected, actual):
    comparable = expected['comparison_status'] == 'comparable'
    metrics = actual['metrics']
    values_match = bool(metrics) if comparable else not metrics
    if comparable:
        for name, value in expected['relative_changes_percent'].items():
            observed = metrics.get(name, {}).get('relative_change_percent')
            values_match = values_match and isinstance(observed, (int, float)) and not isinstance(observed, bool)
            if values_match:
                values_match = isfinite(observed) and isclose(observed, value, rel_tol=0, abs_tol=1e-8)
    boundary = actual['repair_effectiveness'] is None and not actual['diagnostic_claim']
    linked = actual['both_inputs_current_and_authorized'] and actual['parents_valid']
    status = actual['comparison_status'] == expected['comparison_status']
    return dict(comparability_correct=status, numeric_expectation_met=bool(values_match),
                both_input_integrity=linked, boundary_preserved=boundary,
                met_expectations=bool(status and values_match and linked and boundary))
