"""Read-only reconciliation. No deletion of originals, artifacts or staging files."""
from datetime import datetime, timezone
from sqlalchemy import text

from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.file_store import FileStore
from vibradiag.infrastructure.persistence.analysis_store import AnalysisStore
from vibradiag.infrastructure.persistence.tool_store import ToolStore


def reconcile_files(connection):
    store=FileStore()
    rows=connection.execute(text('SELECT id,storage_key,sha256,byte_size,state FROM file_objects')).mappings().all()
    referenced={row['storage_key'] for row in rows}
    issues=[]
    for row in rows:
        try:
            store.read_verified(row['storage_key'],row['sha256'],row['byte_size'])
        except MeasurementError as error:
            issues.append({'file_object_id':str(row['id']),'code':error.code})
        except OSError:
            issues.append({'file_object_id':str(row['id']),'code':'STORAGE_UNAVAILABLE'})
    orphans=[]
    base=store.root/'originals'
    if base.exists():
        for path in base.rglob('*.csv'):
            if path.is_symlink() or not path.resolve().is_relative_to(store.root):
                continue
            key=path.relative_to(store.root).as_posix()
            if key not in referenced:
                orphans.append(key)
    staging=[]
    if store.staging.exists():
        now=datetime.now(timezone.utc).timestamp()
        for path in store.staging.glob('*.part'):
            if path.is_symlink() or not path.resolve().is_relative_to(store.root):
                continue
            staging.append({'key':path.relative_to(store.root).as_posix(),
                            'age_seconds':max(0,int(now-path.stat().st_mtime))})
    artifacts=connection.execute(text('SELECT id,storage_key,sha256,byte_size FROM analysis_artifacts')).mappings().all()
    artifact_keys={row['storage_key'] for row in artifacts}
    for row in artifacts:
        try:
            AnalysisStore().read_verified(row['storage_key'],row['sha256'],row['byte_size'])
        except MeasurementError as error:
            issues.append({'artifact_id':str(row['id']),'code':error.code})
        except OSError:
            issues.append({'artifact_id':str(row['id']),'code':'STORAGE_UNAVAILABLE'})
    unreferenced_artifacts=[]
    if (store.root/'analyses').exists():
        for path in (store.root/'analyses').rglob('*'):
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(store.root):
                key=path.relative_to(store.root).as_posix()
                if key not in artifact_keys:
                    unreferenced_artifacts.append(key)
    analysis_staging=[]
    if store.staging.exists():
        for path in store.staging.glob('analysis-*'):
            if path.is_dir() and not path.is_symlink() and path.resolve().is_relative_to(store.root):
                analysis_staging.append(path.relative_to(store.root).as_posix())
    tool_artifacts=connection.execute(text('SELECT * FROM tool_artifacts')).mappings().all()
    tool_keys={row['storage_key'] for row in tool_artifacts}
    for row in tool_artifacts:
        try:
            # Tool bundles have their own 64MiB limit, beyond the original CSV limit.
            with ToolStore().resolve(row['storage_key']).open('rb') as source:
                from hashlib import sha256
                payload=source.read(64*1024*1024+1)
            if len(payload)!=row['byte_size'] or sha256(payload).hexdigest()!=row['sha256']:
                raise MeasurementError('ARTIFACT_INTEGRITY','Tool artifact hash mismatch',status=409)
        except MeasurementError as error:
            issues.append({'tool_artifact_id':str(row['id']),'code':error.code})
        except OSError:
            issues.append({'tool_artifact_id':str(row['id']),'code':'STORAGE_UNAVAILABLE'})
    tool_orphans=[]
    if (store.root/'tools').exists():
        for path in (store.root/'tools').rglob('*'):
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(store.root):
                key=path.relative_to(store.root).as_posix()
                if key not in tool_keys:
                    tool_orphans.append(key)
    tool_staging=[]
    if store.staging.exists():
        tool_staging=[p.relative_to(store.root).as_posix() for p in store.staging.glob('tool-*')
            if p.is_dir() and not p.is_symlink() and p.resolve().is_relative_to(store.root)]
    return {'mode':'report_only','referenced_files':len(rows),'referenced_artifacts':len(artifacts),'issues':issues,
            'unreferenced_originals':orphans,'unreferenced_analysis_files':unreferenced_artifacts,
            'staging_files':staging,'analysis_staging_directories':analysis_staging,'deleted_files':0,
            'referenced_tool_artifacts':len(tool_artifacts),'unreferenced_tool_files':tool_orphans,
            'tool_staging_directories':tool_staging}
