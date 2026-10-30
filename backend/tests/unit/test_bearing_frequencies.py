from copy import deepcopy
from decimal import Decimal, localcontext
from itertools import product
import json
import math
from uuid import uuid4

import pytest

from vibradiag.analysis.bearing_frequencies import calculate_bearing_frequencies
from vibradiag.domain.tool_results import ToolInputError

EXACT={'kind':'synthetic_exact','reference':'synthetic-geometry-Z8-d10-D40','version':'v1'}
GEOMETRY={'rolling_elements':8,'rolling_element_diameter':10.,'pitch_diameter':40.,'length_unit':'mm',
    'contact_angle_deg':0.,'bearing_design':'single_row_ball','source':EXACT,'checked':True,
    'uncertainty':{'rolling_element_diameter_absolute':0.,'pitch_diameter_absolute':0.,
                   'contact_angle_deg_absolute':0.,'source':EXACT}}


def request():
    return {'parameterization':'geometry','geometry':deepcopy(GEOMETRY),'conditions':{
        'target':{'measurement_revision_id':str(uuid4()),'component_id':str(uuid4()),'bearing_revision_id':str(uuid4())},
        'rpm':1800.,'rpm_basis':'synthetic_exact','rpm_source':EXACT,'rpm_checked':True,
        'rpm_uncertainty_rpm':0.,'rpm_uncertainty_source':EXACT,
        'speed_condition':'constant_steady_checked','rotation':'inner_ring_rotates_outer_fixed'}}


def coefficient_request():
    p=request();del p['geometry'];p['parameterization']='coefficients'
    p['coefficients']={'values':{'FTF':.375,'BPFO':3.,'BPFI':5.,'BSF':1.875},'unit':'shaft_order',
        'bsf_definition':'spin','rotation_assumption':'inner_ring_rotates_outer_fixed',
        'applicability':'Same synthetic ideal fixture, not a manufacturer model',
        'source':EXACT,'checked':True,'uncertainty':{'FTF':0.,'BPFO':0.,'BPFI':0.,'BSF':0.,'source':EXACT}}
    return p


def test_closed_form_geometry_rpm_scaling_and_alias_lineage():
    p=request();first=calculate_bearing_frequencies(p)
    assert first.status=='ok' and first.summary['uncertainty_complete']
    expected={'FTF':11.25,'BPFO':90,'BPFI':150,'BSF':56.25,'2BSF':112.5}
    for name,target in expected.items():
        row=first.summary['frequencies'][name]
        assert row['frequency_hz']==pytest.approx(target,abs=1e-10,rel=1e-10)
        assert row['frequency_hz_interval']==[target,target] and row['uncertainty_hz']==0
    assert first.summary['frequencies']['2BSF']['derived_from']=='BSF'
    assert 'fault_class' not in first.summary
    p['conditions']['rpm']=900.
    second=calculate_bearing_frequencies(p)
    for name,target in expected.items():
        assert second.summary['frequencies'][name]['frequency_hz']==pytest.approx(target/2,abs=1e-10)
    json.dumps(first.metadata(),allow_nan=False)


def test_equal_units_and_coefficient_path_match_geometry():
    p=request();base=calculate_bearing_frequencies(p)
    p['geometry']['rolling_element_diameter']=.01;p['geometry']['pitch_diameter']=.04;p['geometry']['length_unit']='m'
    meters=calculate_bearing_frequencies(p);coefficients=calculate_bearing_frequencies(coefficient_request())
    for name,row in base.summary['frequencies'].items():
        assert meters.summary['frequencies'][name]['frequency_hz']==pytest.approx(row['frequency_hz'],abs=1e-10)
        assert coefficients.summary['frequencies'][name]['frequency_hz']==pytest.approx(row['frequency_hz'],abs=1e-10)


@pytest.mark.parametrize('field,value',[('rolling_elements',True),('rolling_elements',8.0),('rolling_elements',2),
    ('rolling_elements',65),('rolling_element_diameter',40.),('rolling_element_diameter',0.),
    ('pitch_diameter',float('inf')),('contact_angle_deg',90.),('contact_angle_deg',-1.),
    ('length_unit','inch'),('checked','true')])
def test_illegal_geometry_is_an_input_error(field,value):
    p=request();p['geometry'][field]=value
    with pytest.raises(ToolInputError):calculate_bearing_frequencies(p)


@pytest.mark.parametrize('field,value',[('rpm',True),('rpm',float('nan')),('rpm',0.),
    ('rpm_uncertainty_rpm',1800.),('rpm_uncertainty_rpm',-1.)])
def test_illegal_speed_is_an_input_error(field,value):
    p=request();p['conditions'][field]=value
    with pytest.raises(ToolInputError):calculate_bearing_frequencies(p)


@pytest.mark.parametrize('field,value',[('rpm',None),('target',None),('rpm_source',None),('rpm_checked',False),
    ('rpm_basis','estimated'),('rpm_basis','rated_reference'),('speed_condition','not_established'),
    ('speed_condition','variable'),('rotation','both_rings_rotate'),('rotation','outer_ring_rotates_inner_fixed')])
def test_missing_and_out_of_scope_conditions_do_not_produce_frequencies(field,value):
    p=request();p['conditions'][field]=value
    result=calculate_bearing_frequencies(p)
    assert result.status=='unavailable' and not result.summary and not result.arrays and result.missing_requirements
    json.dumps(result.metadata(),allow_nan=False)


@pytest.mark.parametrize('field,value',[('source',None),('checked',False),('bearing_design','double_row_ball'),('bearing_design','unknown')])
def test_parameter_provenance_and_design_are_preconditions(field,value):
    p=request();p['geometry'][field]=value
    result=calculate_bearing_frequencies(p)
    assert result.status=='unavailable' and not result.summary


def test_missing_uncertainty_keeps_nominal_values_but_blocks_matching():
    p=request();p['geometry']['uncertainty']=None;p['conditions']['rpm_uncertainty_rpm']=None
    result=calculate_bearing_frequencies(p)
    assert result.status=='ok' and not result.summary['uncertainty_complete']
    assert result.summary['frequencies']['BPFO']['frequency_hz']==90
    assert result.summary['frequencies']['BPFO']['frequency_hz_interval'] is None
    assert 'geometry_uncertainty' in result.missing_requirements and 'rpm_uncertainty_with_source' in result.missing_requirements


def test_measured_speed_with_nonzero_sourced_uncertainty_is_supported():
    p=request();source={'kind':'dataset_metadata','reference':'documented-speed-bound','version':'v1'}
    p['conditions'].update(rpm_basis='measured',rpm_source=source,rpm_uncertainty_rpm=5.,rpm_uncertainty_source=source)
    result=calculate_bearing_frequencies(p)
    assert result.status=='ok' and result.summary['uncertainty_complete']
    assert result.summary['frequencies']['BPFO']['uncertainty_hz']==pytest.approx(.25,abs=1e-10)
    assert result.summary['parameters']['conditions']['rpm_source']['kind']=='dataset_metadata'


def test_zero_uncertainty_without_exact_source_is_not_invented_precision():
    p=request();manufacturer={'kind':'manufacturer_document','reference':'nominal sheet, not a tolerance guarantee','version':'v1'}
    p['geometry']['uncertainty']['source']=manufacturer
    p['conditions'].update(rpm_basis='measured',rpm_source=manufacturer,rpm_uncertainty_source=manufacturer)
    result=calculate_bearing_frequencies(p)
    assert result.status=='ok' and not result.summary['uncertainty_complete']
    assert result.summary['frequencies']['BPFO']['frequency_hz_interval'] is None
    assert 'zero_rpm_uncertainty_requires_exact_source' in result.missing_requirements


def test_uncertainty_bounds_contain_independent_decimal_parameter_box():
    p=request();p['geometry']['contact_angle_deg']=20.
    p['geometry']['uncertainty'].update(rolling_element_diameter_absolute=.1,pitch_diameter_absolute=.2,contact_angle_deg_absolute=.5)
    p['conditions']['rpm_uncertainty_rpm']=5.
    result=calculate_bearing_frequencies(p)
    assert result.status=='ok' and result.summary['uncertainty_complete']
    with localcontext() as ctx:
        ctx.prec=60
        for d,pitch,angle,rpm in product(('9.9','10','10.1'),('39.8','40','40.2'),(19.5,20,20.5),('1795','1800','1805')):
            ratio=Decimal(d)/Decimal(pitch);cosine=Decimal.from_float(math.cos(math.radians(angle)));fr=Decimal(rpm)/60
            q=ratio*cosine
            independent={'FTF':fr*(1-q)/2,'BPFO':4*fr*(1-q),'BPFI':4*fr*(1+q),
                         'BSF':fr*(1/ratio-ratio*cosine*cosine)/2}
            for name,frequency in independent.items():
                low,high=result.summary['frequencies'][name]['frequency_hz_interval']
                assert low<=float(frequency)<=high
    spin=result.summary['frequencies']['BSF']['frequency_hz_interval']
    assert result.summary['frequencies']['2BSF']['frequency_hz_interval']==[2*x for x in spin]


def test_coefficient_and_speed_cross_term_is_preserved():
    p=coefficient_request();p['coefficients']['uncertainty'].update(FTF=.01,BPFO=.1,BPFI=.1,BSF=.05)
    p['conditions']['rpm_uncertainty_rpm']=6.
    result=calculate_bearing_frequencies(p)
    low,high=result.summary['frequencies']['BPFO']['frequency_hz_interval']
    # Independent upper endpoint: (3 + .1) * (30 + .1), includes .1*.1.
    assert high==pytest.approx(93.31,abs=1e-10)
    assert low==pytest.approx(86.71,abs=1e-10)


def test_conflicts_and_bsf_definitions_do_not_silently_choose_a_value():
    p=request();p['conditions']['parameters_conflict']=True
    assert calculate_bearing_frequencies(p).error_code=='PARAMETER_CONFLICT'
    c=coefficient_request();c['coefficients']['bsf_definition']='twice_spin'
    assert calculate_bearing_frequencies(c).status=='unavailable'
    c=coefficient_request();c['coefficients']['unit']='Hz'
    with pytest.raises(ToolInputError):calculate_bearing_frequencies(c)
    both=request();both['coefficients']=coefficient_request()['coefficients']
    with pytest.raises(ToolInputError):calculate_bearing_frequencies(both)


def test_missing_parameters_and_invalid_uncertainty_intervals():
    p=request();p['geometry']=None
    assert calculate_bearing_frequencies(p).status=='unavailable'
    p=request();p['geometry']['uncertainty']['rolling_element_diameter_absolute']=10.
    with pytest.raises(ToolInputError):calculate_bearing_frequencies(p)


def test_speed_uncertainty_below_float_resolution_is_not_reported_as_exact():
    p=request();p['conditions']['rpm_uncertainty_rpm']=1e-100
    result=calculate_bearing_frequencies(p)
    assert result.status=='ok' and not result.summary['uncertainty_complete']
    assert 'rpm_uncertainty_below_numeric_resolution' in result.missing_requirements
    assert result.summary['frequencies']['FTF']['frequency_hz_interval'] is None
    p=coefficient_request();p['coefficients']['uncertainty']['FTF']=.375
    with pytest.raises(ToolInputError):calculate_bearing_frequencies(p)


@pytest.mark.parametrize('kind',['speed_underflow','diameter_ratio_underflow','frequency_overflow'])
def test_finite_but_unrepresentable_computation_returns_no_fake_numbers(kind):
    p=request()
    if kind=='speed_underflow':p['conditions']['rpm']=5e-324
    elif kind=='diameter_ratio_underflow':
        p['geometry'].update(rolling_element_diameter=1e-300,pitch_diameter=1e300)
    else:
        p['conditions']['rpm']=1e308
        p['geometry']['rolling_element_diameter']=1e-6  # Large finite BSF order overflows only after multiplying fr.
    result=calculate_bearing_frequencies(p)
    assert result.status=='failed' and result.error_code=='NUMERIC_RANGE_UNSUPPORTED' and not result.summary
    json.dumps(result.metadata(),allow_nan=False)
