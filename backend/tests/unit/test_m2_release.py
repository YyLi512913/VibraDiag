import json
from pathlib import Path
from pydantic import TypeAdapter
import pytest

from vibradiag.analysis.matching import VERSION
from vibradiag.evaluation.holdout import read_frozen_bundle
from vibradiag.evaluation.m2_acceptance import BANDS,synthetic_observation,public_applicability
from vibradiag.schemas.tool_runs import ToolRunRequest
from vibradiag.services.tool_contract import m2_tool_contract

ROOT=Path(__file__).resolve().parents[3]


def test_handoff_matches_actual_public_schema_and_versions():
    contract=m2_tool_contract()
    assert contract['request_json_schema']==TypeAdapter(ToolRunRequest).json_schema()
    mapping=contract['request_json_schema']['discriminator']['mapping']
    assert set(mapping)=={tool['name'] for tool in contract['tools']}
    assert VERSION=='frequency-matching-v2'
    matching=next(tool for tool in contract['tools'] if tool['name']=='match_frequency_evidence')
    assert matching['algorithm_version']==VERSION and matching['config_profile']=='matching-v1'
    assert not contract['authority_boundary']['research_adapters_in_tool_allowlist']
    assert not contract['authority_boundary']['diagnostic_release_approved']


@pytest.mark.parametrize('band',BANDS)
def test_unmodulated_and_out_of_band_controls_have_no_significant_support(band):
    for case in ('unmodulated','out_of_band','weak_in_noise'):
        result=synthetic_observation(case,band)
        assert result['target_status']=='no_observed_support'
        assert result['diagnosis'] is None and not result['evidence_eligible']


@pytest.mark.parametrize('band',BANDS)
def test_beating_preserves_alternative_and_boundary_impulse_remains_in_original(band):
    beating=synthetic_observation('beating',band)
    assert beating['target_status']=='ambiguous' and 'possible_carrier_beating' in beating['confounders']
    boundary=synthetic_observation('boundary_impulse',band)
    assert boundary['original_unchanged'] and boundary['original_absolute_peak_m_s2']>100
    assert boundary['effective_segment']['start_index']==boundary['guard_samples_each_side']


def test_public_denominator_is_not_an_invented_tool_execution_or_accuracy():
    _,_,development,validation,_=read_frozen_bundle(ROOT/'datasets/public-data-v2',ROOT)
    records=development.records+validation.records
    assert len(records)==52
    for record in records:
        result=public_applicability(record)
        assert result['decision']=='unavailable' and result['missing_requirements']
        assert not result['tool_executed'] and result['diagnosis'] is None
        assert result['physical_metrics'] is None


def test_exported_handoff_has_no_schema_drift():
    saved=json.loads((ROOT/'docs/contracts/m2-tool-contract.json').read_text(encoding='utf8'))
    assert saved==m2_tool_contract()
