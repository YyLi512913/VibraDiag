"""Explicit DBA operation, never imported by API startup or Alembic migrations."""
import json
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from vibradiag.infrastructure.persistence.database import database_url

SCHEMA='vibradiag_extensions'
VERSION='0.8.6'


def install(admin_url, app_url, migration_url):
    admin,app,migration=map(make_url,(admin_url,app_url,migration_url))
    if any((u.host,u.port,u.database)!=(app.host,app.port,app.database) for u in (admin,migration)):
        raise RuntimeError('All maintenance connections must target the same database')
    if admin.username in {app.username,migration.username} or app.username==migration.username:
        raise RuntimeError('Separate maintenance, migration and runtime accounts required')
    engine=create_engine(admin,hide_parameters=True,connect_args={'connect_timeout':5})
    try:
        with engine.begin() as c:
            c.exec_driver_sql('SET LOCAL search_path TO pg_catalog')
            c.exec_driver_sql('SELECT pg_advisory_xact_lock(9182471216)')
            if not c.execute(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')).scalar_one():
                raise RuntimeError('Explicit maintenance connection required')
            existing=c.execute(text('SELECT e.extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname=\'vector\'')).mappings().first()
            if existing and (existing['nspname']!=SCHEMA or existing['extversion']!=VERSION):
                raise RuntimeError('Existing vector location/version differs; no automatic move or upgrade')
            if not existing:
                if c.execute(text('SELECT 1 FROM pg_namespace WHERE nspname=:schema'),dict(schema=SCHEMA)).first():
                    raise RuntimeError('Refuse to install into a pre-existing unverified schema')
                c.exec_driver_sql(f'CREATE SCHEMA {SCHEMA}')
                c.exec_driver_sql(f'REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC')
                c.exec_driver_sql(f"CREATE EXTENSION vector WITH SCHEMA {SCHEMA} VERSION '{VERSION}'")
            for url in (app,migration):
                role=c.dialect.identifier_preparer.quote(url.username)
                c.exec_driver_sql(f'GRANT USAGE ON SCHEMA {SCHEMA} TO {role}')
                if c.execute(text('SELECT has_schema_privilege(:role,:schema,\'CREATE\')'),dict(role=url.username,schema=SCHEMA)).scalar_one():
                    raise RuntimeError('Extension schema must not be writable by application or migration account')
            return dict(installed=True,created=not bool(existing),version=VERSION,schema=SCHEMA,runtime_ddl_granted=False)
    finally:engine.dispose()


if __name__=='__main__':
    try:
        value=os.environ.get('VIBRADIAG_VECTOR_ADMIN_URL')
        if not value:raise RuntimeError('Maintenance URL required in process environment')
        print(json.dumps(install(value,database_url(),database_url(migration=True))))
    except Exception:
        raise SystemExit('Vector setup failed; check maintenance configuration and existing extension (credentials hidden)') from None
