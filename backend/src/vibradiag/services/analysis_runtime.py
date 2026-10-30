"""M1 single-process lease and explicit interruption recovery; no durable queue."""
from threading import Lock
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from vibradiag.core.config import setting
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.database import database_url, transaction
from vibradiag.services.identity_service import audit

LEASE_KEY=9182471202


def recover_interrupted():
    with transaction() as connection:
        rows=connection.execute(text("""UPDATE analysis_runs SET status='interrupted',finished_at=now(),
            error_code='PROCESS_INTERRUPTED' WHERE status='running' RETURNING id,organization_id""")).mappings().all()
        for row in rows:
            connection.execute(text("""UPDATE idempotency_records SET state='interrupted',
                response_summary='{"error_code":"PROCESS_INTERRUPTED"}' WHERE resource_id=:id AND state='running'"""),dict(id=row['id']))
            audit(connection,row['organization_id'],None,'analysis.interrupted','analysis',row['id'])
        tool_rows=connection.execute(text("""UPDATE tool_runs SET status='interrupted',finished_at=now(),
            error_code='PROCESS_INTERRUPTED' WHERE status='running' RETURNING id,organization_id""")).mappings().all()
        for row in tool_rows:
            connection.execute(text("UPDATE tool_idempotency_records SET state='interrupted' WHERE resource_id=:id AND state='running'"),dict(id=row['id']))
            audit(connection,row['organization_id'],None,'tool.interrupted','tool_run',row['id'])
    return len(rows)+len(tool_rows)


class AnalysisRuntime:
    def __init__(self):
        self.guard=Lock();self.references=0;self.connection=None;self.owner_engine=None;self.healthy=False

    def start(self):
        with self.guard:
            if self.references:
                self.references+=1
                return
            if not setting('VIBRADIAG_DATABASE_URL'):
                return  # Experimental local mode can start without a configured PostgreSQL.
            self.owner_engine=create_engine(database_url(),poolclass=NullPool,hide_parameters=True,
                                             connect_args={'connect_timeout':5})
            self.connection=self.owner_engine.connect()
            try:
                granted=self.connection.execute(text('SELECT pg_try_advisory_lock(:key)'),dict(key=LEASE_KEY)).scalar_one()
                self.connection.commit()
                if not granted:
                    raise RuntimeError('M1 supports only one API process per database')
                recover_interrupted()
            except Exception:
                self.connection.close();self.owner_engine.dispose()
                self.connection=None;self.owner_engine=None
                raise
            self.references=1
            self.healthy=True

    def stop(self):
        with self.guard:
            if not self.references:
                return
            self.references-=1
            if not self.references:
                self.connection.close();self.owner_engine.dispose()
                self.connection=None;self.owner_engine=None
                self.healthy=False

    def ensure(self):
        with self.guard:
            if not self.references or self.connection is None or not self.healthy or self.connection.invalidated:
                raise MeasurementError('ANALYSIS_UNAVAILABLE','Analysis runtime is not active',status=503)
            try:
                self.connection.execute(text('SELECT 1'));self.connection.commit()
            except Exception:
                self.healthy=False
                raise MeasurementError('ANALYSIS_LEASE_LOST','Analysis runtime lost its database lease; restart API',status=503) from None


runtime=AnalysisRuntime()
