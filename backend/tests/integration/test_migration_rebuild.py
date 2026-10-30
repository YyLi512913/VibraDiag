from uuid import uuid4
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text
from vibradiag.core.paths import BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import database_url


def test_all_migrations_build_an_empty_schema_and_rollback_cleanly(database):
    owner=create_engine(database_url(migration=True),hide_parameters=True)
    schema='m1_rebuild_'+uuid4().hex
    try:
        with owner.connect() as connection:
            transaction=connection.begin()
            try:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                config=Config(str(BACKEND_ROOT/'alembic.ini'))
                config.attributes['connection']=connection
                command.upgrade(config,'head')
                count=connection.execute(text('SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema'),dict(schema=schema)).scalar_one()
                assert count==43  # 42 business tables and Alembic version table; extension/checkpoints are separate.
                assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0016_knowledge_vectors'
                checkpoints=connection.execute(text('SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema'),dict(schema=schema+'_agent_checkpoints')).scalar_one()
                assert checkpoints==4
            finally:
                transaction.rollback()
        with owner.connect() as connection:
            assert connection.execute(text('SELECT to_regnamespace(:schema)'),dict(schema=schema)).scalar_one() is None
            assert connection.execute(text('SELECT to_regnamespace(:schema)'),dict(schema=schema+'_agent_checkpoints')).scalar_one() is None
            assert connection.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one()=='0.8.6'
            assert connection.execute(text("SELECT to_regnamespace('vibradiag_extensions')")).scalar_one() is not None
    finally:
        owner.dispose()
