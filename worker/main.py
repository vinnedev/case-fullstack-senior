import os
import signal
import time
from contextlib import suppress
from threading import Event

import psycopg
from prometheus_client import start_http_server

from modules.jobs.listener import JOBS_CHANNEL, wait_for_wakeup
from modules.jobs.processor import process_next, seconds_until_next_attempt
from shared.config.settings import get_settings
from shared.db.pool import connection_scope, dispose_pool
from shared.logging.wide_event import WideEvent
from shared.observability.worker_metrics import worker_wakeups_total

SERVICE = "worker"
POLL_FALLBACK_S = 30.0
WAIT_SLICE_S = 1.0

shutdown = Event()


def _request_shutdown(_signum: int, _frame: object) -> None:
    # Assinatura exigida por signal.signal; só sinaliza — o job em andamento
    # termina e o loop sai antes do próximo claim.
    shutdown.set()


def drain() -> None:
    while not shutdown.is_set():
        with connection_scope() as conn:
            if not process_next(conn, heartbeat_factory=connection_scope):
                return


def _connect_listener() -> psycopg.Connection:
    listen_conn = psycopg.connect(get_settings().database_url, autocommit=True)
    listen_conn.execute(f"LISTEN {JOBS_CHANNEL}")
    return listen_conn


def _reconnect_listener() -> psycopg.Connection | None:
    while not shutdown.is_set():
        try:
            return _connect_listener()
        except psycopg.OperationalError:
            time.sleep(WAIT_SLICE_S)
    return None


def _next_poll_timeout() -> float:
    # O NOTIFY do retry chega antes do backoff vencer; encurtar a espera até o
    # próximo next_attempt_at evita que o job aguarde o fallback inteiro (30s).
    with connection_scope() as conn:
        pending = seconds_until_next_attempt(conn)
    if pending is None:
        return POLL_FALLBACK_S
    return min(POLL_FALLBACK_S, pending + 0.05)


def _wait_for_work(listen_conn: psycopg.Connection, timeout: float) -> bool:
    # Fatias curtas para reagir ao SIGTERM sem esperar o fallback inteiro.
    deadline = time.monotonic() + timeout
    while not shutdown.is_set():
        remaining = min(WAIT_SLICE_S, deadline - time.monotonic())
        if remaining <= 0:
            return False
        if wait_for_wakeup(listen_conn, remaining):
            return True
    return False


def main() -> None:
    WideEvent(SERVICE, "startup").emit()
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    start_http_server(int(os.environ.get("METRICS_PORT", "9100")))
    listen_conn = _connect_listener()
    drain()  # backlog acumulado enquanto o worker esteve fora
    while not shutdown.is_set():
        try:
            woke_by_notify = _wait_for_work(listen_conn, _next_poll_timeout())
        except psycopg.OperationalError as exc:
            if shutdown.is_set():
                break
            WideEvent(SERVICE, "listener_reconnect").error(exc).emit()
            with suppress(Exception):
                listen_conn.close()
            reconnected = _reconnect_listener()
            if reconnected is None:
                break
            listen_conn = reconnected
            woke_by_notify = False
        if shutdown.is_set():
            break
        worker_wakeups_total.labels(reason="notify" if woke_by_notify else "timeout").inc()
        drain()
    listen_conn.close()
    dispose_pool()
    WideEvent(SERVICE, "shutdown").emit()


if __name__ == "__main__":
    main()
