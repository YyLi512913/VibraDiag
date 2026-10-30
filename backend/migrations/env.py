from alembic import context
from sqlalchemy import create_engine, pool
from vibradiag.infrastructure.persistence.database import database_url

if context.is_offline_mode():
    context.configure(url=database_url(migration=True), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = context.config.attributes.get('connection')
    if supplied is not None:
        context.configure(connection=supplied)
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = create_engine(database_url(migration=True), poolclass=pool.NullPool,
                               hide_parameters=True)
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
