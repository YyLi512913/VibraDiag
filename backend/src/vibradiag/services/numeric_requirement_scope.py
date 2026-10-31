"""Lossless, conservative scope labels for actual numerical result requirements."""
DIAGNOSTIC_LIMITS=frozenset({'verified_acquisition_bandwidth','qualified_numeric_profile'})


def scopes(call,result):
    missing=list(result.get('result',{}).get('missing_requirements',[]));status=result.get('result_status',result['status'])
    successful=result['status']=='completed' and status in {'ok','completed'}
    diagnostic=[m for m in missing if m in DIAGNOSTIC_LIMITS] if successful and call['action_name']=='analyze_envelope' and call['payload'].get('mode')=='exploratory' else []
    matching=missing if successful and call['action_name']=='calculate_bearing_frequencies' else []
    return dict(execution_blockers=missing if not successful else [],diagnostic_release_limitations=diagnostic,
        frequency_matching_preconditions=matching,unclassified=[m for m in missing if successful and m not in diagnostic and m not in matching])


def enrich(context,history,results):
    completed=[]
    for observation,call,result in zip(context['completed'],history,results):
        item=dict(observation,requirement_scopes=scopes(call,result))
        if call['action_name']=='calculate_bearing_frequencies':
            value=result.get('result',{}).get('summary',{}).get('uncertainty_complete')
            item['frequency_uncertainty_complete']=value if type(value) is bool else None
        completed.append(item)
    return dict(context,completed=completed,candidates=[dict(c,offered_mode='exploratory',diagnostic_release_allowed=False) for c in context['candidates']],
        requirement_semantics='Raw missing_requirements are retained. Scopes describe actual result limits, not resolved prerequisites or permission to diagnose.')
