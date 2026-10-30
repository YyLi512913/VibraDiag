"""PostgreSQL connections. Schema changes are explicit Alembic operations."""
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from vibradiag.core.config import setting


def database_url(*, migration=False):
    name = 'VIBRADIAG_MIGRATION_DATABASE_URL' if migration else 'VIBRADIAG_DATABASE_URL'
    value = setting(name)
    if not value:
        raise RuntimeError(f'{name} is not configured')
    url = make_url(value)
    if url.drivername != 'postgresql+psycopg':
        raise RuntimeError('Only postgresql+psycopg is supported')
    return url


@lru_cache(maxsize=1)
def engine():
    return create_engine(database_url(), pool_pre_ping=True, hide_parameters=True,
                         pool_size=5, max_overflow=0, connect_args={'connect_timeout': 5})


@contextmanager
def transaction():
    with engine().begin() as connection:
        yield connection
