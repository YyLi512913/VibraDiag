"""Machine-readable M2 handoff. Describes existing services; grants no authority itself."""
from pydantic import TypeAdapter
from vibradiag.analysis.envelope import VERSION as ENVELOPE_VERSION
from vibradiag.analysis.bearing_frequencies import VERSION as FREQUENCY_VERSION
from vibradiag.analysis.matching import VERSION as MATCHING_VERSION
from vibradiag.analysis.comparison import VERSION as COMPARISON_VERSION
from vibradiag.schemas.tool_runs import ToolRunRequest


def m2_tool_contract():
    return {'contract_version':'m2-agent-handoff-v1','stage':'M2 v0.2 local exploratory tools',
        'transport':{'create':'POST /api/v1/tool-runs','history':'GET /api/v1/tool-runs',
            'read':'GET /api/v1/tool-runs/{id}','facts':'GET /api/v1/facts/{id}',
            'evidence':'GET /api/v1/evidence/{id}','authorization':'Existing authenticated principal, CSRF for writes, current role and resource scopes'},
        'request_json_schema':TypeAdapter(ToolRunRequest).json_schema(),
        'tools':[
            {'name':'analyze_envelope','algorithm_version':ENVELOPE_VERSION,'available_purpose':'exploratory',
             'requires':['current input revision','own input confirmation','controlled profile_id'],
             'independent_of':['rpm','bearing geometry'],'diagnostic_profile_publication_available':False},
            {'name':'calculate_bearing_frequencies','algorithm_version':FREQUENCY_VERSION,'available_purpose':'ideal kinematic expectations',
             'requires':['current input revision','own input confirmation','authorized context_id','sourced speed and bearing parameters'],
             'missing_uncertainty':'Nominal values may be retained; matching remains unavailable'},
            {'name':'match_frequency_evidence','algorithm_version':MATCHING_VERSION,'available_purpose':'exploratory observations',
             'requires':['same-input envelope, frequency and basic parent IDs','current input revision and own confirmation','authorized context_id'],
             'config_profile':'matching-v1','states':['observed_support','no_observed_support','ambiguous','not_evaluable'],
             'significance_rule':'Only threshold-passing peaks create multiple-peak ambiguity'},
            {'name':'compare_measurements','algorithm_version':COMPARISON_VERSION,'available_purpose':'conditional numerical differences',
             'requires':['controlled declaration_id binding both inputs and parents','own after and before confirmations','both input scopes'],
             'states':['comparable','not_comparable','related_or_same_acquisition'],'repair_effectiveness':None}],
        'execution':{'states':['running','completed','failed','interrupted'],
            'numerical_states':['ok','unavailable','failed'],'completed_does_not_imply_numerical_success':True,
            'shared_m1_m2_capacity':2,'persistent_queue':False,'hard_cancel_available':False},
        'retry':{'unknown_response':'replay identical payload with original Idempotency-Key',
            'failed_or_interrupted':'explicit retry_of with a new key, after source/permission revalidation',
            'missing_conditions':'obtain valid sourced inputs; do not blindly repeat unchanged request'},
        'runtime_errors':{'401':'login required','403':'role or write permission denied','404':'resource unavailable or concealed',
            '409':'stale input/confirmation, source integrity or idempotency conflict','422':'public request validation failed',
            '429':'shared execution capacity exhausted','503':'dependency unavailable'},
        'authority_boundary':{'model_supplied_trusted_flags':False,'model_writes_asset_or_profile':False,
            'model_confirms_inputs_for_user':False,'raw_arrays_in_model_summary':False,
            'research_adapters_in_tool_allowlist':False,'diagnostic_release_approved':False,
            'side_effects':'Immutable execution/artifact/fact/audit records only; no work order, device control or maintenance action'},
        'interpretation':{'observed_support_is_fault_diagnosis':False,'no_support_is_health':False,
            'derived_figures_are_independent_acquisitions':False,'model_may_invent_evidence_ids':False,
            'unknown_source_parameters_may_be_filled_from_label_or_rag':False,
            'read_before_reusing_ids':'Resolve current authorized service resources and validity flags; JSON contract is not permission'},
        'remaining_release_blocks':['verified physical input scale/timebase','sourced speed and bearing uncertainty',
            'controlled numerical-profile qualification publication','independent diagnostic effectiveness evaluation']}
