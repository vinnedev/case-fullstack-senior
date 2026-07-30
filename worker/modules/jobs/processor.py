import time
import uuid
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from threading import Event, Thread
from typing import Any

from modules.jobs.listener import wait_for_wakeup
from shared.config.settings import get_settings
from shared.db.pool import DictConnection
from shared.logging.wide_event import start_event
from shared.observability.worker_metrics import (
    job_processing_duration_seconds,
    job_queue_wait_seconds,
    jobs_processed_total,
)

SERVICE = "worker"
MAX_ATTEMPTS = 3
LEASE_TIMEOUT_S = 30.0
HEARTBEAT_INTERVAL_S = LEASE_TIMEOUT_S / 3
WORKER_ID = str(uuid.uuid4())
SYSTEM_ACTOR = "system:worker"
CANCELLATIONS_CHANNEL = "jobs_cancelled"
type ConnectionFactory = Callable[[], AbstractContextManager[DictConnection]]


class JobCancelledError(Exception):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelledError


@contextmanager
def cancellation_monitor(
    job: dict[str, Any],
    factory: ConnectionFactory | None,
    interval_s: float = 0.1,
) -> Generator[CancellationToken, None, None]:
    token = CancellationToken()
    if factory is None:
        yield token
        return

    stopped = Event()

    def monitor() -> None:
        with factory() as monitor_conn:
            monitor_conn.execute(f"LISTEN {CANCELLATIONS_CHANNEL}")
            monitor_conn.commit()
            # O SELECT posterior ao LISTEN cobre um cancelamento entre a abertura
            # da conexão e a ativação efetiva da assinatura.
            row = monitor_conn.execute("SELECT status FROM jobs WHERE id = %s", (job["id"],)).fetchone()
            monitor_conn.commit()
            if row is not None and row["status"] == "cancelled":
                token.cancel()
                return
            while not stopped.is_set():
                if wait_for_wakeup(monitor_conn, timeout=interval_s):
                    token.cancel()
                    return

    thread = Thread(target=monitor, name=f"job-{job['id']}-cancellation", daemon=True)
    thread.start()
    try:
        yield token
    finally:
        stopped.set()
        thread.join(timeout=interval_s + 1)


def _renew_lease(factory: ConnectionFactory, job: dict[str, Any]) -> None:
    with factory() as heartbeat_conn, heartbeat_conn.transaction():
        renewed = heartbeat_conn.execute(
            """
            UPDATE jobs SET locked_at = now()
            WHERE id = %s AND status = 'running' AND worker_id = %s
            RETURNING id
            """,
            (job["id"], job["worker_id"]),
        ).fetchone()
        if renewed is None:
            raise RuntimeError(f"lease do job {job['id']} não pertence mais a este worker")


@contextmanager
def lease_heartbeat(
    job: dict[str, Any],
    factory: ConnectionFactory | None,
    interval_s: float = HEARTBEAT_INTERVAL_S,
) -> Generator[None, None, None]:
    if factory is None:
        yield
        return

    stopped = Event()
    errors: list[Exception] = []

    def heartbeat_loop() -> None:
        while not stopped.wait(interval_s):
            try:
                _renew_lease(factory, job)
            except Exception as exc:
                errors.append(exc)
                return

    heartbeat = Thread(target=heartbeat_loop, name=f"job-{job['id']}-heartbeat", daemon=True)
    heartbeat.start()
    body_succeeded = False
    try:
        yield
        body_succeeded = True
    finally:
        stopped.set()
        heartbeat.join()
    if body_succeeded and errors:
        raise RuntimeError(f"heartbeat do job {job['id']} falhou") from errors[0]


def recover_stale_jobs(conn: DictConnection, lease_timeout_s: float = LEASE_TIMEOUT_S) -> int:
    """Transforma claims abandonados em falhas recuperáveis ou DLQ."""
    error = "WorkerLeaseExpired: worker parou antes de finalizar o job"
    with conn.transaction():
        stale = conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                last_error = %s,
                next_attempt_at = NULL,
                locked_at = NULL,
                worker_id = NULL,
                updated_at = now()
            WHERE status = 'running'
              AND (
                locked_at IS NULL
                OR locked_at < now() - make_interval(secs => %s)
              )
            RETURNING id, company_id, kind, trace_id, attempts
            """,
            (error, lease_timeout_s),
        ).fetchall()
        for job in stale:
            _record_audit_event(conn, job, "failed")
            if job["attempts"] >= MAX_ATTEMPTS:
                _send_to_dlq(conn, job, error)
    return len(stale)


def _claim_next(conn: DictConnection) -> dict[str, Any] | None:
    with conn.transaction():
        job = conn.execute(
            """
            SELECT id, company_id, kind, trace_id, attempts,
                   extract(epoch FROM now() - updated_at) AS queue_wait_s
            FROM jobs
            WHERE status = 'queued'
              AND attempts < %s
              AND (next_attempt_at IS NULL OR next_attempt_at <= now())
            ORDER BY id LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            (MAX_ATTEMPTS,),
        ).fetchone()
        if job is None:
            return None
        job_queue_wait_seconds.observe(max(float(job["queue_wait_s"] or 0), 0.0))
        row = conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                locked_at = now(),
                worker_id = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING attempts, locked_at, worker_id
            """,
            (WORKER_ID, job["id"]),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"job {job['id']} sumiu durante o claim")
        job.update(row)
        return job


def _finalize(conn: DictConnection, job: dict[str, Any]) -> str:
    with conn.transaction():
        done = conn.execute(
            """
            UPDATE jobs
            SET status = 'done', locked_at = NULL, worker_id = NULL, updated_at = now()
            WHERE id = %s AND status = 'running' AND worker_id = %s
            RETURNING id
            """,
            (job["id"], job["worker_id"]),
        ).fetchone()
        if done is None:
            return "cancelled"
        inserted = conn.execute(
            "INSERT INTO job_results (job_id, payload) VALUES (%s, %s) ON CONFLICT (job_id) DO NOTHING",
            (job["id"], f"resultado sensível da empresa {job['company_id']}"),
        ).rowcount
        _record_audit_event(conn, job, "completed")
        if inserted:
            # a linha da company é o ponto quente do tenant: todos os finalizes
            # serializam nela, então o lock precisa ser o último antes do commit
            conn.execute("UPDATE companies SET job_quota = job_quota - 1 WHERE id = %s", (job["company_id"],))
        return "done"


def _acknowledge_cancellation(conn: DictConnection, job: dict[str, Any]) -> None:
    with conn.transaction():
        conn.execute(
            """
            UPDATE jobs
            SET locked_at = NULL, worker_id = NULL, updated_at = now()
            WHERE id = %s AND status = 'cancelled' AND worker_id = %s
            """,
            (job["id"], job["worker_id"]),
        )


def _send_to_dlq(conn: DictConnection, job: dict[str, Any], error: str) -> None:
    conn.execute(
        """
        INSERT INTO dead_letter_jobs (job_id, company_id, kind, attempts, last_error, trace_id, job_created_at)
        SELECT id, company_id, kind, attempts, %s, trace_id, created_at FROM jobs WHERE id = %s
        ON CONFLICT (job_id) WHERE job_id IS NOT NULL DO NOTHING
        """,
        (error, job["id"]),
    )


def _record_audit_event(conn: DictConnection, job: dict[str, Any], event_type: str) -> None:
    conn.execute(
        """
        INSERT INTO job_audit_events (job_id, company_id, event_type, actor, trace_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (job["id"], job["company_id"], event_type, SYSTEM_ACTOR, job["trace_id"]),
    )


def _record_failure(conn: DictConnection, job: dict[str, Any], exc: Exception) -> tuple[str, float | None]:
    error = f"{type(exc).__name__}: {exc}"
    exhausted = job["attempts"] >= MAX_ATTEMPTS
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                last_error = %s,
                next_attempt_at = NULL,
                locked_at = NULL,
                worker_id = NULL,
                updated_at = now()
            WHERE id = %s AND status = 'running' AND worker_id = %s
            RETURNING status
            """,
            (error, job["id"], job["worker_id"]),
        ).fetchone()
        if row is None:
            return "cancelled", None
        _record_audit_event(conn, job, "failed")
        if exhausted:
            _send_to_dlq(conn, job, error)
    return row["status"], None


def _execute(conn: DictConnection, job: dict[str, Any]) -> None:
    token = job["cancel_token"]
    work_s = get_settings().job_simulated_work_s
    if work_s > 0:
        for _ in range(10):
            token.raise_if_cancelled()
            time.sleep(work_s / 10)
    token.raise_if_cancelled()
    company = conn.execute("SELECT id FROM companies WHERE id = %s", (job["company_id"],)).fetchone()
    if company is None:
        raise RuntimeError(f"company {job['company_id']} não encontrada para o job {job['id']}")


def process_next(
    conn: DictConnection,
    heartbeat_factory: ConnectionFactory | None = None,
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
) -> bool:
    recover_stale_jobs(conn)
    job = _claim_next(conn)
    if job is None:
        return False
    started = time.perf_counter()
    outcome = "failed"
    event = start_event(
        SERVICE,
        "job_processed",
        job_id=job["id"],
        company_id=job["company_id"],
        job_kind=job["kind"],
        trace_id=job["trace_id"],
        attempts=job["attempts"],
    )
    try:
        with cancellation_monitor(job, heartbeat_factory) as cancel_token:
            job["cancel_token"] = cancel_token
            with lease_heartbeat(job, heartbeat_factory, heartbeat_interval_s):
                _execute(conn, job)
        outcome = _finalize(conn, job)
        event.add(outcome=outcome)
    except JobCancelledError:
        conn.rollback()
        _acknowledge_cancellation(conn, job)
        outcome = "cancelled"
        event.add(outcome=outcome)
    except Exception as exc:
        conn.rollback()
        status, retry_in = _record_failure(conn, job, exc)
        event.error(exc, outcome="failed", job_status=status)
        if status == "failed" and job["attempts"] >= MAX_ATTEMPTS:
            event.add(dead_lettered=True)
        if retry_in is not None:
            event.add(retry_in_s=round(retry_in, 1))
    finally:
        jobs_processed_total.labels(outcome=outcome).inc()
        job_processing_duration_seconds.labels(outcome=outcome).observe(time.perf_counter() - started)
        event.emit()
    return True
