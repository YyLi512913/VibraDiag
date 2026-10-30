"""Controlled two-input comparison; statements are attested, never field calibration proofs."""
from hashlib import sha256
import io
import json
from uuid import UUID, uuid4
import numpy as np
from sqlalchemy import text
from vibradiag.analysis.comparison import compare_measurements, VERSION, CONFIG
from vibradiag.analysis.csv_input import parse_csv
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.domain.tool_results import NumericToolResult
from vibradiag.infrastructure.persistence.analysis_store import json_bytes
from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.schemas.comparison import ComparisonCapture, ComparisonParameters
from vibradiag.schemas.comparison_declarations import CreateComparisonDeclaration
from vibradiag.schemas.measurements import MeasurementMetadata
from vibradiag.services.diagnostic_context_service import engineer, resolve_context, input_revision
from vibradiag.services.identity_service import audit
from vibradiag.services.measurement_service import fresh, not_found, original_bytes, authorized_measurement
from vibradiag.services import persistent_analysis_service as basic_service
from vibradiag.services.matching_service import descriptor


def conflict(message):
    raise MeasurementError('COMPARISON_SOURCE_INVALID',message,status=409)


def serial(value):
    return json.loads(json.dumps(value,default=str,allow_nan=False))


def contexts(connection,current,declaration,*,write=False,current_only=False,lock=False):
    resolved={name:resolve_context(connection,current,declaration[name+'_context_id']) for name in ('before','after')}
    # Lock both measurements in a stable order, including when reversed pairs race.
    ordered=sorted(resolved.values(),key=lambda ctx:str(ctx['revision']['measurement_id']))
    records={}
    for ctx in ordered:
        rev,record=input_revision(connection,current,ctx['revision']['id'],write=write,lock=lock)
        if current_only and record['current_revision_id']!=rev['id']:
            raise MeasurementError('REVISION_CONFLICT','Comparison requires both current input revisions',status=409)
        records[rev['id']]=record
    for name,ctx in resolved.items(): ctx['record']=dict(records[ctx['revision']['id']])
    return resolved


def resolve_declaration(connection,current,identifier,**kwargs):
    row=connection.execute(text('SELECT * FROM comparison_declarations WHERE id=:id AND organization_id=:org'),
        dict(id=identifier,org=current.organization_id)).mappings().first()
    if not row: not_found()
    if sha256(json_bytes(row['declaration'])).hexdigest()!=row['declaration_sha256']:
        conflict('Comparison declaration hash mismatch')
    declaration=CreateComparisonDeclaration.model_validate(row['declaration'])
    for name in ('before_context_id','after_context_id','before_basic_id','after_basic_id','before_envelope_id','after_envelope_id'):
        if getattr(declaration,name)!=row[name]: conflict('Declaration references differ from stored bindings')
    resolved=contexts(connection,current,row,**kwargs)
    for name,ctx in resolved.items():
        if ctx['revision']['id']!=row[name+'_revision_id']: conflict('Declaration context differs from input revision')
    return row,declaration,resolved


def capture(row,declaration,name,ctx,*,normalized_hash=None):
    side=getattr(declaration,name).model_dump(mode='json')
    chain=side.pop('measurement_chain')
    for key in ('provenance','timing','load'):
        side[key]['checked']=side[key].get('source') is not None
    for value in side['metric_uncertainties'].values(): value['checked']=True
    if chain:
        side['measurement_chain']=dict(configuration_sha256=sha256(json_bytes(chain['configuration'])).hexdigest(),source=chain['source'],checked=True)
    rev=ctx['revision']; record=ctx['record']
    return ComparisonCapture.model_validate(dict(side,organization_id=record['organization_id'],measurement_id=record['id'],
        measurement_revision_id=rev['id'],file_object_id=record['file_object_id'],input_sha256=record['sha256'],
        normalized_signal_sha256=normalized_hash,basic_execution_id=row[name+'_basic_id'],envelope_execution_id=row[name+'_envelope_id'],
        metadata={key:rev[key] for key in MeasurementMetadata.model_fields},speed=ctx['frequency_parameters'].conditions,acquisition=ctx['acquisition']))


def parameters(declaration):
    tolerance=declaration.load_tolerance.model_dump(mode='json') if declaration.load_tolerance else None
    if tolerance: tolerance['checked']=True
    return ComparisonParameters.model_validate(dict(order_checked=declaration.order_source is not None,order_source=declaration.order_source,load_tolerance=tolerance))


def parent_rows(connection,current,row,resolved):
    from vibradiag.services.tool_run_service import authorized_run, confirmation_active
    parents={}; active=True
    for name,ctx in resolved.items():
        run,_=basic_service.authorized_analysis(connection,current,row[name+'_basic_id'])
        if run['status']!='completed' or run['measurement_revision_id']!=ctx['revision']['id'] or run['config_snapshot']['input_sha256']!=ctx['record']['sha256']:
            conflict('Basic parent must be completed for its declared input')
        active=active and run['confirmation_revoked_at'] is None
        artifacts=connection.execute(text('SELECT * FROM analysis_artifacts WHERE analysis_id=:id AND organization_id=:org'),
            dict(id=run['id'],org=current.organization_id)).mappings().all()
        parents[name+'_basic']=descriptor(run,artifacts,basic=True)
        envelope=row[name+'_envelope_id']
        if envelope:
            run,_,_=authorized_run(connection,current,envelope)
            if run['tool_name']!='analyze_envelope' or run['status']!='completed' or run['result_status']!='ok' or run['measurement_revision_id']!=ctx['revision']['id'] or run['context_id']!=ctx['context']['id']:
                conflict('Envelope parent must match its declared input/context')
            active=active and confirmation_active(connection,run)
            artifacts=connection.execute(text('SELECT * FROM tool_artifacts WHERE tool_run_id=:id AND organization_id=:org'),
                dict(id=envelope,org=current.organization_id)).mappings().all()
            parents[name+'_envelope']=descriptor(run,artifacts)
    return parents,bool(active)


def create_declaration(payload,principal,request_id=None):
    declaration=CreateComparisonDeclaration.model_validate(payload)
    data=declaration.model_dump(mode='json')
    with transaction() as connection:
        current=engineer(connection,principal)
        resolved=contexts(connection,current,data,write=True,current_only=True,lock=True)
        row=dict(data,organization_id=current.organization_id,
            before_revision_id=resolved['before']['revision']['id'],after_revision_id=resolved['after']['revision']['id'])
        # Validate source semantics, including synthetic/field conflicts, without
        # promoting missing conditions to checked or requiring a comparable result.
        for name,ctx in resolved.items(): capture(row,declaration,name,ctx)
        p=parameters(declaration)
        for source in (p.order_source,p.load_tolerance.source if p.load_tolerance else None):
            if source and source.kind=='synthetic_exact' and any(ctx['revision']['source_type']!='synthetic' for ctx in resolved.values()):
                raise MeasurementError('SOURCE_CONFLICT','Exact synthetic comparison declarations require synthetic inputs',status=409)
        parents,active=parent_rows(connection,current,row,resolved)
        if not active: conflict('Parent input confirmation has been revoked')
        identifier=uuid4()
        connection.execute(text('''INSERT INTO comparison_declarations
            (id,organization_id,before_revision_id,after_revision_id,before_context_id,after_context_id,
             before_basic_id,after_basic_id,before_envelope_id,after_envelope_id,declaration,declaration_sha256,parents,checked_by)
            VALUES (:id,:org,:br,:ar,:bc,:ac,:bb,:ab,:be,:ae,CAST(:data AS jsonb),:hash,CAST(:parents AS jsonb),:actor)'''),
            dict(id=identifier,org=current.organization_id,br=row['before_revision_id'],ar=row['after_revision_id'],
                bc=declaration.before_context_id,ac=declaration.after_context_id,bb=declaration.before_basic_id,ab=declaration.after_basic_id,
                be=declaration.before_envelope_id,ae=declaration.after_envelope_id,data=json_bytes(data).decode(),
                hash=sha256(json_bytes(data)).hexdigest(),parents=json_bytes(parents).decode(),actor=current.user_id))
        audit(connection,current.organization_id,current.user_id,'comparison_declaration.create','comparison_declaration',identifier,request_id)
    return identifier


def authorize(connection,current,run,*,write=False):
    row,_,resolved=resolve_declaration(connection,current,run['comparison_declaration_id'],write=write)
    if row['before_revision_id']!=run['before_revision_id'] or row['after_revision_id']!=run['measurement_revision_id']:
        conflict('Comparison execution input bindings differ from declaration')
    return dict(run,is_current_before_revision=resolved['before']['record']['current_revision_id']==row['before_revision_id'],
        before_measurement_id=resolved['before']['record']['id'])


def prepare(connection,current,payload):
    from vibradiag.services.tool_run_service import confirmation_active
    row,declaration,resolved=resolve_declaration(connection,current,payload.declaration_id,write=True,current_only=True,lock=True)
    if row['after_revision_id']!=payload.measurement_revision_id: conflict('Request must anchor the declared after revision')
    accepted=dict(organization_id=current.organization_id,requested_by=current.user_id,measurement_revision_id=row['after_revision_id'],
        confirmation_id=payload.confirmation_id,before_revision_id=row['before_revision_id'],before_confirmation_id=payload.before_confirmation_id)
    if not confirmation_active(connection,accepted):
        raise MeasurementError('CONFIRMATION_INVALID','Confirm both input revisions with your own account',status=409)
    parents,active=parent_rows(connection,current,row,resolved)
    if parents!=row['parents'] or not active: conflict('Parent snapshot or confirmation is no longer valid')
    after=resolved['after'];record=after['record']
    snapshot=dict(organization_id=str(current.organization_id),requested_by=str(current.user_id),
        measurement_revision_id=str(row['after_revision_id']),input_sha256=record['sha256'],input_byte_size=record['byte_size'],
        file_object_id=str(record['file_object_id']),confirmation_id=str(payload.confirmation_id),before_confirmation_id=str(payload.before_confirmation_id),
        comparison=serial(dict(row)),context=serial(after['context']),
        tool_name='compare_measurements',tool_version=VERSION,configuration=dict(CONFIG))
    return after['revision'],record,dict(comparison_declaration_id=row['id'],before_revision_id=row['before_revision_id'],
        before_confirmation_id=payload.before_confirmation_id,context_id=row['after_context_id']),VERSION,snapshot


def load_parents(snapshot,principal,*,require_active):
    from vibradiag.services.tool_run_service import read_bundle
    expected=snapshot['comparison']
    with transaction() as connection:
        current=fresh(connection,principal)
        row,declaration,resolved=resolve_declaration(connection,current,expected['id'])
        if serial(dict(row))!=expected: conflict('Comparison declaration differs from accepted snapshot')
    loaded={};captures={}; active=True
    for name,ctx in resolved.items():
        run,record,artifacts,blobs=basic_service.read_bundle(row[name+'_basic_id'],principal)
        if descriptor(run,artifacts,basic=True)!=row['parents'][name+'_basic']: conflict('Basic parent lineage changed')
        active=active and run['confirmation_revoked_at'] is None
        loaded[name+'_basic']=json.loads(blobs['result'])
        raw=original_bytes(FileStore(),ctx['record']);parsed=parse_csv(raw)
        factor=9.80665 if ctx['revision']['acceleration_unit']=='g' else 1.
        with np.errstate(over='ignore',under='ignore',invalid='ignore'): normalized=parsed.values*factor
        if not np.isfinite(normalized).all() or np.any((parsed.values!=0)&(normalized==0)):
            raise MeasurementError('NUMERIC_RANGE_UNSUPPORTED','Cannot normalize original samples for comparison',status=409)
        normalized[normalized==0]=0.  # Canonicalize signed zero for duplicate detection only.
        digest=sha256(normalized.astype('<f8',copy=False).tobytes()).hexdigest()
        captures[name]=capture(row,declaration,name,ctx,normalized_hash=digest)
        if row[name+'_envelope_id']:
            er,_,confirmation,artifacts,blobs=read_bundle(row[name+'_envelope_id'],principal)
            if descriptor(er,artifacts)!=row['parents'][name+'_envelope']: conflict('Envelope parent lineage changed')
            active=active and confirmation
            metadata=json.loads(blobs['result'])
            with np.load(io.BytesIO(blobs['arrays']),allow_pickle=False) as arrays:
                products={key:arrays[key] for key in arrays.files}
            loaded[name+'_envelope']=NumericToolResult(metadata['tool_name'],metadata['tool_version'],metadata['status'],metadata['summary'],products,
                tuple(metadata['missing_requirements']),tuple(metadata['limitations']),metadata['error_code'])
    # Recheck permission and parent confirmations after slow file IO.
    with transaction() as connection:
        current=fresh(connection,principal)
        _,_,resolved=resolve_declaration(connection,current,row['id'])
        current_parents,current_active=parent_rows(connection,current,row,resolved)
        if current_parents!=row['parents']: conflict('Comparison parent manifest changed')
        active=active and current_active
    if require_active and not active: conflict('Parent input confirmation was revoked')
    return dict(loaded,before=captures['before'],after=captures['after'],parameters=parameters(declaration)),bool(active)


def compute(snapshot,principal):
    inputs,_=load_parents(snapshot,principal,require_active=True)
    from vibradiag.infrastructure.compute import runner as numeric
    from vibradiag.services.analysis_runtime import runtime
    return numeric.run('comparison',inputs,lambda:compare_measurements(**inputs),check=runtime.ensure)


def recheck(connection,current,run):
    row,_,resolved=resolve_declaration(connection,current,run['comparison_declaration_id'],write=True)
    parents,active=parent_rows(connection,current,row,resolved)
    if parents!=row['parents'] or not active: conflict('Comparison parent confirmation or lineage changed')


def options(measurement_id,principal):
    items=[]
    with transaction() as connection:
        current=fresh(connection,principal); record=authorized_measurement(connection,current,measurement_id)
        rows=connection.execute(text('''SELECT * FROM comparison_declarations WHERE organization_id=:org
            AND after_revision_id=:revision ORDER BY checked_at DESC,id LIMIT 100'''),
            dict(org=current.organization_id,revision=record['current_revision_id'])).mappings().all()
        for row in rows:
            try:
                _,_,resolved=resolve_declaration(connection,current,row['id'])
            except MeasurementError as error:
                if error.status==404: continue
                raise
            before=resolved['before']['record']
            if before['current_revision_id']!=row['before_revision_id']: continue
            items.append(dict(id=str(row['id']),before_measurement_id=str(before['id']),before_revision_id=str(row['before_revision_id']),
                before_filename=before['original_filename'],checked_at=row['checked_at'],reason=row['declaration']['reason']))
    return dict(items=items,max_options=100)
