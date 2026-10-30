"""Frozen entity holdouts. Test archives are opaque to the validation runner."""
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from vibradiag.evaluation.datasets import DatasetManifest, Digest, Strict, Text

BearingID = Annotated[str, Field(pattern=r'^(K00[1-6]|KA\d{2}|KI\d{2}|KB\d{2})$')]


class BearingAssignment(Strict):
    bearing_id: BearingID
    label: Literal['healthy_reference', 'inner_race', 'outer_race']
    origin: Literal['healthy_reference', 'artificial_electric_engraver', 'accelerated_lifetime_test']


class SplitPlan(Strict):
    version: Literal['paderborn-entity-holdout-plan-v1']
    frozen_on: Text
    purpose: Text
    development_manifest: Literal['datasets/public-data-v1/manifest.json']
    development_manifest_sha256: Digest
    validation: Annotated[tuple[BearingAssignment, ...], Field(min_length=3)]
    sealed_test: Annotated[tuple[BearingAssignment, ...], Field(min_length=3)]
    conditions: Annotated[tuple[Annotated[str, Field(pattern=r'^N\d{2}_M\d{2}_F\d{2}$')], ...], Field(min_length=1)]
    validation_repetitions: Annotated[tuple[Annotated[int, Field(strict=True, ge=1, le=20)], ...], Field(min_length=1)]
    channel: Literal['vibration_1']
    sealed_policy: Text
    evaluation_scope: Text
    label_source: Text
    license: Literal['CC-BY-NC-4.0']
    commercial_distribution_approved: Literal[False]

    @model_validator(mode='after')
    def partitions(self):
        ids = [row.bearing_id for row in (*self.validation, *self.sealed_test)]
        if len(ids) != len(set(ids)):
            raise ValueError('bearing entity cannot occur in multiple assignments')
        required = {'healthy_reference', 'inner_race', 'outer_race'}
        if any({row.label for row in rows} != required for rows in (self.validation, self.sealed_test)):
            raise ValueError('both holdout partitions must cover the declared three-class subset')
        if len(set(self.conditions)) != len(self.conditions) or len(set(self.validation_repetitions)) != len(self.validation_repetitions):
            raise ValueError('conditions and repetitions must be unique')
        return self


def planned_members(plan, bearing_id):
    if bearing_id not in {row.bearing_id for row in plan.validation}:
        raise ValueError('sealed or unassigned bearing cannot be extracted by the validation runner')
    return tuple(f'{bearing_id}/{condition}_{bearing_id}_{repeat}.mat'
                 for condition in plan.conditions for repeat in plan.validation_repetitions)


class LockedMember(Strict):
    member: Text
    relative_path: Text
    sha256: Digest
    bytes: Annotated[int, Field(strict=True, ge=1, le=32*1024*1024)]


class HoldoutAsset(Strict):
    bearing_id: BearingID
    split: Literal['validation', 'sealed_test']
    relative_path: Text
    url: Text
    sha256: Digest
    bytes: Annotated[int, Field(strict=True, ge=1, le=512*1024*1024)]
    members: tuple[LockedMember, ...]

    @model_validator(mode='after')
    def source(self):
        if self.relative_path != f'paderborn/{self.bearing_id}.rar' or self.url != f'https://groups.uni-paderborn.de/kat/BearingDataCenter/{self.bearing_id}.rar':
            raise ValueError('asset must bind exact bearing and official archive path')
        if self.split == 'sealed_test' and self.members:
            raise ValueError('sealed test catalog must not contain extracted member definitions')
        for member in self.members:
            if not member.member.startswith(self.bearing_id+'/') or member.relative_path != 'paderborn/'+member.member:
                raise ValueError('member must bind its original bearing path')
        return self


class HoldoutCatalog(Strict):
    version: Literal['paderborn-heldout-assets-v1']
    plan_sha256: Digest
    protocol_sha256: Digest
    assets: Annotated[tuple[HoldoutAsset, ...], Field(min_length=6)]


def verify_bindings(plan, catalog, development, validation):
    assigned = {row.bearing_id: split for split in ('validation', 'sealed_test') for row in getattr(plan,split)}
    actual = {row.bearing_id: row.split for row in catalog.assets}
    if assigned != actual or len(catalog.assets) != len(actual):
        raise ValueError('catalog assets differ from frozen entity assignments')
    development_groups = {row.entity_group for row in development.records}
    if any(f'paderborn-bearing-{bearing}' in development_groups for bearing in assigned):
        raise ValueError('held-out bearing already belongs to development')
    expected = set()
    for asset in catalog.assets:
        if asset.split == 'validation':
            members = set(planned_members(plan,asset.bearing_id))
            if {m.member for m in asset.members} != members or len(asset.members) != len(members):
                raise ValueError('validation members differ from frozen acquisition plan')
            expected.update('paderborn/'+member for member in members)
    if {r.relative_path for r in validation.records} != expected or len(validation.records) != len(expected):
        raise ValueError('validation manifest differs from frozen acquisition plan')
    for record in validation.records:
        binding = next((asset,member) for asset in catalog.assets for member in asset.members if member.relative_path == record.relative_path)
        asset,member = binding
        assignment = next(row for row in plan.validation if row.bearing_id == asset.bearing_id)
        expected_id = 'pu-'+Path(member.member).stem.replace('_','-')
        if (record.dataset != 'paderborn' or record.split != 'validation' or record.previously_used_for_development
                or record.entity_group != f'paderborn-bearing-{asset.bearing_id}' or record.label != assignment.label
                or record.file_sha256 != member.sha256 or record.channel != plan.channel
                or record.source_url != asset.url or record.license_status != 'CC-BY-NC-4.0'
                or record.record_id != expected_id
                or record.acquisition_root != 'paderborn-'+Path(member.member).stem.replace('_','-')):
            raise ValueError('validation record identities disagree with frozen bindings')


def read_frozen_bundle(config, project_root):
    config = Path(config); project_root = Path(project_root)
    plan_path = config/'split-plan.json'; protocol_path = config/'validation-protocol.json'
    plan = SplitPlan.model_validate_json(plan_path.read_text(encoding='utf8'))
    catalog = HoldoutCatalog.model_validate_json((config/'catalog.json').read_text(encoding='utf8'))
    if sha256(plan_path.read_bytes()).hexdigest() != catalog.plan_sha256 or sha256(protocol_path.read_bytes()).hexdigest() != catalog.protocol_sha256:
        raise ValueError('frozen plan or validation protocol fingerprint changed')
    development_path = project_root/plan.development_manifest
    if sha256(development_path.read_bytes()).hexdigest() != plan.development_manifest_sha256:
        raise ValueError('frozen development inventory changed')
    development = DatasetManifest.model_validate_json(development_path.read_text(encoding='utf8'))
    development_assets = json.loads((development_path.parent/'catalog.json').read_text(encoding='utf8'))['assets']
    old_hashes = {asset['sha256'] for asset in development_assets}
    new_hashes = [asset.sha256 for asset in catalog.assets]
    if len(set(new_hashes)) != len(new_hashes) or old_hashes.intersection(new_hashes):
        raise ValueError('original archive bytes repeat across declared bearing entities')
    validation = DatasetManifest.model_validate_json((config/'validation-manifest.json').read_text(encoding='utf8'))
    verify_bindings(plan,catalog,development,validation)
    protocol = json.loads(protocol_path.read_text(encoding='utf8'))
    if (protocol['version'] != 'public-readiness-validation-v1' or protocol['time_axis_relative_tolerance'] != .001
            or protocol['sampling_rate_hz'] != 64000. or protocol['channel'] != plan.channel):
        raise ValueError('unsupported frozen readiness protocol')
    return plan,catalog,development,validation,protocol
