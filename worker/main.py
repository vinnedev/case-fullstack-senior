import os

import psycopg
from prometheus_client import start_http_server

from modules.jobs.listener import JOBS_CHANNEL, wait_for_wakeup
from modules.jobs.processor import process_next
from shared.config.settings import get_settings
from shared.db.pool import connection_scope
from shared.logging.wide_event import WideEvent
from shared.observability.worker_metrics import worker_wakeups_total

SERVICE = "worker"
POLL_FALLBACK_S = 30.0


def drain() -> None:
    while True:
        with connection_scope() as conn:
            if not process_next(conn, heartbeat_factory=connection_scope):
                return


def main() -> None:
    WideEvent(SERVICE, "startup").emit()
    start_http_server(int(os.environ.get("METRICS_PORT", "9100")))
    listen_conn = psycopg.connect(get_settings().database_url, autocommit=True)
    listen_conn.execute(f"LISTEN {JOBS_CHANNEL}")
    drain()  # backlog acumulado enquanto o worker esteve fora
    while True:
        woke_by_notify = wait_for_wakeup(listen_conn, POLL_FALLBACK_S)
        worker_wakeups_total.labels(reason="notify" if woke_by_notify else "timeout").inc()
        drain()


if __name__ == "__main__":
    main()
