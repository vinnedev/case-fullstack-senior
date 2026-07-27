import os
import threading

import psycopg

_lock = threading.Lock()
_active_conns: set[psycopg.Connection] = set()


class _TrackedConnection:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._conn.__exit__(exc_type, exc, tb)
        finally:
            with _lock:
                _active_conns.discard(self._conn)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_conn():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    with _lock:
        _active_conns.add(conn)
    return _TrackedConnection(conn)


def close_all_connections():
    with _lock:
        conns = list(_active_conns)
        _active_conns.clear()
    for conn in conns:
        try:
            conn.close()
        except Exception:
            pass
