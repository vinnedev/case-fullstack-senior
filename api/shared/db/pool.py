from collections.abc import Generator
from contextlib import contextmanager
from typing import cast

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from shared.config.settings import get_settings

DictConnection = Connection[DictRow]

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.db_pool_size + settings.db_max_overflow,
            timeout=settings.db_pool_timeout_s,
            max_lifetime=settings.db_pool_recycle_s,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def connect_dict(url: str) -> DictConnection:
    return Connection[DictRow].connect(url, row_factory=dict_row)


@contextmanager
def connection_scope() -> Generator[DictConnection, None, None]:
    with get_pool().connection() as conn:
        yield cast(DictConnection, conn)


def get_db() -> Generator[DictConnection, None, None]:
    with connection_scope() as conn:
        yield conn


def dispose_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = None
