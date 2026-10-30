"""Ideal bearing kinematics. No model, lookup, resource access or fault decision."""
import math
import numpy as np

from vibradiag.domain.tool_results import NumericRangeError, NumericToolResult
from vibradiag.schemas.tools import BearingFrequencyParameters, validate_parameters

VERSION = 'bearing-kinematics-v1'
ROTATION = 'inner_ring_rotates_outer_fixed'
LIMITATIONS = ('Ideal kinematic expectations, not observed peaks or a fault diagnosis',
               'FTF is auxiliary; no cage-fault classification',
               '2BSF is derived from BSF and is not independent evidence',
               'Target, provenance and actor authorization must be verified by the future service adapter')


def _positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise NumericRangeError()
    return value


def _orders(z, ratio, angle):
    ratio = _positive(ratio)
    q = ratio * math.cos(math.radians(angle))
    return {name: _positive(value) for name, value in {
        'FTF': .5 * (1-q), 'BPFO': z*.5*(1-q), 'BPFI': z*.5*(1+q),
        'BSF': (1-q*q)/(2*ratio)}.items()}


def _geometry_bounds(geometry, orders):
    uncertainty = geometry.uncertainty
    if uncertainty is None:
        return None, 'geometry_uncertainty'
    d, pitch, angle = geometry.rolling_element_diameter, geometry.pitch_diameter, geometry.contact_angle_deg
    ud, up, ua = (uncertainty.rolling_element_diameter_absolute, uncertainty.pitch_diameter_absolute,
                  uncertainty.contact_angle_deg_absolute)
    if any(value == 0 for value in (ud, up, ua)) and uncertainty.source.kind != 'synthetic_exact':
        return None, 'zero_geometry_uncertainty_requires_exact_source'
    rlow = _positive((d-ud)/(pitch+up)); rhigh = _positive((d+ud)/(pitch-up))
    alow, ahigh = angle-ua, angle+ua
    qlow = rlow*math.cos(math.radians(ahigh)); qhigh = rhigh*math.cos(math.radians(alow))
    z = geometry.rolling_elements
    bounds = {'FTF': (.5*(1-qhigh), .5*(1-qlow)),
              'BPFO': (z*.5*(1-qhigh), z*.5*(1-qlow)),
              'BPFI': (z*.5*(1+qlow), z*.5*(1+qhigh)),
              'BSF': (1/(2*rhigh)-rhigh*math.cos(math.radians(alow))**2/2,
                      1/(2*rlow)-rlow*math.cos(math.radians(ahigh))**2/2)}
    if any(value > 0 for value in (ud, up, ua)):
        if all(low == high == orders[name] for name, (low, high) in bounds.items()):
            return None, 'geometry_uncertainty_below_numeric_resolution'
        bounds = {name: (float(np.nextafter(min(low, orders[name]), -np.inf)),
                         float(np.nextafter(max(high, orders[name]), np.inf))) for name, (low, high) in bounds.items()}
    else:
        bounds = {name: (value, value) for name, value in orders.items()}
    return bounds, None


def _coefficient_bounds(coefficients, orders):
    uncertainty = coefficients.uncertainty
    if uncertainty is None:
        return None, 'coefficient_uncertainty'
    if any(getattr(uncertainty, name) == 0 for name in orders) and uncertainty.source.kind != 'synthetic_exact':
        return None, 'zero_coefficient_uncertainty_requires_exact_source'
    bounds = {}
    for name, center in orders.items():
        deviation = getattr(uncertainty, name)
        low, high = center-deviation, center+deviation
        if deviation and low == high:
            return None, 'coefficient_uncertainty_below_numeric_resolution'
        bounds[name] = (float(np.nextafter(low, -np.inf)), float(np.nextafter(high, np.inf))) if deviation else (center, center)
    return bounds, None


def calculate_bearing_frequencies(parameters) -> NumericToolResult:
    request = validate_parameters(BearingFrequencyParameters, parameters)
    conditions = request.conditions
    missing = []
    if conditions.target is None: missing.append('identified_target_and_input_revision')
    if conditions.rpm is None: missing.append('current_rpm')
    if conditions.rpm_basis not in {'measured', 'synthetic_exact'}: missing.append('reliable_current_rpm_basis')
    if conditions.rpm_source is None: missing.append('rpm_source')
    elif (conditions.rpm_basis == 'synthetic_exact') != (conditions.rpm_source.kind == 'synthetic_exact'):
        missing.append('consistent_rpm_source_and_basis')
    if not conditions.rpm_checked: missing.append('explicit_rpm_check')
    if conditions.speed_condition != 'constant_steady_checked': missing.append('constant_steady_condition')
    if conditions.rotation != ROTATION: missing.append('supported_rotation_reference')
    selected = request.geometry if request.parameterization == 'geometry' else request.coefficients
    if selected is None: missing.append('bearing_parameters')
    else:
        if selected.source is None: missing.append('bearing_parameter_source')
        if not selected.checked: missing.append('explicit_bearing_parameter_check')
        if request.geometry and request.geometry.bearing_design != 'single_row_ball': missing.append('supported_single_row_ball_geometry')
        if request.coefficients:
            if request.coefficients.bsf_definition != 'spin': missing.append('explicit_bsf_spin_definition')
            if request.coefficients.rotation_assumption != ROTATION: missing.append('consistent_coefficient_rotation_reference')
    if conditions.parameters_conflict: missing.append('resolved_parameter_conflict')
    if missing:
        code = 'PARAMETER_CONFLICT' if conditions.parameters_conflict else 'OUT_OF_SCOPE' if conditions.speed_condition == 'variable' or conditions.rotation in {'both_rings_rotate', 'outer_ring_rotates_inner_fixed'} else 'PRECONDITION_MISSING'
        return NumericToolResult('calculate_bearing_frequencies', VERSION, 'unavailable',
                                 missing_requirements=tuple(missing), limitations=LIMITATIONS, error_code=code)
    try:
        fr = _positive(conditions.rpm/60)
        if request.geometry:
            geometry = request.geometry
            orders = _orders(geometry.rolling_elements, geometry.rolling_element_diameter/geometry.pitch_diameter, geometry.contact_angle_deg)
            bounds, missing_bound = _geometry_bounds(geometry, orders)
        else:
            orders = request.coefficients.values.model_dump()
            bounds, missing_bound = _coefficient_bounds(request.coefficients, orders)
        uncertainty_gaps = [missing_bound] if missing_bound else []
        u = conditions.rpm_uncertainty_rpm
        if u is None or conditions.rpm_uncertainty_source is None:
            uncertainty_gaps.append('rpm_uncertainty_with_source')
        elif u == 0 and (conditions.rpm_basis != 'synthetic_exact' or conditions.rpm_uncertainty_source.kind != 'synthetic_exact'):
            uncertainty_gaps.append('zero_rpm_uncertainty_requires_exact_source')
        du = u/60 if u is not None else None
        if u and (du == 0 or fr-du == fr+du == fr):
            uncertainty_gaps.append('rpm_uncertainty_below_numeric_resolution')
        matching_eligible = not uncertainty_gaps
        rows = {}
        for name, order in orders.items():
            order = _positive(order); frequency = _positive(order*fr)
            row = {'order': order, 'frequency_hz': frequency, 'order_interval': None,
                   'coefficient_uncertainty_absolute': None, 'frequency_hz_interval': None, 'uncertainty_hz': None}
            if bounds is not None:
                low, high = map(_positive, bounds[name])
                row['order_interval'] = [low, high]
                row['coefficient_uncertainty_absolute'] = max(order-low, high-order)
                if matching_eligible:
                    f_low, f_high = _positive(low*(fr-du)), _positive(high*(fr+du))
                    if (du or high-low) and f_low == f_high:
                        uncertainty_gaps.append('frequency_uncertainty_below_numeric_resolution')
                    else:
                        row['frequency_hz_interval'] = [f_low, f_high]
                        row['uncertainty_hz'] = max(frequency-f_low, f_high-frequency)
            rows[name] = row
        spin = rows['BSF']
        rows['2BSF'] = {key: [_positive(2*x) for x in value] if isinstance(value, list) else _positive(2*value) if value is not None and value != 0 else value
                        for key, value in spin.items()}
        rows['2BSF']['derived_from'] = 'BSF'
        return NumericToolResult('calculate_bearing_frequencies', VERSION, 'ok',
            summary={'shaft_frequency_hz': fr, 'frequency_unit': 'Hz', 'order_unit': 'shaft_order',
                     'parameterization': request.parameterization, 'frequencies': rows,
                     'uncertainty_complete': not uncertainty_gaps, 'parameters': request.model_dump(mode='json')},
            missing_requirements=tuple(dict.fromkeys(uncertainty_gaps)), limitations=LIMITATIONS)
    except (NumericRangeError, ArithmeticError, ValueError):
        return NumericToolResult('calculate_bearing_frequencies', VERSION, 'failed',
                                 limitations=LIMITATIONS, error_code='NUMERIC_RANGE_UNSUPPORTED')
