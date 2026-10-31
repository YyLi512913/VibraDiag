"""A versioned development pass cannot silently become an independent holdout or field claim."""
from collections import defaultdict


def audit(records,previous_hashes,*,independent_annotations=False,blind_holdout=False):
    partitions=defaultdict(lambda:dict(hashes=set(),families=set()))
    for row in records:
        partitions[row['split']]['hashes'].add(row['signal_sha256']);partitions[row['split']]['families'].add(row['source_family'])
    dev=partitions['development'];prospective=partitions['prospective_synthetic']
    overlap=sorted(dev['hashes']&prospective['hashes']);families=sorted(dev['families']&prospective['families'])
    seen=sorted({r['signal_sha256'] for r in records}&set(previous_hashes));blockers=[]
    if overlap:blockers.append('exact_waveform_overlap_between_partitions')
    if seen:blockers.append('waveform_previously_used_for_development')
    if families:blockers.append('same_source_family_across_partitions')
    if not independent_annotations:blockers.append('independent_annotations_missing')
    if not blind_holdout:blockers.append('protocol_and_expected_behavior_author_visible')
    if any(r['source_family'].startswith('analytic_') for r in records):blockers.append('synthetic_controls_are_not_physical_entity_holdout')
    if not dev['hashes'] or not prospective['hashes']:blockers.append('both_partitions_required')
    # This benchmark has no verifier for physical entity and independent annotation artifacts.
    # Operator booleans or invented group labels cannot stand in for that evidence.
    blockers.append('independent_entity_and_annotation_evidence_not_verified')
    return dict(independent_holdout_admitted=not blockers,blockers=blockers,exact_waveform_overlap=overlap,previous_development_overlap=seen,
        shared_source_families=families,synthetic_waveform_bytes_disjoint=not overlap and not seen,
        diagnostic_release=False,factory_trial_approved=False)
