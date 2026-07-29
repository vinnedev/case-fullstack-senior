import psycopg

JOBS_CHANNEL = "jobs_queued"


def wait_for_wakeup(listen_conn: psycopg.Connection, timeout: float) -> bool:
    # NOTIFY é sinal de "chegou trabalho"; o timeout é o fallback caso um
    # notify se perca (não há garantia de entrega sem listener conectado).
    for _ in listen_conn.notifies(timeout=timeout, stop_after=1):
        return True
    return False
