"""Apply versioned migrations using the separate migration account."""
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from vibradiag.core.paths import BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import database_url


def migrate():
    config=Config(str(BACKEND_ROOT/'alembic.ini'))
    engine=create_engine(database_url(migration=True),hide_parameters=True)
    with engine.begin() as connection:
        # Serializes migration attempts in this database.
        connection.exec_driver_sql('SELECT pg_advisory_xact_lock(9182471201)')
        config.attributes['connection']=connection
        command.upgrade(config,'head')
        role=connection.dialect.identifier_preparer.quote(database_url().username)
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO {role}')
        connection.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {role}')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public_agent_checkpoints TO {role}')
        connection.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public_agent_checkpoints TO {role}')
        connection.exec_driver_sql(f'REVOKE ALL ON public_agent_checkpoints.checkpoint_migrations FROM {role}')
        connection.exec_driver_sql(f'GRANT SELECT ON public_agent_checkpoints.checkpoint_migrations TO {role}')
        connection.exec_driver_sql(f'REVOKE DELETE ON ALL TABLES IN SCHEMA public_agent_checkpoints FROM {role}')
        connection.exec_driver_sql(f'REVOKE ALL ON alembic_version FROM {role}')
        connection.exec_driver_sql(f'REVOKE UPDATE,DELETE ON audit_events,measurement_revisions FROM {role}')
    engine.dispose()


if __name__=='__main__':
    try:
        migrate()
    except Exception:
        raise SystemExit('Migration failed; check schema/permissions with migration account (credentials hidden)') from None
    print('Schema upgraded to head; application grants applied')
