"""Immutable exploratory graph; resolve each ID against the result and its full lineage."""
from uuid import UUID, uuid5
import json
from sqlalchemy import text
from vibradiag.infrastructure.persistence.analysis_store import json_bytes
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.services.measurement_service import fresh, not_found


def graph(identifier, result):
    """Deterministic IDs/content reconstructed for both registration and reads."""
    namespace=UUID(str(identifier)); facts=[]; evidence=[]
    def fact(key, kind, content, parents=()):
        item=dict(id=str(uuid5(namespace,'fact:'+key)),fact_key=key,kind=kind,content=content,parent_fact_ids=list(parents))
        facts.append(item); return item['id']
    if result['status']!='ok': return facts,evidence
    peaks={}
    for row in result['summary']['rows']:
        for candidate in row['candidates']:
            peak_id=candidate['peak_id']
            if peak_id not in peaks:
                peaks[peak_id]=fact('peak:'+peak_id,'observation_fact',dict(
                    peak_id=peak_id,bin=candidate['bin'],frequency_hz=candidate['frequency_hz'],
                    amplitude=candidate['amplitude_m_s2'],amplitude_unit='m/s2',frequency_unit='Hz',
                    effective_segment=result['summary']['effective_segment']))
    for row in result['summary']['rows']:
        theory=fact('target:'+row['target_id'],'calculation_fact',dict(target_id=row['target_id'],
            frequency_hz=row['target_hz'],uncertainty_hz=row['propagated_uncertainty_hz'],frequency_unit='Hz',
            harmonic=row['harmonic'],aliases=row['aliases'],auxiliary_only=row['auxiliary_only']))
        relation=fact('relation:'+row['target_id'],'matching_relation',row,[theory]+[peaks[c['peak_id']] for c in row['candidates']])
        evidence.append(dict(id=str(uuid5(namespace,'evidence:'+row['target_id'])),fact_id=relation,
            purpose='exploratory_observation',polarity=row['status'],evidence_eligible=False,
            content=dict(target_id=row['target_id'],limitations=result['limitations'],
                confounder_checks_complete=False,independent_acquisition_count=1)))
    return facts,evidence


def register(connection, run, facts, evidence):
    snapshot=run['input_snapshot']
    common=dict(org=run['organization_id'],run=run['id'],revision=run['measurement_revision_id'],context=run['context_id'],
        root=UUID(snapshot['file_object_id']),hash=snapshot['input_sha256'])
    for item in facts:
        connection.execute(text('''INSERT INTO analysis_facts
            (id,organization_id,tool_run_id,measurement_revision_id,context_id,root_file_object_id,input_sha256,
             fact_key,kind,content,parent_fact_ids) VALUES (:id,:org,:run,:revision,:context,:root,:hash,:key,:kind,CAST(:content AS jsonb),:parents)'''),
            dict(common,id=UUID(item['id']),key=item['fact_key'],kind=item['kind'],content=json_bytes(item['content']).decode(),
                parents=[UUID(p) for p in item['parent_fact_ids']]))
    for item in evidence:
        connection.execute(text('''INSERT INTO evidence_items(id,organization_id,tool_run_id,fact_id,purpose,polarity,content)
            VALUES (:id,:org,:run,:fact,:purpose,:polarity,CAST(:content AS jsonb))'''),
            dict(common,id=UUID(item['id']),fact=UUID(item['fact_id']),purpose=item['purpose'],polarity=item['polarity'],content=json_bytes(item['content']).decode()))


def resolve(identifier, principal, *, evidence=False):
    from vibradiag.services.tool_run_service import read_bundle, authorized_run
    from vibradiag.services.matching_service import load_parents
    table='evidence_items' if evidence else 'analysis_facts'
    with transaction() as connection:
        current=fresh(connection,principal)
        item=connection.execute(text(f'SELECT * FROM {table} WHERE id=:id AND organization_id=:org'),dict(id=identifier,org=current.organization_id)).mappings().first()
        if not item: not_found()
        authorized_run(connection,current,item['tool_run_id'])
    run,record,own_active,_,_=read_bundle(item['tool_run_id'],principal)
    facts,items=graph(run['id'],run['result_metadata'])
    expected=next((x for x in (items if evidence else facts) if x['id']==str(identifier)),None)
    if expected is None or any(json_bytes(json.loads(json.dumps(item[key],default=str,allow_nan=False)))!=json_bytes(value)
                              for key,value in expected.items() if key!='id'):
        raise MeasurementError('EVIDENCE_INTEGRITY','Stored fact/evidence differs from its source result',status=409)
    _,parent_active=load_parents(run['input_snapshot'],principal,require_active=False)
    with transaction() as connection:
        current=fresh(connection,principal); authorized_run(connection,current,run['id'])
    return dict(expected,tool_execution_id=str(run['id']),measurement_revision_id=str(run['measurement_revision_id']),
        context_id=str(run['context_id']),root_acquisition_refs=[dict(file_object_id=run['input_snapshot']['file_object_id'],sha256=record['sha256'])],
        is_current_revision=record['current_revision_id']==run['measurement_revision_id'],
        is_current_bearing_revision=run.get('is_current_bearing_revision'),
        validity=dict(source_integrity='verified',input_confirmations_active=bool(own_active and parent_active),
            evidence_eligible=False,purpose='exploratory_observation'))
