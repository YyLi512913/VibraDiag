import os
from threading import BoundedSemaphore
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from vibradiag.analysis.csv_input import MAX_FILE_BYTES
from vibradiag.api.upload_limits import LIMIT_MESSAGE
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.schemas.measurements import ConfirmationRequest, MeasurementMetadata, RevisionRequest
from vibradiag.services import measurement_service as service
from .identity import current_principal

router=APIRouter(prefix='/measurements',tags=['measurements'])
_imports=BoundedSemaphore(2)


@router.post('',status_code=201)
async def upload(request: Request,principal=Depends(current_principal)):
    if not principal.roles.intersection({'inspector','engineer'}):
        raise MeasurementError('ROLE_REQUIRED','An inspector or engineer role is required',status=403)
    if not _imports.acquire(blocking=False):
        raise MeasurementError('IMPORT_CAPACITY','Import capacity is full; retry later',status=429)
    staged=None
    try:
        try:
            async with request.form(max_files=1,max_fields=1,max_part_size=64*1024) as form:
                if set(form.keys())!={'file','metadata'} or len(form.multi_items())!=2:
                    raise MeasurementError('UPLOAD_FIELDS','Supply exactly file and metadata fields')
                source=form['file']; metadata=form['metadata']
                if not isinstance(source,UploadFile) or not isinstance(metadata,str):
                    raise MeasurementError('UPLOAD_FIELDS','file must be a CSV upload and metadata a JSON string')
                try:
                    payload=MeasurementMetadata.model_validate_json(metadata)
                except ValidationError:
                    raise MeasurementError('METADATA_INVALID','Invalid metadata; check required fields, finite numbers and associations',field='metadata') from None
                staged=FileStore().new_staging()
                size=0
                with staged.open('xb') as target:
                    while chunk:=await source.read(64*1024):
                        size+=len(chunk)
                        if size>MAX_FILE_BYTES:
                            raise MeasurementError('FILE_TOO_LARGE','CSV exceeds 32 MiB',status=413)
                        target.write(chunk)
                    target.flush(); os.fsync(target.fileno())
                return await run_in_threadpool(service.create_measurement,staged,source.filename,payload,principal,request.state.request_id)
        except HTTPException as exc:
            status=413 if exc.detail==LIMIT_MESSAGE else 422
            code='FILE_TOO_LARGE' if status==413 else 'MULTIPART_INVALID'
            raise MeasurementError(code,'Multipart upload exceeds limit' if status==413 else 'Invalid multipart upload',status=status) from None
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        _imports.release()


@router.get('')
def list_records(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),principal=Depends(current_principal)):
    return service.list_measurements(principal,page,page_size)


@router.get('/{measurement_id}')
def get_record(measurement_id:UUID,principal=Depends(current_principal)):
    return service.read_measurement(measurement_id,principal)


@router.get('/{measurement_id}/revisions')
def versions(measurement_id:UUID,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),principal=Depends(current_principal)):
    return service.list_revisions(measurement_id,principal,page,page_size)


@router.post('/{measurement_id}/revisions',status_code=201)
def revise(measurement_id:UUID,payload:RevisionRequest,request:Request,principal=Depends(current_principal)):
    return service.revise_measurement(measurement_id,payload,principal,request.state.request_id)


@router.post('/{measurement_id}/confirmations')
def confirm(measurement_id:UUID,payload:ConfirmationRequest,request:Request,principal=Depends(current_principal)):
    return service.confirm_input(measurement_id,payload.revision_id,principal,request.state.request_id)


@router.get('/{measurement_id}/original')
def original(measurement_id:UUID,request:Request,principal=Depends(current_principal)):
    raw=service.download_original(measurement_id,principal,request.state.request_id)
    return Response(raw,media_type='text/csv',headers={
        'Content-Disposition':f'attachment; filename="{measurement_id}.csv"',
        'X-Content-Type-Options':'nosniff'})
